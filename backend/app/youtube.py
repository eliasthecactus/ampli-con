"""YouTube Music: the signed-in account (cookies), search, albums, playlists, artists and playlist edits.

Without an account everything public works (search, albums, public playlists). The account adds its own
library playlists, editing them, and videos that need a sign-in.
"""
import asyncio
import logging
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from typing import Any

from . import config, crypto, db
from .events import bus

log = logging.getLogger("amplicon.youtube")

AUTH_COOKIES = {"SAPISID", "__Secure-3PSID", "__Secure-1PSID", "SID", "LOGIN_INFO", "__Secure-3PAPISID"}
LIKED = "LM"


class YouTubeError(Exception):
    """Something YouTube refused, with a message for people."""


class NotSignedIn(YouTubeError):
    pass


# ---------------------------------------------------------------- cookies

def parse_cookies(text: str) -> dict:
    """Accepts a Netscape cookies.txt or a Cookie header copied from the browser. Returns the cookies.txt text."""
    text = text.replace("\r\n", "\n").strip()
    if not text:
        raise ValueError("Paste the cookies first")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if any(len(ln.split("\t")) == 7 for ln in lines):
        out, names, expiry = [], set(), []
        for line in lines:
            if line.startswith("#") and not line.startswith("#HttpOnly_"):
                continue
            parts = line.split("\t")
            if len(parts) != 7:
                raise ValueError("That isn't a complete cookies.txt (every cookie line needs 7 tab-separated fields)")
            domain = parts[0].removeprefix("#HttpOnly_")
            if "youtube.com" in domain or "google.com" in domain:
                names.add(parts[5])
                try:
                    if int(parts[4]) > 0:
                        expiry.append(int(parts[4]))
                except ValueError:
                    pass
            out.append(line)
        if not names:
            raise ValueError("No youtube.com cookies in there. Export them while music.youtube.com is open.")
        cookies = "# Netscape HTTP Cookie File\n" + "\n".join(out) + "\n"
    else:
        header = re.sub(r"^\s*cookie\s*:\s*", "", " ".join(lines), flags=re.I)
        pairs = [p.strip().split("=", 1) for p in header.split(";") if "=" in p]
        if not pairs:
            raise ValueError("That's neither a cookies.txt nor a Cookie header")
        names = {k for k, _ in pairs}
        far = int(time.time()) + 180 * 86400
        cookies = "# Netscape HTTP Cookie File\n" + "".join(
            f".youtube.com\tTRUE\t/\tTRUE\t{far}\t{k}\t{v}\n" for k, v in pairs)
        expiry = []
    if not names & AUTH_COOKIES:
        raise ValueError("These cookies have no sign-in. Copy them while you're signed in to YouTube.")
    return {"text": cookies, "expires_at": min(expiry) if expiry else None}


def cookie_header(cookies_txt: str) -> str:
    pairs = {}
    for line in cookies_txt.splitlines():
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        parts = line.split("\t")
        if len(parts) == 7 and parts[0].removeprefix("#HttpOnly_").endswith("youtube.com"):
            pairs[parts[5]] = parts[6]
    if "__Secure-3PAPISID" not in pairs and "SAPISID" in pairs:
        pairs["__Secure-3PAPISID"] = pairs["SAPISID"]
    if "__Secure-3PAPISID" not in pairs:
        raise NotSignedIn("The cookies have no SAPISID, copy them again while signed in")
    return "; ".join(f"{k}={v}" for k, v in pairs.items())


def account() -> dict | None:
    return db.row("SELECT * FROM youtube_account WHERE id = 1")


def public_account() -> dict | None:
    a = account()
    if not a:
        return None
    return {k: a[k] for k in ("name", "handle", "photo", "status", "error", "checked_at", "updated_at")}


@asynccontextmanager
async def cookies_file(use: bool = True):
    """The account's cookies as a private temp file for yt-dlp; rotated cookies are saved back."""
    a = account() if use else None
    if not a:
        yield None
        return
    text = crypto.decrypt(a["cookies_enc"])
    config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = config.TMP_DIR / f"cookies-{secrets.token_hex(6)}.txt"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    try:
        yield str(path)
    finally:
        try:
            new = path.read_text()
            if new.strip() and new != text:
                try:
                    parse_cookies(new)
                    db.execute("UPDATE youtube_account SET cookies_enc = ? WHERE id = 1", [crypto.encrypt(new)])
                except ValueError:
                    pass
        finally:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------- ytmusicapi

_lock = threading.Lock()
_clients: dict[str, Any] = {}


def _make(authenticated: bool):
    from ytmusicapi import YTMusic
    if not authenticated:
        return YTMusic()
    a = account()
    if not a:
        raise NotSignedIn("No account is connected. Connect one in Amplicon's web interface.")
    headers = {
        "cookie": cookie_header(crypto.decrypt(a["cookies_enc"])),
        "authorization": "SAPISIDHASH 0",
        "x-goog-authuser": "0",
        "origin": "https://music.youtube.com",
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
    }
    return YTMusic(auth=headers)


def client(authenticated: bool | None = None):
    """The signed-in client when an account is connected (or required), otherwise an anonymous one."""
    if authenticated is None:
        authenticated = account() is not None
    key = "auth" if authenticated else "anon"
    with _lock:
        if key not in _clients:
            _clients[key] = _make(authenticated)
        return _clients[key]


def reset_clients() -> None:
    with _lock:
        _clients.clear()


async def call(fn, authenticated: bool | None = None):
    def run():
        try:
            return fn(client(authenticated))
        except YouTubeError:
            raise
        except Exception as e:
            msg = str(e)
            if any(x in msg for x in ("401", "403", "Sign in", "sign in")):
                raise YouTubeError(f"The request was refused: {msg[:200]}") from e
            raise YouTubeError(msg[:300]) from e
    return await asyncio.to_thread(run)


async def connect(cookies_text: str) -> dict:
    parsed = parse_cookies(cookies_text)
    now = time.time()
    db.execute("INSERT INTO youtube_account (id, cookies_enc, status, updated_at) VALUES (1, ?, 'unknown', ?) "
               "ON CONFLICT(id) DO UPDATE SET cookies_enc = excluded.cookies_enc, status = 'unknown', error = NULL, "
               "updated_at = excluded.updated_at", [crypto.encrypt(parsed["text"]), now])
    reset_clients()
    result = await check()
    if result["status"] != "valid":
        raise ValueError(result.get("error") or "YouTube didn't accept these cookies")
    return result


def disconnect() -> None:
    db.execute("DELETE FROM youtube_account")
    reset_clients()
    bus.publish("account", {"connected": False})


async def check() -> dict:
    if not account():
        return {"status": "none"}
    try:
        info = await call(lambda yt: yt.get_account_info(), authenticated=True)
        fields = {"name": info.get("accountName"), "handle": info.get("channelHandle"),
                  "photo": info.get("accountPhotoUrl"), "status": "valid", "error": None}
    except YouTubeError as e:
        fields = {"status": "expired", "error": str(e)}
    fields["checked_at"] = time.time()
    db.update("youtube_account", 1, fields)
    bus.publish("account", {"connected": True, **(public_account() or {})})
    return public_account() or {"status": "none"}


# ---------------------------------------------------------------- mapping

def _thumb(item: dict) -> str | None:
    thumbs = item.get("thumbnails") or item.get("thumbnail") or []
    if isinstance(thumbs, dict):
        thumbs = thumbs.get("thumbnails") or []
    if not thumbs:
        return None
    best = max(thumbs, key=lambda t: (t.get("width") or 0))
    url = best.get("url")
    # Album art from googleusercontent can be resized; ask for a big square one, the app resizes again.
    if url and "googleusercontent.com" in url:
        url = re.sub(r"=w\d+-h\d+.*$", "=w544-h544-l90-rj", url)
    return url


def _artists(item: dict) -> tuple[str, str | None]:
    artists = [a for a in (item.get("artists") or []) if a.get("name")]
    if not artists and item.get("artist"):
        return str(item["artist"]), item.get("browseId")
    return ", ".join(a["name"] for a in artists), next((a.get("id") for a in artists if a.get("id")), None)


def _seconds(item: dict) -> int:
    if item.get("duration_seconds"):
        return int(item["duration_seconds"])
    text = item.get("duration") or item.get("length") or ""
    total = 0
    for part in str(text).split(":"):
        if part.isdigit():
            total = total * 60 + int(part)
    return total


def song(item: dict, album: dict | None = None) -> dict | None:
    vid = item.get("videoId")
    if not vid:
        return None
    artist, artist_id = _artists(item)
    alb = item.get("album") if isinstance(item.get("album"), dict) else None
    return {
        "id": vid,
        "title": item.get("title") or "",
        "artist": artist or (album or {}).get("artist") or "",
        "artist_id": artist_id,
        "album": (alb or {}).get("name") or (album or {}).get("title"),
        "album_id": (alb or {}).get("id") or (album or {}).get("id"),
        "duration": _seconds(item),
        "thumbnail": _thumb(item) or (album or {}).get("thumbnail"),
        "track": item.get("trackNumber"),
        "set_video_id": item.get("setVideoId"),
        "available": item.get("isAvailable", True) is not False,
        "is_video": item.get("videoType") not in (None, "MUSIC_VIDEO_TYPE_ATV"),
    }


def album_summary(item: dict) -> dict | None:
    bid = item.get("browseId")
    if not bid:
        return None
    artist, artist_id = _artists(item)
    return {"id": bid, "title": item.get("title") or "", "artist": artist, "artist_id": artist_id,
            "year": str(item.get("year") or "") or None, "type": item.get("type") or "Album", "thumbnail": _thumb(item)}


def playlist_summary(item: dict) -> dict | None:
    pid = item.get("playlistId") or item.get("browseId")
    if not pid:
        return None
    pid = pid.removeprefix("VL")
    author = item.get("author")
    if isinstance(author, list):
        author = ", ".join(a.get("name", "") for a in author if isinstance(a, dict))
    elif isinstance(author, dict):
        author = author.get("name")
    count = item.get("itemCount") or item.get("count")
    try:
        count = int(re.sub(r"\D", "", str(count))) if count else None
    except ValueError:
        count = None
    return {"id": pid, "title": item.get("title") or "", "author": author, "thumbnail": _thumb(item), "track_count": count}


def artist_summary(item: dict) -> dict | None:
    bid = item.get("browseId")
    if not bid:
        return None
    return {"id": bid, "name": item.get("artist") or item.get("title") or "", "thumbnail": _thumb(item)}


# ---------------------------------------------------------------- browse

SEARCH_FILTERS = {"songs": "songs", "videos": "videos", "albums": "albums", "playlists": "playlists", "artists": "artists"}


def _sort_results(results: list[dict], out: dict) -> None:
    for r in results:
        t = r.get("resultType")
        if t in ("song", "video"):
            if s := song(r):
                out["songs"].append(s)
        elif t in ("album", "single", "ep"):
            if a := album_summary(r):
                out["albums"].append(a)
        elif t == "playlist":
            if p := playlist_summary(r):
                out["playlists"].append(p)
        elif t == "artist":
            if a := artist_summary(r):
                out["artists"].append(a)


async def search(query: str, kind: str = "all", limit: int = 20) -> dict:
    """kind "all": songs come from a songs-only search (those carry their album), the rest from the mixed one."""
    query = query.strip()
    out: dict[str, Any] = {"songs": [], "albums": [], "playlists": [], "artists": []}
    if not query:
        return out
    if kind != "all":
        _sort_results(await call(lambda yt: yt.search(query, filter=SEARCH_FILTERS.get(kind, "songs"), limit=limit)), out)
        return out
    mixed, songs = await asyncio.gather(call(lambda yt: yt.search(query, limit=limit)),
                                        call(lambda yt: yt.search(query, filter="songs", limit=limit)))
    _sort_results([r for r in mixed if r.get("resultType") not in ("song", "video")], out)
    _sort_results(songs, out)
    if top := next((r for r in mixed if r.get("category") == "Top result"), None):
        t = top.get("resultType")
        item = song(top) if t in ("song", "video") else album_summary(top) if t in ("album", "single", "ep") \
            else artist_summary(top) if t == "artist" else playlist_summary(top) if t == "playlist" else None
        if item:
            out["top"] = {"type": "song" if t in ("song", "video") else "album" if t in ("album", "single", "ep") else t,
                          "item": item}
    return out


_album_cache: dict[str, tuple[float, dict]] = {}


async def album(browse_id: str) -> dict:
    hit = _album_cache.get(browse_id)
    if hit and hit[0] > time.time():
        return hit[1]
    data = await call(lambda yt: yt.get_album(browse_id))
    artist, artist_id = _artists(data)
    head = {"id": browse_id, "title": data.get("title") or "", "artist": artist, "artist_id": artist_id,
            "year": str(data.get("year") or "") or None, "type": data.get("type") or "Album",
            "thumbnail": _thumb(data), "description": data.get("description"),
            "playlist_id": data.get("audioPlaylistId")}
    raw = data.get("tracks") or []
    # get_album hands out the music video's id where a track has one; the album's audio playlist lists the
    # audio tracks in the same order, so their ids win (the audio is what gets played and downloaded).
    if head["playlist_id"] and any(t.get("videoType") not in (None, "MUSIC_VIDEO_TYPE_ATV") for t in raw):
        try:
            pl = await call(lambda yt: yt.get_playlist(head["playlist_id"], limit=None))
            audio = pl.get("tracks") or []
            if len(audio) == len(raw):
                raw = [{**t, "videoId": a.get("videoId") or t.get("videoId"), "videoType": a.get("videoType") or "MUSIC_VIDEO_TYPE_ATV",
                        "isAvailable": a.get("isAvailable", t.get("isAvailable"))} for t, a in zip(raw, audio)]
        except YouTubeError as e:
            log.info("audio playlist of %s: %s", browse_id, e)
    tracks = []
    for i, t in enumerate(raw, start=1):
        s = song(t, head)
        if s:
            s["track"] = t.get("trackNumber") or i
            s["album"], s["album_id"] = head["title"], browse_id
            if not s["artist"]:
                s["artist"] = artist
            s["thumbnail"] = head["thumbnail"]
            tracks.append(s)
    result = {**head, "tracks": tracks, "track_count": len(tracks), "duration": sum(t["duration"] for t in tracks)}
    _album_cache[browse_id] = (time.time() + 3600, result)
    return result


async def playlist(playlist_id: str, limit: int | None = None) -> dict:
    playlist_id = playlist_id.removeprefix("VL")
    if playlist_id == LIKED:
        data = await call(lambda yt: yt.get_liked_songs(limit=limit), authenticated=True)
        data.setdefault("title", "Liked Music")
    else:
        data = await call(lambda yt: yt.get_playlist(playlist_id, limit=limit))
    summary = playlist_summary({**data, "playlistId": playlist_id}) or {"id": playlist_id}
    tracks = [s for s in (song(t) for t in data.get("tracks") or []) if s]
    return {**summary, "title": data.get("title") or summary.get("title") or "", "tracks": tracks,
            "track_count": data.get("trackCount") or len(tracks), "duration": sum(t["duration"] for t in tracks),
            "editable": data.get("owned", playlist_id == LIKED) is True or playlist_id == LIKED,
            "description": data.get("description")}


async def artist(channel_id: str) -> dict:
    data = await call(lambda yt: yt.get_artist(channel_id))
    songs = [s for s in (song(t) for t in (data.get("songs") or {}).get("results") or []) if s]
    albums = [a for a in (album_summary(x) for x in (data.get("albums") or {}).get("results") or []) if a]
    singles = [a for a in (album_summary(x) for x in (data.get("singles") or {}).get("results") or []) if a]
    return {"id": channel_id, "name": data.get("name") or "", "thumbnail": _thumb(data),
            "description": data.get("description"), "songs": songs, "albums": albums, "singles": singles,
            "songs_playlist_id": ((data.get("songs") or {}).get("browseId") or "").removeprefix("VL") or None}


async def song_details(video_id: str) -> dict:
    """A single song with its album, from the "up next" list (the song itself is first)."""
    data = await call(lambda yt: yt.get_watch_playlist(videoId=video_id, limit=1))
    tracks = data.get("tracks") or []
    first = next((t for t in tracks if t.get("videoId") == video_id), tracks[0] if tracks else None)
    if not first:
        raise YouTubeError("That song wasn't found")
    s = song(first) or {"id": video_id, "title": video_id, "artist": "", "duration": 0}
    s["lyrics_id"] = data.get("lyrics")
    return s


async def lyrics(video_id: str) -> str | None:
    try:
        data = await call(lambda yt: yt.get_watch_playlist(videoId=video_id, limit=1))
        if not data.get("lyrics"):
            return None
        lyr = await call(lambda yt: yt.get_lyrics(data["lyrics"]))
        return (lyr or {}).get("lyrics")
    except YouTubeError:
        return None


# ---------------------------------------------------------------- the account's playlists

async def library_playlists() -> list[dict]:
    if not account():
        raise NotSignedIn("No account is connected. Connect one in Amplicon's web interface.")
    pls = await call(lambda yt: yt.get_library_playlists(limit=None), authenticated=True)
    out = [{"id": LIKED, "title": "Liked Music", "author": None, "thumbnail": None, "track_count": None, "editable": True}]
    for p in pls:
        s = playlist_summary(p)
        if s and s["id"] != LIKED:
            out.append({**s, "editable": True})
    return out


async def create_playlist(title: str, video_ids: list[str], privacy: str = "PRIVATE") -> str:
    title = re.sub(r"[<>]", "", title).strip()[:150]
    if not title:
        raise ValueError("The playlist needs a name")
    res = await call(lambda yt: yt.create_playlist(title, "", privacy_status=privacy, video_ids=video_ids or None),
                     authenticated=True)
    if not isinstance(res, str):
        raise YouTubeError(f"The playlist wasn't created: {str(res)[:200]}")
    return res


async def rename_playlist(playlist_id: str, title: str) -> None:
    if playlist_id == LIKED:
        raise ValueError("Liked Music can't be renamed")
    await call(lambda yt: yt.edit_playlist(playlist_id, title=title.strip()[:150]), authenticated=True)


async def delete_playlist(playlist_id: str) -> None:
    if playlist_id == LIKED:
        raise ValueError("Liked Music can't be deleted")
    await call(lambda yt: yt.delete_playlist(playlist_id), authenticated=True)


async def add_to_playlist(playlist_id: str, video_ids: list[str]) -> None:
    if playlist_id == LIKED:
        for v in video_ids:
            await call(lambda yt, v=v: yt.rate_song(v, "LIKE"), authenticated=True)
        return
    for i in range(0, len(video_ids), 50):
        res = await call(lambda yt, b=video_ids[i:i + 50]: yt.add_playlist_items(playlist_id, b, duplicates=True),
                         authenticated=True)
        if isinstance(res, dict) and res.get("status") not in (None, "STATUS_SUCCEEDED"):
            raise YouTubeError(f"The songs weren't added: {str(res)[:200]}")


async def remove_from_playlist(playlist_id: str, items: list[dict]) -> None:
    """items: [{id, set_video_id}]"""
    if playlist_id == LIKED:
        for it in items:
            await call(lambda yt, v=it["id"]: yt.rate_song(v, "INDIFFERENT"), authenticated=True)
        return
    videos = [{"videoId": it["id"], "setVideoId": it["set_video_id"]} for it in items if it.get("set_video_id")]
    if len(videos) != len(items):
        raise YouTubeError("The playlist changed on the server. Refresh it and try again.")
    await call(lambda yt: yt.remove_playlist_items(playlist_id, videos), authenticated=True)


async def move_in_playlist(playlist_id: str, set_video_id: str, before_set_video_id: str | None) -> None:
    if playlist_id == LIKED:
        raise ValueError("Liked Music can't be reordered")
    move = (set_video_id, before_set_video_id) if before_set_video_id else set_video_id
    await call(lambda yt: yt.edit_playlist(playlist_id, moveItem=move), authenticated=True)
