"""The library: every audio file in every location, indexed so it can be filtered, edited, renamed and deduplicated.

The files stay the truth. The index is rebuilt from them by a scan, and a scan of a location that isn't mounted
changes nothing, so a missing disk can't empty the library.
"""
import asyncio
import hashlib
import logging
import os
import re
import shutil
import threading
from pathlib import Path

from . import db, locations, metadata, profiles, settings_store, tags
from .events import bus

log = logging.getLogger("amplicon.files")

SORTS = {"title", "artist", "album", "albumartist", "year", "genre", "format", "bitrate", "duration", "size",
         "added_at", "path", "track"}
MISSING = {"title": "title", "artist": "artist", "album": "album", "albumartist": "albumartist", "year": "year",
           "genre": "genre", "label": "label", "track": "track", "isrc": "isrc"}
COLUMNS = ("id", "location_id", "path", "target_path", "size", "mtime", "format", "duration", "bitrate", "title",
           "artist", "albumartist", "album", "track", "track_total", "disc", "disc_total", "year", "genre", "label",
           "isrc", "video_id", "has_cover", "has_lyrics", "error", "added_at")
INDEXED = ("title", "artist", "albumartist", "album", "track", "track_total", "disc", "disc_total", "year", "genre",
           "label", "isrc", "video_id", "format", "duration", "bitrate")

state = {"running": False, "location": None, "seen": 0, "changed": 0, "finished_at": None}
_write_lock = threading.Lock()


# ---------------------------------------------------------------- scanning

def _record(path: Path) -> dict:
    st = path.stat()
    rec: dict = {"size": st.st_size, "mtime": st.st_mtime, "error": None}
    try:
        t = tags.read(path)
        rec.update({k: t.get(k) for k in INDEXED})
        rec["has_cover"] = int(bool(t["has_cover"]))
        rec["has_lyrics"] = int(bool(t.get("lyrics")))
    except Exception as e:  # noqa: BLE001 - a broken file still shows up, with the reason
        rec.update({k: None for k in INDEXED})
        rec.update({"has_cover": 0, "has_lyrics": 0, "error": str(e)[:200], "format": path.suffix.lstrip(".").lower()})
    return rec


def _upsert(location_id: int, rel: str, rec: dict, extra: dict | None = None) -> int:
    now = db.now()
    row = db.row("SELECT id FROM library_files WHERE location_id = ? AND path = ?", [location_id, rel])
    fields = {**rec, **(extra or {}), "scanned_at": now}
    if row:
        db.update("library_files", row["id"], fields)
        return row["id"]
    return db.insert("library_files", {"location_id": location_id, "path": rel, "added_at": now, **fields})


def scan_location(loc: dict) -> None:
    root = Path(loc["path"])
    if not root.is_dir():
        log.info("location %s isn't mounted, keeping its index", loc["name"])
        return
    started = db.now()
    known = {r["path"]: r for r in db.rows("SELECT id, path, size, mtime FROM library_files WHERE location_id = ?", [loc["id"]])}
    seen: list[int] = []
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in names:
            if name.startswith(".") or not name.lower().endswith(tags.SUPPORTED):
                continue
            p = Path(folder) / name
            rel = str(p.relative_to(root))
            state["seen"] += 1
            try:
                st = p.stat()
            except OSError:
                continue
            k = known.get(rel)
            if k and k["size"] == st.st_size and abs(k["mtime"] - st.st_mtime) < 0.001:
                seen.append(k["id"])
                continue
            seen.append(_upsert(loc["id"], rel, _record(p)))
            state["changed"] += 1
    keep = set(seen)
    for rel, k in known.items():
        # A rename during the scan may have moved it, so what's on disk decides, not the list from the start.
        if k["id"] not in keep and not (root / rel).exists():
            db.execute("DELETE FROM library_files WHERE id = ? AND path = ? AND scanned_at < ?", [k["id"], rel, started])
    if keep:
        for i in range(0, len(seen), 500):
            part = seen[i:i + 500]
            db.execute(f"UPDATE library_files SET scanned_at = ? WHERE id IN ({','.join('?' * len(part))})", [db.now(), *part])


def scan(location_id: int | None = None) -> None:
    if state["running"]:
        return
    state.update(running=True, seen=0, changed=0)
    bus.publish("library", {**state})
    try:
        for loc in [locations.get(location_id)] if location_id else db.rows("SELECT * FROM locations"):
            if loc:
                state["location"] = loc["name"]
                scan_location(loc)
        refresh_targets()
    except Exception:  # noqa: BLE001
        log.exception("library scan")
    finally:
        state.update(running=False, location=None, finished_at=db.now())
        bus.publish("library", {**state})


async def scan_soon(location_id: int | None = None) -> None:
    await asyncio.to_thread(scan, location_id)


def index_file(loc: dict, rel: str, profile_id: int | None = None, playlist: str | None = None,
               position: int | None = None) -> None:
    """A finished download goes straight into the library."""
    p = Path(loc["path"]) / rel
    fid = _upsert(loc["id"], rel, _record(p), {"profile_id": profile_id, "playlist": playlist, "position": position})
    refresh_targets(ids=[fid])
    bus.publish("library", {**state})


# ---------------------------------------------------------------- file names

def _rule_for(f: dict, loc: dict, by_id: dict[int, dict], per_location: dict[int, dict]) -> str | None:
    """The name a file should have, or None when the rules don't apply to it."""
    if not (f.get("video_id") or loc.get("rename_all")):
        return None  # not downloaded by Amplicon, and the location leaves such files alone
    if f.get("error") or not (f.get("title") and f.get("artist")):
        return None  # a name made of missing tags would be worse than the one it has
    profile = by_id.get(f.get("profile_id")) or per_location.get(loc["id"]) or per_location.get(0)
    if not profile:
        return None
    if f.get("playlist") and profile["playlist_layout"] == "playlist":
        template = profile["playlist_template"]
    elif f.get("album") and f.get("track"):
        template = profile["album_template"]
    else:
        template = profile["single_template"]
    values = {**f, "albumartist": f.get("albumartist") or f.get("artist"), "disc_total": f.get("disc_total") or 1}
    try:
        return profiles.render(template, values) + Path(f["path"]).suffix.lower()
    except ValueError:
        return None


def _rule_context() -> tuple[dict, dict, dict]:
    locs = {loc["id"]: loc for loc in db.rows("SELECT * FROM locations")}
    all_profiles = db.rows("SELECT * FROM profiles ORDER BY is_default DESC, id")
    by_id = {p["id"]: p for p in all_profiles}
    per_location: dict[int, dict] = {}
    for p in reversed(all_profiles):  # the default profile wins, so it's written last
        per_location[p["location_id"]] = p
    if all_profiles:
        per_location[0] = all_profiles[0]
    return locs, by_id, per_location


def refresh_targets(ids: list[int] | None = None) -> None:
    """Works out where every file belongs. Runs after scans, edits and changes of a profile or location."""
    locs, by_id, per_location = _rule_context()
    sql = "SELECT * FROM library_files"
    params: list = []
    if ids is not None:
        if not ids:
            return
        sql += f" WHERE id IN ({','.join('?' * len(ids))})"
        params = ids
    rows = db.rows(sql, params)
    with db.tx() as c:
        for f in rows:
            loc = locs.get(f["location_id"])
            target = _rule_for(f, loc, by_id, per_location) if loc else None
            if target != f["target_path"]:
                c.execute("UPDATE library_files SET target_path = ? WHERE id = ?", [target, f["id"]])


def _misnamed_sql() -> str:
    return "target_path IS NOT NULL AND target_path != path"


def rename_preview(location_id: int | None = None, ids: list[int] | None = None, limit: int = 8) -> dict:
    where, params = [_misnamed_sql()], []
    if location_id:
        where.append("location_id = ?")
        params.append(location_id)
    if ids is not None:
        where.append(f"id IN ({','.join('?' * len(ids))})" if ids else "0")
        params += ids
    clause = " AND ".join(where)
    return {"count": db.scalar(f"SELECT COUNT(*) FROM library_files WHERE {clause}", params),
            "samples": db.rows(f"SELECT id, path, target_path FROM library_files WHERE {clause} ORDER BY path LIMIT ?", [*params, limit])}


def _prune(folder: Path, root: Path) -> None:
    """Removes folders a rename left empty, up to the location itself."""
    while folder != root and root in folder.parents:
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def _fix_playlists(root: Path, moved: dict[str, str]) -> None:
    """M3U files point at the songs by path, so they follow every rename."""
    folder = root / "Playlists"
    if not moved or not folder.is_dir():
        return
    for m3u in folder.glob("*.m3u*"):
        try:
            lines = m3u.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        changed = False
        for i, line in enumerate(lines):
            if not line or line.startswith("#"):
                continue
            rel = os.path.normpath(os.path.relpath((folder / line).resolve(), root.resolve()))
            if rel in moved:
                lines[i] = os.path.relpath(root / moved[rel], folder)
                changed = True
        if changed:
            m3u.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _rename_rows(rows: list[dict]) -> dict:
    locs = {loc["id"]: loc for loc in db.rows("SELECT * FROM locations")}
    renamed, problems = 0, []
    moved: dict[int, dict[str, str]] = {}
    for f in rows:
        loc = locs.get(f["location_id"])
        if not loc or not f["target_path"] or f["target_path"] == f["path"]:
            continue
        root = Path(loc["path"]).resolve()
        src, dest = root / f["path"], (root / f["target_path"]).resolve()
        if root not in dest.parents:
            problems.append({"id": f["id"], "path": f["path"], "reason": "The rule leads outside the location"})
            continue
        if not src.exists():
            problems.append({"id": f["id"], "path": f["path"], "reason": "The file is gone"})
            continue
        if dest.exists() and not (dest.samefile(src)):
            problems.append({"id": f["id"], "path": f["path"], "target": f["target_path"],
                             "reason": "Another file already has that name"})
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            lrc = src.with_suffix(".lrc")
            if lrc.exists():
                shutil.move(str(lrc), str(dest.with_suffix(".lrc")))
        except OSError as e:
            problems.append({"id": f["id"], "path": f["path"], "reason": str(e)[:200]})
            continue
        with db.tx() as c:
            c.execute("UPDATE library_files SET path = ? WHERE id = ?", [f["target_path"], f["id"]])
            c.execute("UPDATE job_items SET file_path = ? WHERE file_path = ? AND job_id IN "
                      "(SELECT id FROM jobs WHERE location_id = ?)", [f["target_path"], f["path"], loc["id"]])
        moved.setdefault(loc["id"], {})[f["path"]] = f["target_path"]
        _prune(src.parent, root)
        renamed += 1
    for lid, paths in moved.items():
        _fix_playlists(Path(locs[lid]["path"]), paths)
    return {"renamed": renamed, "problems": problems, "locations": list(moved)}


def rename(location_id: int | None = None, ids: list[int] | None = None) -> dict:
    where, params = [_misnamed_sql()], []
    if location_id:
        where.append("location_id = ?")
        params.append(location_id)
    if ids is not None:
        where.append(f"id IN ({','.join('?' * len(ids))})" if ids else "0")
        params += ids
    with _write_lock:
        return _rename_rows(db.rows(f"SELECT * FROM library_files WHERE {' AND '.join(where)} ORDER BY id", params))


# ---------------------------------------------------------------- browsing

def _public(f: dict) -> dict:
    out = {k: f.get(k) for k in COLUMNS}
    out["has_cover"], out["has_lyrics"] = bool(f.get("has_cover")), bool(f.get("has_lyrics"))
    out["misnamed"] = bool(f.get("target_path")) and f.get("target_path") != f.get("path")
    out["location_name"] = f.get("location_name")
    return out


def _filters(p: dict) -> tuple[str, list]:
    where, params = [], []
    if p.get("q"):
        for word in str(p["q"]).split():
            like = f"%{word}%"
            where.append("(f.title LIKE ? OR f.artist LIKE ? OR f.album LIKE ? OR f.albumartist LIKE ? OR f.path LIKE ?)")
            params += [like] * 5
    for key in ("location_id", "format", "year"):
        if p.get(key):
            where.append(f"f.{key} LIKE ?" if key == "year" else f"f.{key} = ?")
            params.append(f"{p[key]}%" if key == "year" else p[key])
    for key in ("artist", "album", "genre"):
        if p.get(key):
            where.append(f"f.{key} = ? COLLATE NOCASE")
            params.append(p[key])
    for name in str(p.get("missing") or "").split(","):
        if name in MISSING:
            where.append(f"(f.{MISSING[name]} IS NULL OR f.{MISSING[name]} = '')")
        elif name == "cover":
            where.append("f.has_cover = 0")
        elif name == "lyrics":
            where.append("f.has_lyrics = 0")
    if p.get("misnamed"):
        where.append("f.target_path IS NOT NULL AND f.target_path != f.path")
    if p.get("broken"):
        where.append("f.error IS NOT NULL")
    if p.get("source") == "amplicon":
        where.append("f.video_id IS NOT NULL")
    elif p.get("source") == "other":
        where.append("f.video_id IS NULL")
    return (" WHERE " + " AND ".join(where) if where else ""), params


def search(p: dict) -> dict:
    clause, params = _filters(p)
    sort = p.get("sort") if p.get("sort") in SORTS else "artist"
    order = "DESC" if p.get("dir") == "desc" else "ASC"
    collate = " COLLATE NOCASE" if sort in ("title", "artist", "album", "albumartist", "genre", "path") else ""
    empties = f"(f.{sort} IS NULL OR f.{sort} = '')"
    limit = max(1, min(int(p.get("limit") or 100), 500))
    offset = max(0, int(p.get("offset") or 0))
    rows = db.rows(f"SELECT f.*, l.name AS location_name FROM library_files f JOIN locations l ON l.id = f.location_id {clause} "
                   f"ORDER BY {empties}, f.{sort}{collate} {order}, f.title COLLATE NOCASE, f.id LIMIT ? OFFSET ?", [*params, limit, offset])
    total = db.scalar(f"SELECT COUNT(*) FROM library_files f {clause}", params)
    return {"total": total, "items": [_public(r) for r in rows], "scan": {**state}}


def matching_ids(p: dict, cap: int = 5000) -> list[int]:
    clause, params = _filters(p)
    return [r["id"] for r in db.rows(f"SELECT f.id FROM library_files f {clause} LIMIT ?", [*params, cap])]


def facets() -> dict:
    def top(col: str, n: int = 400) -> list[dict]:
        return db.rows(f"SELECT {col} AS value, COUNT(*) AS n FROM library_files WHERE {col} IS NOT NULL AND {col} != '' "
                       f"GROUP BY {col} COLLATE NOCASE ORDER BY n DESC, value COLLATE NOCASE LIMIT ?", [n])

    def empty(col: str) -> int:
        return db.scalar(f"SELECT COUNT(*) FROM library_files WHERE {col} IS NULL OR {col} = ''")

    totals = db.row("SELECT COUNT(*) AS files, COALESCE(SUM(size), 0) AS bytes, COALESCE(SUM(duration), 0) AS seconds FROM library_files")
    return {
        **totals, "formats": top("format"), "genres": top("genre"), "artists": top("artist"), "albums": top("album"),
        "years": db.rows("SELECT substr(year, 1, 4) AS value, COUNT(*) AS n FROM library_files WHERE year IS NOT NULL AND year != '' "
                         "GROUP BY value ORDER BY value DESC"),
        "missing": {**{k: empty(k) for k in ("title", "artist", "album", "year", "genre")},
                    "cover": db.scalar("SELECT COUNT(*) FROM library_files WHERE has_cover = 0"),
                    "lyrics": db.scalar("SELECT COUNT(*) FROM library_files WHERE has_lyrics = 0")},
        "misnamed": db.scalar(f"SELECT COUNT(*) FROM library_files WHERE {_misnamed_sql()}"),
        "broken": db.scalar("SELECT COUNT(*) FROM library_files WHERE error IS NOT NULL"),
        "locations": db.rows("SELECT l.id, l.name, COUNT(f.id) AS n FROM locations l LEFT JOIN library_files f ON f.location_id = l.id "
                             "GROUP BY l.id ORDER BY l.name COLLATE NOCASE"),
        "scan": {**state},
    }


def _file(file_id: int) -> tuple[dict, Path]:
    f = db.row("SELECT f.*, l.name AS location_name, l.path AS root FROM library_files f JOIN locations l ON l.id = f.location_id "
               "WHERE f.id = ?", [file_id])
    if not f:
        raise ValueError("That file isn't in the library")
    return f, Path(f["root"]) / f["path"]


def path_of(file_id: int) -> Path:
    return _file(file_id)[1]


def detail(file_id: int) -> dict:
    f, path = _file(file_id)
    out = _public(f)
    out["tags"] = {k: f.get(k) for k in tags.FIELDS if k not in ("comment", "lyrics")}
    try:
        t = tags.read(path)
        out["tags"] = {k: t.get(k) for k in tags.FIELDS}
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:200]
    return out


# ---------------------------------------------------------------- editing

def _as_number(v) -> int | None:
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{v} isn't a number") from e


def clean_changes(fields: dict) -> dict:
    out = {}
    for k, v in fields.items():
        if k not in tags.FIELDS:
            raise ValueError(f"Unknown field {k}")
        if k in tags.NUMBER_FIELDS:
            out[k] = _as_number(v)
        else:
            out[k] = (str(v).strip() if k != "lyrics" else str(v)) if v is not None else ""
    return out


def apply_changes(changes: list[dict]) -> dict:
    """changes: [{id, fields, cover (JPEG bytes), remove_cover}]. Writes tags, then moves the files to where the rules
    say. One broken file doesn't stop the others."""
    done, problems, touched = 0, [], []
    with _write_lock:
        for ch in changes:
            try:
                f, path = _file(int(ch["id"]))
                fields = clean_changes(ch.get("fields") or {})
                if fields or ch.get("cover") or ch.get("remove_cover"):
                    tags.update(path, fields, cover=ch.get("cover"), remove_cover=bool(ch.get("remove_cover")))
                    _upsert(f["location_id"], f["path"], _record(path))
                touched.append(f["id"])
                done += 1
            except Exception as e:  # noqa: BLE001
                problems.append({"id": ch.get("id"), "reason": str(e)[:200]})
        refresh_targets(ids=touched)
        renamed = {"renamed": 0, "problems": [], "locations": []}
        if touched and settings_store.get("enforce_file_names"):
            rows = db.rows(f"SELECT * FROM library_files WHERE id IN ({','.join('?' * len(touched))}) AND {_misnamed_sql()}", touched)
            renamed = _rename_rows(rows)
    return {"changed": done, "renamed": renamed["renamed"], "problems": problems + renamed["problems"],
            "locations": sorted({db.scalar("SELECT location_id FROM library_files WHERE id = ?", [i]) for i in touched} - {None})}


def _title_case(s: str) -> str:
    small = {"a", "an", "and", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "vs", "with"}
    words = s.split(" ")
    shouting = sum(w.isupper() for w in words) * 2 > len(words)  # then capitals say nothing about a word
    out = []
    for i, w in enumerate(words):
        low = w.lower()
        if w.isupper() and len(w) <= 4 and w.isalpha() and i and not shouting:  # DJ, USA, ABBA stay
            out.append(w)
        elif i and low in small:
            out.append(low)
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)


def _from_name(name: str, pattern: str) -> dict:
    """'{artist} - {title}' against 'Daft Punk - One More Time' → the fields."""
    parts = re.split(r"(\{\w+\})", pattern)
    regex = "".join(f"(?P<{p[1:-1]}>.+?)" if re.fullmatch(r"\{\w+\}", p) else re.escape(p) for p in parts)
    m = re.fullmatch(regex, name)
    return {k: v.strip() for k, v in m.groupdict().items() if k in tags.FIELDS} if m else {}


def preview_ops(ids: list[int], ops: dict) -> list[dict]:
    """What bulk operations would change, without writing anything.
    ops: set {field: value}, clear [field], replace {field, find, with, regex}, case {field, mode},
    from_name pattern, number {start}, artist_split true."""
    proposals = []
    replace = ops.get("replace") or None
    pattern = None
    if replace and replace.get("find"):
        try:
            pattern = re.compile(replace["find"] if replace.get("regex") else re.escape(replace["find"]),
                                 0 if replace.get("match_case") else re.I)
        except re.error as e:
            raise ValueError(f"That pattern doesn't work: {e}") from e
    start = (ops.get("number") or {}).get("start")
    for n, fid in enumerate(ids):
        f, path = _file(fid)
        before = {k: f.get(k) for k in tags.FIELDS if k not in ("comment", "lyrics")}
        after = dict(before)
        after.update(clean_changes(ops.get("set") or {}))
        for k in ops.get("clear") or []:
            if k in after:
                after[k] = None
        if pattern:
            field = replace.get("field")
            if field in after and isinstance(after[field], str):
                after[field] = pattern.sub(replace.get("with") or "", after[field]).strip()
        if (case := ops.get("case")) and case.get("field") in after and after[case["field"]]:
            v = str(after[case["field"]])
            after[case["field"]] = {"title": _title_case(v), "upper": v.upper(), "lower": v.lower()}.get(case.get("mode"), v)
        if ops.get("from_name"):
            after.update(_from_name(path.stem, ops["from_name"]))
        if start is not None:
            after["track"] = int(start) + n
        if ops.get("clean_title") and after.get("title"):
            after["title"] = metadata.clean_title(after["title"])
        changed = [k for k in after if (after[k] or None) != (before[k] or None)]
        proposals.append({"id": fid, "path": f["path"], "before": before, "after": after, "changed": changed})
    return proposals


# ---------------------------------------------------------------- duplicates

def _sha(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _song_key(f: dict) -> str | None:
    title = metadata.norm(metadata.clean_title(f.get("title")))
    first = metadata.artists_of(f.get("artist"))
    if not title or not first:
        return None
    return f"{title}|{metadata.norm(first[0])}|{','.join(sorted(metadata.versions(f.get('title'))))}"


def _quality(f: dict) -> tuple:
    """The copy to keep: best sound, then best tags, then the right name."""
    tagged = sum(bool(f.get(k)) for k in ("title", "artist", "album", "year", "genre", "track", "isrc"))
    return (f.get("bitrate") or 0, bool(f.get("has_cover")), tagged, f.get("target_path") in (None, f.get("path")), -f["id"])


def duplicates(show_ignored: bool = False) -> list[dict]:
    rows = db.rows("SELECT f.*, l.name AS location_name, l.path AS root FROM library_files f JOIN locations l ON l.id = f.location_id")
    parent = {f["id"]: f["id"] for f in rows}
    reason: dict[int, set] = {}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def join(group: list[dict], why: str) -> None:
        for f in group[1:]:
            parent[find(f["id"])] = find(group[0]["id"])
        for f in group:
            reason.setdefault(f["id"], set()).add(why)

    def buckets(key) -> dict:
        out: dict = {}
        for f in rows:
            k = key(f)
            if k:
                out.setdefault(k, []).append(f)
        return {k: v for k, v in out.items() if len(v) > 1}

    for group in buckets(lambda f: f.get("video_id")).values():
        join(group, "video")
    for group in buckets(lambda f: f["size"] or None).values():
        by_hash: dict = {}
        for f in group:
            if h := _sha(Path(f["root"]) / f["path"]):
                by_hash.setdefault(h, []).append(f)
        for same in by_hash.values():
            if len(same) > 1:
                join(same, "file")
    for group in buckets(_song_key).values():
        group.sort(key=lambda f: f.get("duration") or 0)
        run = [group[0]]
        for f in group[1:]:
            a, b = run[-1].get("duration"), f.get("duration")
            if a and b and abs(a - b) > 3:  # the same name, but another recording
                if len(run) > 1:
                    join(run, "song")
                run = [f]
            else:
                run.append(f)
        if len(run) > 1:
            join(run, "song")

    groups: dict[int, list[dict]] = {}
    for f in rows:
        if f["id"] in reason:
            groups.setdefault(find(f["id"]), []).append(f)
    ignored = {r["key"] for r in db.rows("SELECT key FROM duplicate_ignores")}
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        key = hashlib.sha1(",".join(str(i) for i in sorted(m["id"] for m in members)).encode()).hexdigest()
        if key in ignored and not show_ignored:
            continue
        why = set().union(*(reason[m["id"]] for m in members))
        kind = "file" if "file" in why else "video" if "video" in why else "song"
        best = max(members, key=_quality)
        out.append({"key": key, "kind": kind, "keep": best["id"], "ignored": key in ignored,
                    "files": [_public(m) for m in sorted(members, key=_quality, reverse=True)]})
    out.sort(key=lambda g: (g["files"][0].get("artist") or "").lower())
    return out


def ignore_duplicates(key: str) -> None:
    db.execute("INSERT OR REPLACE INTO duplicate_ignores (key, created_at) VALUES (?, ?)", [key, db.now()])


def delete_files(ids: list[int]) -> dict:
    deleted, problems = 0, []
    with _write_lock:
        for fid in ids:
            try:
                f, path = _file(fid)
                path.unlink(missing_ok=True)
                path.with_suffix(".lrc").unlink(missing_ok=True)
                db.execute("DELETE FROM library_files WHERE id = ?", [fid])
                _prune(path.parent, Path(f["root"]))
                deleted += 1
            except (OSError, ValueError) as e:
                problems.append({"id": fid, "reason": str(e)[:200]})
    return {"deleted": deleted, "problems": problems}
