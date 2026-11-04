"""Finds better tags for a song: title, all artists, album, numbers, genre, label, ISRC and a good cover.

YouTube Music is what a download starts from. Deezer's public API fills in what YouTube Music doesn't have and needs
no key. The iTunes Search API is an optional source of large covers. A failing source never fails a download; there
are just fewer suggestions.
"""
import asyncio
import difflib
import logging
import re
import time
import unicodedata
from urllib.parse import urlparse

import httpx

from . import config, settings_store

log = logging.getLogger("amplicon.metadata")

DEEZER = "https://api.deezer.com"
ITUNES = "https://itunes.apple.com"
# From this score on a match is taken over without asking; below it stays a choice in the review.
CONFIDENT = 0.82
# Covers are only ever fetched from these, so a client can't make the server open any address.
COVER_HOSTS = ("googleusercontent.com", "ytimg.com", "ggpht.com", "dzcdn.net", "mzstatic.com")

NOISE = re.compile(
    r"[(\[][^()\[\]]*\b(official|video|lyrics?|audio|visuali[sz]er|hd|hq|4k|music video|mv|m/v|clip)\b[^()\[\]]*[)\]]", re.I)
# "with" only counts in brackets: "With or Without You" and "Dancing with Myself" are titles.
FEAT = re.compile(r"\s*[(\[]\s*(?:feat|ft|featuring|with)\b\.?\s+([^()\[\]]*)[)\]]|\s+(?:feat|ft|featuring)\b\.?\s+([^()\[\]]*)", re.I)
VERSION = re.compile(r"\b(remix|mashup|cover|live|acoustic|instrumental|karaoke|edit|bootleg|flip|vip|sped up|slowed|"
                     r"nightcore|demo|remaster(?:ed)?)\b", re.I)


class Throttle:
    """At most one call per `gap` seconds."""

    def __init__(self, gap: float) -> None:
        self.gap = gap
        self.next = 0.0
        self.lock: asyncio.Lock | None = None

    async def wait(self) -> None:
        if self.lock is None:
            self.lock = asyncio.Lock()
        async with self.lock:
            now = time.monotonic()
            if now < self.next:
                await asyncio.sleep(self.next - now)
            self.next = max(now, self.next) + self.gap


_deezer_gate = Throttle(0.12)   # Deezer allows about 10 calls a second
_itunes_gate = Throttle(3.1)    # Apple allows about 20 calls a minute
_albums: dict[int, dict] = {}
_covers: dict[tuple, str | None] = {}


async def _get_json(url: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": f"Amplicon/{config.VERSION}"}) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        return r.json()


async def _deezer(path: str, params: dict | None = None) -> dict:
    for attempt in range(3):
        await _deezer_gate.wait()
        data = await _get_json(f"{DEEZER}/{path}", params)
        err = data.get("error") if isinstance(data, dict) else None
        if not err:
            return data
        if err.get("code") == 4 and attempt < 2:  # quota: answers with 200 and an error
            await asyncio.sleep(2)
            continue
        raise ValueError(err.get("message") or "Deezer error")
    return {}


# ---------------------------------------------------------------- comparing

def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode() or (s or "")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.lower().replace("&", " and "))).strip()


def clean_title(title: str | None) -> str:
    """'Song (Official Video) [HD] feat. X' → 'Song'. Versions like (Name Remix) stay."""
    t = NOISE.sub(" ", title or "")
    t = FEAT.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip(" -–—")


def split_featured(title: str, artist: str) -> tuple[str, str]:
    """'Song (feat. A and B)' by 'C' → 'Song' by 'C, A, B': the title stays clean, nobody gets lost."""
    names = [n for m in FEAT.finditer(title or "") for n in re.split(r",|&|\s+and\s+", m.group(1) or m.group(2) or "") if n.strip()]
    known = {norm(a) for a in artists_of(artist)}
    extra = [n.strip() for n in names if norm(n) not in known]
    clean = re.sub(r"\s+", " ", FEAT.sub(" ", title or "")).strip(" -–—")
    return clean or title, ", ".join([*([artist] if artist else []), *extra])


def artists_of(text: str | None) -> list[str]:
    parts = re.split(r",|;|&|\s+x\s+|\s+feat\.?\s+|\s+ft\.?\s+|\s+with\s+", text or "", flags=re.I)
    return [re.sub(r"\s*-\s*Topic$|VEVO$", "", p.strip(), flags=re.I).strip() for p in parts if p.strip()]


def versions(title: str | None) -> set[str]:
    return {m.lower() for m in VERSION.findall(title or "")}


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def score(song: dict, cand: dict) -> float:
    title = _ratio(norm(clean_title(song.get("title"))), norm(clean_title(cand.get("title"))))
    mine = [norm(a) for a in artists_of(song.get("artist"))]
    theirs = [norm(a) for a in artists_of(cand.get("artist"))]
    artist = max((_ratio(a, b) for a in mine for b in theirs), default=0.0)
    if not mine:  # the title may hold it: "Artist - Song"
        artist = 0.6 if any(t and t in norm(song.get("title")) for t in theirs) else 0.3
    total = title * 0.55 + artist * 0.3
    da, db = song.get("duration") or 0, cand.get("duration") or 0
    if da and db:
        diff = abs(da - db)
        total += 0.15 if diff <= 3 else 0.08 if diff <= 10 else 0
    else:
        total += 0.08
    if versions(song.get("title")) != versions(cand.get("title")):
        total -= 0.25  # a remix is another recording, however close the name is
    return round(max(0.0, min(1.0, total)), 3)


# ---------------------------------------------------------------- Deezer

def _date_year(value: str | None) -> str | None:
    return value if value and value != "0000-00-00" else None


def _candidate(track: dict, album: dict | None = None, full: dict | None = None) -> dict:
    album = album or {}
    full = full or {}
    short = track.get("album") or {}
    contributors = [c.get("name") for c in full.get("contributors") or [] if c.get("name")]
    artist = ", ".join(dict.fromkeys(contributors)) or (track.get("artist") or {}).get("name") or ""
    genres = [g.get("name") for g in (album.get("genres") or {}).get("data") or [] if g.get("name")]
    title, artist = split_featured(track.get("title") or "", artist)
    return {
        "source": "deezer", "id": str(track.get("id") or ""), "link": track.get("link"),
        "title": title, "artist": artist,
        "albumartist": (album.get("artist") or {}).get("name") or (track.get("artist") or {}).get("name") or "",
        "album": album.get("title") or short.get("title") or "",
        "track": full.get("track_position") or track.get("track_position"),
        "track_total": album.get("nb_tracks"),
        "disc": full.get("disk_number") or track.get("disk_number"),
        "disc_total": max((t.get("disk_number") or 1 for t in (album.get("tracks") or {}).get("data") or []), default=None),
        "year": _date_year(album.get("release_date") or full.get("release_date")),
        "genre": ", ".join(genres) or None, "label": album.get("label") or None,
        "isrc": full.get("isrc") or track.get("isrc") or None,
        "cover": album.get("cover_xl") or short.get("cover_xl") or None, "duration": track.get("duration"),
    }


async def _album(album_id: int) -> dict:
    if album_id in _albums:
        return _albums[album_id]
    a = await _deezer(f"album/{album_id}")
    try:  # the album's own track list is the one with disc numbers
        a["tracks"] = await _deezer(f"album/{album_id}/tracks", {"limit": 300})
    except (httpx.HTTPError, ValueError):
        pass
    _albums[album_id] = a
    return a


async def _full(c: dict, track: dict) -> dict:
    """Adds what the search result doesn't have: all artists, numbers, genre, label."""
    try:
        full = await _deezer(f"track/{track['id']}")
        album = await _album((track.get("album") or {}).get("id")) if (track.get("album") or {}).get("id") else {}
        return {**_candidate(track, album, full), "score": c.get("score")}
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.info("deezer details of %s: %s", track.get("id"), e)
        return c


async def lookup(song: dict, details: int = 3, query: str | None = None) -> list[dict]:
    """Candidates for a song, best first. `details` of them get the full data (two more calls each, albums cached)."""
    if not settings_store.get("metadata_deezer"):
        return []
    title = clean_title(song.get("title"))
    artist = ", ".join(artists_of(song.get("artist"))[:1])
    q = (query or f"{artist} {title}").strip()
    if not q:
        return []
    try:
        data = await _deezer("search", {"q": q, "limit": 10})
        found = data.get("data") or []
        if not found and not query and artist:
            found = (await _deezer("search", {"q": title, "limit": 10})).get("data") or []
    except (httpx.HTTPError, ValueError) as e:
        log.info("deezer search %r: %s", q, e)
        return []
    pairs = []
    for t in found:
        c = _candidate(t)
        c["score"] = score(song, c)
        pairs.append((c, t))
    # The same recording is on many albums: the one the file already names goes first.
    mine = norm(clean_title(song.get("album")))

    def names_album(c: dict) -> bool:  # only a real match counts: the hint may be a folder called "Misc"
        return bool(mine) and _ratio(mine, norm(clean_title(c.get("album")))) >= 0.85

    pairs.sort(key=lambda p: (p[0]["score"], names_album(p[0])), reverse=True)
    pairs = pairs[:5]
    head = await asyncio.gather(*(_full(c, t) for c, t in pairs[:details]))
    return [*head, *(c for c, _ in pairs[details:])]


async def lookup_album(artist: str, title: str, songs: list[dict]) -> list[dict]:
    """Albums that fit, best first, each with its tracks laid over `songs` (by position, then by name)."""
    if not settings_store.get("metadata_deezer"):
        return []
    try:
        found = (await _deezer("search/album", {"q": f"{artists_of(artist)[0] if artists_of(artist) else ''} {clean_title(title)}".strip(),
                                                "limit": 8})).get("data") or []
    except (httpx.HTTPError, ValueError) as e:
        log.info("deezer album search %r: %s", title, e)
        return []
    ranked = []
    for a in found:
        name = _ratio(norm(clean_title(title)), norm(clean_title(a.get("title"))))
        who = max((_ratio(norm(x), norm((a.get("artist") or {}).get("name"))) for x in artists_of(artist)), default=0.0)
        size = 1 - min(abs((a.get("nb_tracks") or 0) - len(songs)), 10) / 10
        ranked.append((round(name * 0.5 + who * 0.3 + size * 0.2, 3), a))
    ranked.sort(key=lambda r: r[0], reverse=True)
    out = []
    for s, a in ranked[:3]:
        try:
            full = await _album(a["id"])
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.info("deezer album %s: %s", a.get("id"), e)
            continue
        tracks = (full.get("tracks") or {}).get("data") or []
        matched: dict[int, dict] = {}
        for i, song in enumerate(songs):
            t = tracks[i] if len(tracks) == len(songs) else None
            if t is None or score(song, _candidate(t)) < 0.5:
                t = max(tracks, key=lambda x: score(song, _candidate(x)), default=None)
                if t is None or score(song, _candidate(t)) < 0.6:
                    continue
            c = _candidate(t, full, t)
            c["track"] = t.get("track_position") or c.get("track")
            matched[i] = c
        if not matched:
            continue  # a similar name, but none of these songs: no use as a choice
        out.append({"source": "deezer", "id": str(a["id"]), "title": full.get("title") or "",
                    "artist": (full.get("artist") or {}).get("name") or "", "year": _date_year(full.get("release_date")),
                    "genre": ", ".join(g["name"] for g in (full.get("genres") or {}).get("data") or []) or None,
                    "label": full.get("label"), "cover": full.get("cover_xl"), "track_count": full.get("nb_tracks"),
                    "score": s, "tracks": matched, "link": full.get("link")})
    return out


# ---------------------------------------------------------------- covers

async def itunes_cover(artist: str, album: str) -> str | None:
    """A large square cover from Apple. Asked once per album, because Apple only allows about 20 calls a minute."""
    if not settings_store.get("metadata_itunes_covers") or not album:
        return None
    key = (norm(artist), norm(album))
    if key in _covers:
        return _covers[key]
    url = None
    try:
        await _itunes_gate.wait()
        data = await _get_json(f"{ITUNES}/search", {"term": f"{artist} {album}", "entity": "album", "limit": 5})
        best = max(data.get("results") or [], default=None, key=lambda r: _ratio(norm(album), norm(r.get("collectionName"))))
        if best and _ratio(norm(album), norm(best.get("collectionName"))) >= 0.7 and best.get("artworkUrl100"):
            url = best["artworkUrl100"].replace("100x100bb", "1400x1400bb")
    except (httpx.HTTPError, ValueError) as e:
        log.info("itunes cover %r: %s", album, e)
    _covers[key] = url
    return url


def cover_allowed(url: str | None) -> bool:
    try:
        u = urlparse(url or "")
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in COVER_HOSTS)


def hires(url: str | None) -> str | None:
    """YouTube Music art can be asked for in any size; the app shows small ones, a file deserves a big one."""
    if url and "googleusercontent.com" in url:
        return re.sub(r"=w\d+-h\d+.*$", "=w1400-h1400-l90-rj", url)
    return url


def apply(base: dict, cand: dict) -> dict:
    """A song's data with a candidate laid over it. Empty values of the candidate never erase anything."""
    out = dict(base)
    for k in ("title", "artist", "albumartist", "album", "track", "track_total", "disc", "disc_total", "year", "genre",
              "label", "isrc"):
        if cand.get(k) not in (None, "", 0):
            out[k] = cand[k]
    if cand.get("cover"):
        out["cover"] = cand["cover"]
    return out
