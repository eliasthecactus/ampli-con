"""The library: every audio file in every location, indexed so it can be filtered, edited, renamed and deduplicated.

The files stay the truth. The index is rebuilt from them by a scan, and a scan of a location that isn't mounted
changes nothing, so a missing disk can't empty the library.

A file a scan finds is `new` and waits in the Import tab; nothing happens to it there. Importing gives it a profile,
and from then on the profile says where the file belongs and how it should look. Downloads start out imported.
A file that disappears from the disk stays listed as deleted, so its history and profile survive a moved folder or
a disk that comes back.
"""
import asyncio
import hashlib
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path

from . import db, locations, metadata, profiles, settings_store, tags
from .events import bus

log = logging.getLogger("amplicon.files")

SORTS = {"title", "artist", "album", "albumartist", "year", "genre", "format", "bitrate", "duration", "size",
         "added_at", "path", "track", "profile_id"}
MISSING = {"title": "title", "artist": "artist", "album": "album", "albumartist": "albumartist", "year": "year",
           "genre": "genre", "label": "label", "track": "track", "isrc": "isrc"}
COLUMNS = ("id", "location_id", "path", "target_path", "size", "mtime", "format", "duration", "bitrate", "title",
           "artist", "albumartist", "album", "track", "track_total", "disc", "disc_total", "year", "genre", "label",
           "isrc", "video_id", "has_cover", "has_lyrics", "error", "added_at", "state", "profile_id", "imported_at",
           "deleted_at", "target_location_id")
INDEXED = ("title", "artist", "albumartist", "album", "track", "track_total", "disc", "disc_total", "year", "genre",
           "label", "isrc", "video_id", "format", "duration", "bitrate")

LIVE = "state = 'imported' AND deleted_at IS NULL"
ISSUES = ("name", "taken", "location", "tags", "cover", "profile", "broken")
# The file in the way of a rename, for every query that shows one.
CONFLICT_COLS = "c.path AS conflict_path, c.state AS conflict_state, cl.name AS conflict_location"
CONFLICT_JOIN = "LEFT JOIN library_files c ON c.id = f.conflict_id LEFT JOIN locations cl ON cl.id = c.location_id"
COVER_NAMES = ("cover", "folder", "front", "albumart", "album")
COVER_EXT = (".jpg", ".jpeg", ".png")
SETTLE_SECONDS = 30  # a file changed this recently may still be copying
SAME_SONG_SECONDS = 30  # only for a warning: a video edit of a song is often that much shorter than the album version

state = {"running": False, "location": None, "seen": 0, "changed": 0, "settling": 0, "finished_at": None}
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
    fields = {**rec, **(extra or {}), "scanned_at": now, "deleted_at": None}
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
    known = {r["path"]: r for r in db.rows("SELECT id, path, size, mtime, state, deleted_at FROM library_files "
                                           "WHERE location_id = ?", [loc["id"]])}
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
            if time.time() - st.st_mtime < SETTLE_SECONDS:
                state["settling"] += 1  # the next scan reads it, once it's complete
                if k:
                    seen.append(k["id"])
                continue
            seen.append(_upsert(loc["id"], rel, _record(p)))
            state["changed"] += 1
    keep = set(seen)
    for rel, k in known.items():
        # A rename during the scan may have moved it, so what's on disk decides, not the list from the start.
        if k["id"] in keep or (root / rel).exists():
            continue
        where = "id = ? AND path = ? AND location_id = ? AND scanned_at < ?"
        if k["state"] == "new":
            db.execute(f"DELETE FROM library_files WHERE {where}", [k["id"], rel, loc["id"], started])
        elif not k["deleted_at"]:
            db.execute(f"UPDATE library_files SET deleted_at = ?, issues = '', target_path = NULL, target_location_id = NULL "
                       f"WHERE {where}", [db.now(), k["id"], rel, loc["id"], started])
    for i in range(0, len(seen), 500):
        part = seen[i:i + 500]
        db.execute(f"UPDATE library_files SET scanned_at = ?, deleted_at = NULL WHERE id IN ({','.join('?' * len(part))})",
                   [db.now(), *part])


def _follow_moves(started: float) -> None:
    """A file moved outside Amplicon looks like one deleted and one new file. Joining them keeps its profile."""
    fresh = db.rows("SELECT * FROM library_files WHERE state = 'new' AND added_at >= ?", [started])
    if not fresh:
        return
    gone: dict[tuple, list[dict]] = {}
    for g in db.rows("SELECT * FROM library_files WHERE deleted_at IS NOT NULL AND state != 'new'"):
        gone.setdefault(("time", g["size"], int(g["mtime"])), []).append(g)
        if g["title"]:
            gone.setdefault(("tags", g["size"], g["title"], g["artist"]), []).append(g)
    taken: set[int] = set()
    for n in fresh:
        keys = [("time", n["size"], int(n["mtime"])), ("tags", n["size"], n["title"], n["artist"])]
        old = next((g for k in keys for g in gone.get(k, []) if g["id"] not in taken), None)
        if old:
            taken.add(old["id"])
            with db.tx() as c:
                c.execute("DELETE FROM library_files WHERE id = ?", [n["id"]])
                c.execute("UPDATE library_files SET location_id = ?, path = ?, mtime = ?, scanned_at = ?, deleted_at = NULL "
                          "WHERE id = ?", [n["location_id"], n["path"], n["mtime"], n["scanned_at"], old["id"]])
        elif n["video_id"]:
            # A download whose indexing failed: the job knows the profile.
            job = db.row("SELECT j.profile_id FROM job_items ji JOIN jobs j ON j.id = ji.job_id "
                         "WHERE ji.video_id = ? AND ji.file_path = ? AND j.profile_id IS NOT NULL ORDER BY ji.id DESC LIMIT 1",
                         [n["video_id"], n["path"]])
            if job:
                db.update("library_files", n["id"], {"state": "imported", "profile_id": job["profile_id"], "imported_at": db.now()})


def scan(location_id: int | None = None) -> None:
    if state["running"]:
        return
    state.update(running=True, seen=0, changed=0, settling=0)
    bus.publish("library", {**state})
    started = db.now()
    try:
        for loc in [locations.get(location_id)] if location_id else db.rows("SELECT * FROM locations"):
            if loc:
                state["location"] = loc["name"]
                scan_location(loc)
        _follow_moves(started)
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
    """A finished download goes straight into the library. Saved to another location than its profile's on purpose,
    it stays there."""
    p = Path(loc["path"]) / rel
    profile = db.row("SELECT location_id FROM profiles WHERE id = ?", [profile_id])
    fid = _upsert(loc["id"], rel, _record(p), {"profile_id": profile_id, "playlist": playlist, "position": position,
                                               "state": "imported", "imported_at": db.now(),
                                               "pinned": int(bool(profile and profile["location_id"] != loc["id"]))})
    video = db.scalar("SELECT video_id FROM library_files WHERE id = ?", [fid])
    if video:  # downloaded again under another name: the old entry has done its job
        db.execute("DELETE FROM library_files WHERE video_id = ? AND deleted_at IS NOT NULL AND id != ?", [video, fid])
    refresh_targets(ids=[fid])
    bus.publish("library", {**state})


# ---------------------------------------------------------------- what a profile asks of a file

def _plan(f: dict, by_id: dict[int, dict]) -> tuple[int | None, str | None, str]:
    """(location it belongs in, path it should have there, what's wrong with it)."""
    if f["state"] != "imported" or f["deleted_at"]:
        return None, None, ""
    if f["error"]:
        return None, None, "broken"
    profile = by_id.get(f["profile_id"])
    if not profile:
        return None, None, "profile"
    issues = [f"tag:{k}" for k in profiles.required(profile) if not f.get(k)]
    if profile["embed_cover"] and not f["has_cover"]:
        issues.append("cover")
    location_id = f["location_id"] if f["pinned"] else profile["location_id"]
    target = f["path"]
    if f["title"] and f["artist"]:  # a name made of missing tags would be worse than the one it has
        if f["playlist"] and profile["playlist_layout"] == "playlist":
            template = profile["playlist_template"]
        elif f["album"] and f["track"]:
            template = profile["album_template"]
        else:
            template = profile["single_template"]
        values = {**f, "albumartist": f["albumartist"] or f["artist"], "disc_total": f["disc_total"] or 1}
        try:
            target = profiles.render(template, values) + Path(f["path"]).suffix.lower()
        except ValueError:
            pass
    if location_id != f["location_id"]:
        issues.append("location")
    if target != f["path"]:
        issues.append("name")
    return location_id, target, ",".join(issues)


def refresh_targets(ids: list[int] | None = None) -> None:
    """Works out where every file belongs and what's wrong with it. Runs after scans, edits and changes of a profile."""
    by_id = {p["id"]: p for p in db.rows("SELECT * FROM profiles")}
    rows = []
    if ids is None:
        rows = db.rows("SELECT * FROM library_files")
    for i in range(0, len(ids or []), 500):
        part = ids[i:i + 500]
        rows += db.rows(f"SELECT * FROM library_files WHERE id IN ({','.join('?' * len(part))})", part)
    with db.tx() as c:
        for f in rows:
            location_id, target, issues = _plan(f, by_id)
            if (location_id, target, issues) != (f["target_location_id"], f["target_path"], f["issues"]):
                c.execute("UPDATE library_files SET target_location_id = ?, target_path = ?, issues = ? WHERE id = ?",
                          [location_id, target, issues, f["id"]])
    refresh_conflicts()


def _blocked() -> dict[int, int]:
    """Plays a rename of everything through, in the order of _rename_rows: {file that can't move: file in its way}.
    The index knows every file on the disk, so a name held by a file waiting for its import counts too, and a name its
    holder is about to give up doesn't. Of files after the same free name, the first gets it."""
    moves = db.rows(f"SELECT id, location_id, path, target_location_id, target_path FROM library_files WHERE {_moves_sql()} ORDER BY id")
    taken = {(r["location_id"], r["path"]): r["id"] for r in db.rows(
        "SELECT h.id, h.location_id, h.path FROM library_files f JOIN library_files h "
        "ON h.location_id = f.target_location_id AND h.path = f.target_path "
        f"WHERE h.deleted_at IS NULL AND {_moves_sql('f')}")}
    taken |= {(f["location_id"], f["path"]): f["id"] for f in moves}
    waiting = moves
    while waiting:
        stuck = []
        for f in waiting:
            to = (f["target_location_id"], f["target_path"])
            if to in taken:
                stuck.append(f)
            else:
                del taken[(f["location_id"], f["path"])]
                taken[to] = f["id"]
        if len(stuck) == len(waiting):
            break
        waiting = stuck
    return {f["id"]: taken[(f["target_location_id"], f["target_path"])] for f in waiting}


def refresh_conflicts() -> None:
    """Names free up and get taken by what happens to other files, so this looks at all of them."""
    blocked = _blocked()
    before = {r["id"]: r["conflict_id"] for r in db.rows("SELECT id, conflict_id FROM library_files WHERE conflict_id IS NOT NULL")}
    with db.tx() as c:
        for fid in before.keys() - blocked.keys():
            c.execute("UPDATE library_files SET conflict_id = NULL WHERE id = ?", [fid])
        for fid, holder in blocked.items():
            if before.get(fid) != holder:
                c.execute("UPDATE library_files SET conflict_id = ? WHERE id = ?", [holder, fid])


def _moves_sql(alias: str = "") -> str:
    a = f"{alias}." if alias else ""
    return (f"{a}state = 'imported' AND {a}deleted_at IS NULL AND {a}target_path IS NOT NULL "
            f"AND ({a}target_path != {a}path OR {a}target_location_id != {a}location_id)")


def rename_preview(location_id: int | None = None, ids: list[int] | None = None, limit: int = 8) -> dict:
    where, params = [_moves_sql("f")], []
    if location_id:
        where.append("f.location_id = ?")
        params.append(location_id)
    if ids is not None:
        where.append(f"f.id IN ({','.join('?' * len(ids))})" if ids else "0")
        params += ids
    clause = " AND ".join(where)
    return {"count": db.scalar(f"SELECT COUNT(*) FROM library_files f WHERE {clause}", params),
            "blocked": db.scalar(f"SELECT COUNT(*) FROM library_files f WHERE {clause} AND f.conflict_id IS NOT NULL", params),
            "samples": [_with_conflict(r) for r in db.rows(
                f"SELECT f.id, f.path, f.target_path, f.conflict_id, l.name AS location, t.name AS target_location, {CONFLICT_COLS} "
                f"FROM library_files f JOIN locations l ON l.id = f.location_id LEFT JOIN locations t ON t.id = f.target_location_id "
                f"{CONFLICT_JOIN} WHERE {clause} ORDER BY f.conflict_id IS NOT NULL DESC, f.path LIMIT ?", [*params, limit])]}


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
    renamed, problems, done = 0, [], []
    moved: dict[int, dict[str, str]] = {}
    touched: set[int] = set()
    waiting, blocked = rows, []
    while waiting:
        blocked = []
        for f in waiting:
            loc, to = locs.get(f["location_id"]), locs.get(f["target_location_id"])
            if not loc or not to or not f["target_path"]:
                continue
            if f["target_path"] == f["path"] and to["id"] == loc["id"]:
                continue
            root, dest_root = Path(loc["path"]).resolve(), Path(to["path"]).resolve()
            src, dest = root / f["path"], (dest_root / f["target_path"]).resolve()
            if dest_root not in dest.parents:
                problems.append({"id": f["id"], "path": f["path"], "reason": "The rule leads outside the location"})
                continue
            if not src.exists():
                problems.append({"id": f["id"], "path": f["path"], "reason": "The file is gone"})
                continue
            if dest.exists() and not (dest.samefile(src)):
                blocked.append(f)
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
                # The name is free on the disk, so an entry that still holds it is a deleted file.
                c.execute("DELETE FROM library_files WHERE location_id = ? AND path = ? AND id != ?",
                          [to["id"], f["target_path"], f["id"]])
                c.execute("UPDATE library_files SET path = ?, location_id = ? WHERE id = ?", [f["target_path"], to["id"], f["id"]])
                c.execute("UPDATE job_items SET file_path = ? WHERE file_path = ? AND job_id IN "
                          "(SELECT id FROM jobs WHERE location_id = ?)", [f["target_path"], f["path"], loc["id"]])
            if to["id"] == loc["id"]:
                moved.setdefault(loc["id"], {})[f["path"]] = f["target_path"]
            touched |= {loc["id"], to["id"]}
            done.append(f["id"])
            _prune(src.parent, root)
            renamed += 1
        if len(blocked) == len(waiting):  # nothing moved, so no name came free
            break
        waiting = blocked  # the file in the way may have moved on in the meantime
    problems += [{"id": f["id"], "path": f["path"], "target": f["target_path"], "taken": True,
                  "reason": "Another file already has that name"} for f in blocked]
    for lid, paths in moved.items():
        _fix_playlists(Path(locs[lid]["path"]), paths)
    refresh_targets(ids=done)
    return {"renamed": renamed, "problems": problems, "locations": sorted(touched)}


def rename(location_id: int | None = None, ids: list[int] | None = None) -> dict:
    where, params = [_moves_sql()], []
    if location_id:
        where.append("location_id = ?")
        params.append(location_id)
    if ids is not None:
        where.append(f"id IN ({','.join('?' * len(ids))})" if ids else "0")
        params += ids
    with _write_lock:
        return _rename_rows(db.rows(f"SELECT * FROM library_files WHERE {' AND '.join(where)} ORDER BY id", params))


# ---------------------------------------------------------------- browsing

def _with_conflict(f: dict) -> dict:
    """Replaces the conflict columns with what the web interface needs to name that file."""
    fid = f.pop("conflict_id", None)
    found = {k: f.pop(f"conflict_{k}", None) for k in ("path", "state", "location")}
    f["conflict"] = {"id": fid, **found} if fid and found["path"] else None
    return f


def _public(f: dict) -> dict:
    out = _with_conflict({**{k: f.get(k) for k in COLUMNS}, **{k: f.get(k) for k in f if k.startswith("conflict_")}})
    out["has_cover"], out["has_lyrics"] = bool(f.get("has_cover")), bool(f.get("has_lyrics"))
    out["issues"] = [i for i in (f.get("issues") or "").split(",") if i] + (["taken"] if out["conflict"] else [])
    out["target_path"] = f.get("target_path")
    out["location_name"] = f.get("location_name")
    out["target_location_name"] = f.get("target_location_name")
    out["profile_name"] = f.get("profile_name")
    return out


def _issue_sql(name: str) -> str | None:
    if name == "any":
        return "f.issues != ''"
    if name == "tags":
        return "(',' || f.issues) LIKE '%,tag:%'"
    if name == "taken":
        return "f.conflict_id IS NOT NULL"
    return f"(',' || f.issues || ',') LIKE '%,{name},%'" if name in ISSUES else None


def _filters(p: dict) -> tuple[str, list]:
    where, params = ["f.state = ?"], [p.get("state") if p.get("state") in ("new", "ignored") else "imported"]
    if p.get("deleted") == "only":
        where.append("f.deleted_at IS NOT NULL")
    elif p.get("deleted") == "no":
        where.append("f.deleted_at IS NULL")
    if p.get("folder") is not None and p.get("location_id"):
        if p["folder"]:
            where.append("substr(f.path, 1, length(?) + 1) = ? || '/' AND instr(substr(f.path, length(?) + 2), '/') = 0")
            params += [p["folder"]] * 3
        else:
            where.append("instr(f.path, '/') = 0")
    if p.get("q"):
        for word in str(p["q"]).split():
            like = f"%{word}%"
            where.append("(f.title LIKE ? OR f.artist LIKE ? OR f.album LIKE ? OR f.albumartist LIKE ? OR f.path LIKE ?)")
            params += [like] * 5
    for key in ("location_id", "format", "year", "profile_id"):
        if p.get(key):
            where.append(f"f.{key} LIKE ?" if key == "year" else f"f.{key} = ?")
            params.append(f"{p[key]}%" if key == "year" else p[key])
    for key in ("artist", "album", "genre"):
        if p.get(key):
            where.append(f"f.{key} = ? COLLATE NOCASE")
            params.append(p[key])
    if p.get("missing"):
        where.append("f.deleted_at IS NULL")
    for name in str(p.get("missing") or "").split(","):
        if name in MISSING:
            where.append(f"(f.{MISSING[name]} IS NULL OR f.{MISSING[name]} = '')")
        elif name == "cover":
            where.append("f.has_cover = 0")
        elif name == "lyrics":
            where.append("f.has_lyrics = 0")
    wanted = [sql for name in str(p.get("issue") or "").split(",") if (sql := _issue_sql(name))]
    if wanted:
        where.append("(" + " OR ".join(wanted) + ")")
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
    rows = db.rows(f"SELECT f.*, l.name AS location_name, t.name AS target_location_name, p.name AS profile_name, {CONFLICT_COLS} "
                   "FROM library_files f JOIN locations l ON l.id = f.location_id LEFT JOIN locations t ON t.id = f.target_location_id "
                   f"LEFT JOIN profiles p ON p.id = f.profile_id {CONFLICT_JOIN} {clause} "
                   f"ORDER BY {empties}, f.{sort}{collate} {order}, f.title COLLATE NOCASE, f.id LIMIT ? OFFSET ?", [*params, limit, offset])
    total = db.scalar(f"SELECT COUNT(*) FROM library_files f {clause}", params)
    return {"total": total, "items": [_public(r) for r in rows], "scan": {**state}}


def matching_ids(p: dict, cap: int = 30000) -> list[int]:
    clause, params = _filters(p)
    return [r["id"] for r in db.rows(f"SELECT f.id FROM library_files f {clause} LIMIT ?", [*params, cap])]


def facets() -> dict:
    def top(col: str, n: int = 400) -> list[dict]:
        return db.rows(f"SELECT {col} AS value, COUNT(*) AS n FROM library_files WHERE {LIVE} AND {col} IS NOT NULL AND {col} != '' "
                       f"GROUP BY {col} COLLATE NOCASE ORDER BY n DESC, value COLLATE NOCASE LIMIT ?", [n])

    def empty(col: str) -> int:
        return db.scalar(f"SELECT COUNT(*) FROM library_files WHERE {LIVE} AND ({col} IS NULL OR {col} = '')")

    def issue(name: str) -> int:
        return db.scalar(f"SELECT COUNT(*) FROM library_files f WHERE {_issue_sql(name)}")

    totals = db.row(f"SELECT COUNT(*) AS files, COALESCE(SUM(size), 0) AS bytes, COALESCE(SUM(duration), 0) AS seconds FROM library_files WHERE {LIVE}")
    return {
        **totals, "formats": top("format"), "genres": top("genre"), "artists": top("artist"), "albums": top("album"),
        "years": db.rows(f"SELECT substr(year, 1, 4) AS value, COUNT(*) AS n FROM library_files WHERE {LIVE} AND year IS NOT NULL AND year != '' "
                         "GROUP BY value ORDER BY value DESC"),
        "missing": {**{k: empty(k) for k in ("title", "artist", "album", "year", "genre")},
                    "cover": db.scalar(f"SELECT COUNT(*) FROM library_files WHERE {LIVE} AND has_cover = 0"),
                    "lyrics": db.scalar(f"SELECT COUNT(*) FROM library_files WHERE {LIVE} AND has_lyrics = 0")},
        "issues": {name: issue(name) for name in ("any", *ISSUES)},
        "moves": db.scalar(f"SELECT COUNT(*) FROM library_files WHERE {_moves_sql()}"),
        "deleted": db.scalar("SELECT COUNT(*) FROM library_files WHERE state = 'imported' AND deleted_at IS NOT NULL"),
        "new": db.scalar("SELECT COUNT(*) FROM library_files WHERE state = 'new'"),
        "ignored": db.scalar("SELECT COUNT(*) FROM library_files WHERE state = 'ignored'"),
        "profiles": db.rows(f"SELECT p.id, p.name, COUNT(f.id) AS n FROM profiles p LEFT JOIN library_files f ON f.profile_id = p.id "
                            f"AND f.state = 'imported' AND f.deleted_at IS NULL GROUP BY p.id ORDER BY p.name COLLATE NOCASE"),
        "locations": db.rows("SELECT l.id, l.name, COUNT(f.id) AS n FROM locations l LEFT JOIN library_files f ON f.location_id = l.id "
                             "AND f.state = 'imported' AND f.deleted_at IS NULL GROUP BY l.id ORDER BY l.name COLLATE NOCASE"),
        "scan": {**state},
    }


def _file(file_id: int) -> tuple[dict, Path]:
    f = db.row(f"SELECT f.*, l.name AS location_name, t.name AS target_location_name, l.path AS root, {CONFLICT_COLS} FROM library_files f "
               f"JOIN locations l ON l.id = f.location_id LEFT JOIN locations t ON t.id = f.target_location_id {CONFLICT_JOIN} WHERE f.id = ?", [file_id])
    if not f:
        raise ValueError("That file isn't in the library")
    return f, Path(f["root"]) / f["path"]


def path_of(file_id: int) -> Path:
    return _file(file_id)[1]


def detail(file_id: int) -> dict:
    f, path = _file(file_id)
    out = _public(f)
    out["tags"] = {k: f.get(k) for k in tags.FIELDS if k not in ("comment", "lyrics")}
    if f["deleted_at"]:
        return out
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
                if f["deleted_at"]:
                    raise ValueError("This file was deleted")
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
            rows = db.rows(f"SELECT * FROM library_files WHERE id IN ({','.join('?' * len(touched))}) AND {_moves_sql()}", touched)
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
    rows = db.rows("SELECT f.*, l.name AS location_name, l.path AS root FROM library_files f JOIN locations l ON l.id = f.location_id "
                   "WHERE f.state = 'imported' AND f.deleted_at IS NULL")
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
    refresh_conflicts()
    return {"deleted": deleted, "problems": problems}


def forget(ids: list[int]) -> dict:
    """Takes deleted files off the list. Files that are still there are left alone."""
    marks = ",".join("?" * len(ids))
    n = db.scalar(f"SELECT COUNT(*) FROM library_files WHERE deleted_at IS NOT NULL AND id IN ({marks})", ids)
    db.execute(f"DELETE FROM library_files WHERE deleted_at IS NOT NULL AND id IN ({marks})", ids)
    return {"forgotten": n}


# ---------------------------------------------------------------- importing

def folders(state_name: str = "new", limit: int = 500) -> dict:
    """What waits for an import, folder by folder: that's how a collection arrives."""
    groups: dict[tuple, dict] = {}
    rows = db.rows("SELECT f.location_id, l.name AS location_name, f.path, f.size, f.format, f.video_id FROM library_files f "
                   "JOIN locations l ON l.id = f.location_id WHERE f.state = ? ORDER BY f.location_id, f.path COLLATE NOCASE", [state_name])
    for r in rows:
        folder = os.path.dirname(r["path"])
        g = groups.setdefault((r["location_id"], folder), {
            "location_id": r["location_id"], "location_name": r["location_name"], "folder": folder,
            "files": 0, "bytes": 0, "formats": set(), "downloaded": 0})
        g["files"] += 1
        g["bytes"] += r["size"] or 0
        g["downloaded"] += bool(r["video_id"])
        if r["format"]:
            g["formats"].add(r["format"])
    items = [{**g, "formats": sorted(g["formats"])} for g in groups.values()]
    return {"folders": items[:limit], "more": max(0, len(items) - limit), "total": len(rows)}


def ids_of(ids: list[int] | None, picked: list[dict] | None, state_name: str) -> list[int]:
    """The files of a selection: single ids, whole folders, or both."""
    out = set(ids or [])
    for f in picked or []:
        out |= set(matching_ids({"state": state_name, "location_id": f.get("location_id"), "folder": f.get("folder") or ""}, 1000000))
    return sorted(out)


def _set_state(ids: list[int], fields: dict, where: str = "1") -> int:
    cols = ", ".join(f"{k} = ?" for k in fields)
    n = 0
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        n += db.scalar(f"SELECT COUNT(*) FROM library_files WHERE {where} AND id IN ({','.join('?' * len(part))})", part)
        db.execute(f"UPDATE library_files SET {cols} WHERE {where} AND id IN ({','.join('?' * len(part))})", [*fields.values(), *part])
    return n


def import_files(ids: list[int], profile_id: int) -> dict:
    """Nothing on the disk changes: the files join the library and their profile shows what's left to fix. Works for
    files already in the library too, to give them another profile."""
    if not db.row("SELECT id FROM profiles WHERE id = ?", [profile_id]):
        raise ValueError("Pick a profile")
    n = _set_state(ids, {"state": "imported", "profile_id": profile_id, "pinned": 0, "imported_at": db.now()}, "deleted_at IS NULL")
    refresh_targets(ids=ids)
    return {"imported": n}


def ignore_files(ids: list[int]) -> dict:
    return {"ignored": _set_state(ids, {"state": "ignored"}, "state = 'new'")}


def restore_files(ids: list[int]) -> dict:
    """Back to the Import tab, from the ignored files or the library."""
    n = _set_state(ids, {"state": "new", "profile_id": None, "pinned": 0, "imported_at": None, "issues": "",
                         "target_path": None, "target_location_id": None}, "state != 'new' AND deleted_at IS NULL")
    refresh_conflicts()
    return {"restored": n}


# ---------------------------------------------------------------- what's already there

def in_library(video_id: str | None, title: str | None, artist: str | None, duration: float | None = None) -> dict | None:
    """The file that already holds a song: the same YouTube video, or the same song by title, artist and length."""
    usable = "f.deleted_at IS NULL AND f.state != 'ignored'"
    head = ("SELECT f.path, f.format, f.title, f.artist, f.duration, l.name AS location FROM library_files f "
            f"JOIN locations l ON l.id = f.location_id WHERE {usable}")
    if video_id and (r := db.row(f"{head} AND f.video_id = ? LIMIT 1", [video_id])):
        return {"match": "video", "path": r["path"], "location": r["location"], "format": r["format"]}
    key = _song_key({"title": title, "artist": artist})
    if not key:
        return None
    plain = metadata.clean_title(title).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    for r in db.rows(f"{head} AND f.title LIKE ? ESCAPE '\\'", [f"{plain}%"]):  # the file may have noise behind the title
        close = not (duration and r["duration"]) or abs(duration - r["duration"]) <= SAME_SONG_SECONDS
        if close and _song_key(r) == key:
            return {"match": "song", "path": r["path"], "location": r["location"], "format": r["format"]}
    return None


# ---------------------------------------------------------------- helping a lookup

def lookup_song(d: dict) -> dict:
    """What a lookup can go on: the tags, and for a file without them its name and folders. Folders are only a hint
    for the search, they can be called anything; `from_name` lists what the name says clearly enough to be a tag."""
    song = {**d["tags"], "duration": d.get("duration"), "from_name": []}
    if song.get("title") and song.get("artist"):
        return song
    path = Path(d["path"])
    number = re.compile(r"^\s*\d{1,3}\s*[-._)]?\s+")
    stem = number.sub("", path.stem)
    artist, sep, title = stem.partition(" - ")
    if sep:
        song["from_name"] = [k for k in ("artist", "title") if not song.get(k)]
        song["artist"] = song.get("artist") or artist.strip()
        song["title"] = song.get("title") or number.sub("", title).strip()
    else:
        if song.get("artist") and not song.get("title"):
            song["from_name"] = ["title"]
        song["title"] = song.get("title") or stem.strip()
    folders_up = path.parts[:-1]
    if len(folders_up) >= 2:
        song["artist"] = song.get("artist") or folders_up[-2]
        song["album"] = song.get("album") or folders_up[-1]
    return song


def folder_cover(path: Path) -> Path | None:
    """cover.jpg and friends next to the file."""
    try:
        for p in sorted(path.parent.iterdir()):
            if p.stem.lower() in COVER_NAMES and p.suffix.lower() in COVER_EXT and p.is_file():
                return p
    except OSError:
        pass
    return None
