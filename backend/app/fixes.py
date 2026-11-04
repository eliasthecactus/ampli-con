"""Bringing files in line with their profile: tags and covers are looked up in the background, and what was accepted
gets written. Everything lives in fix_items, so a restart picks up where it stopped and a big library can take its time.
"""
import asyncio
import logging
from pathlib import Path

from . import db, files, locations, metadata, stream, tags
from .events import bus

log = logging.getLogger("amplicon.fixes")

FIELDS = ("title", "artist", "albumartist", "album", "track", "track_total", "disc", "disc_total", "year", "genre",
          "label", "isrc")
MINIMUM = 0.5  # below this a match is more likely wrong than right
BATCH = 8
ELIGIBLE = ("f.state = 'imported' AND f.deleted_at IS NULL "
            "AND ((',' || f.issues) LIKE '%,tag:%' OR (',' || f.issues || ',') LIKE '%,cover,%')")


def _where(group: str) -> tuple[str, list]:
    if group == "confident":
        return "status = 'ready' AND score >= ?", [metadata.CONFIDENT]
    if group == "check":
        return "status = 'ready' AND score < ?", [metadata.CONFIDENT]
    return "status = ?", [group]


def counts() -> dict:
    return {g: db.scalar(f"SELECT COUNT(*) FROM fix_items WHERE {_where(g)[0]}", _where(g)[1])
            for g in ("queued", "apply", "confident", "check", "none", "failed")}


def eligible() -> int:
    return db.scalar("SELECT COUNT(*) FROM library_files f WHERE " + ELIGIBLE)


def items(group: str, offset: int = 0, limit: int = 100) -> list[dict]:
    where, params = _where(group)
    out = []
    for r in db.rows(f"SELECT * FROM fix_items WHERE {where} ORDER BY score DESC, file_id LIMIT ? OFFSET ?", [*params, limit, offset]):
        out.append({"id": r["file_id"], "score": r["score"], "error": r["error"], **db.jload(r["proposal"], {})})
    return out


class Fixer:
    def __init__(self) -> None:
        self.wake: asyncio.Event | None = None
        self.task: asyncio.Task | None = None
        self.touched: set[int] = set()

    def start(self) -> None:
        self.wake = asyncio.Event()
        self.task = asyncio.create_task(self._loop())
        if db.scalar("SELECT COUNT(*) FROM fix_items WHERE status IN ('queued', 'apply')"):
            self.wake.set()

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()

    def _kick(self) -> None:
        if self.wake:
            self.wake.set()
        bus.publish("fix", counts())

    def queue(self, ids: list[int] | None = None, mode: str = "fill") -> int:
        """Files with missing tags or covers, or exactly these. mode: fill only what's empty, or replace with the match."""
        if ids is None:
            ids = [r["id"] for r in db.rows("SELECT f.id FROM library_files f WHERE " + ELIGIBLE)]
        spec = db.jdump({"mode": "all" if mode == "all" else "fill"})
        with db.tx() as c:
            for fid in ids:
                c.execute("INSERT OR REPLACE INTO fix_items (file_id, status, proposal, updated_at) VALUES (?, 'queued', ?, ?)",
                          [fid, spec, db.now()])
        self._kick()
        return len(ids)

    def accept(self, ids: list[int] | None = None, group: str | None = None) -> int:
        where, params = _where(group or "confident")
        if ids is not None:
            where = "status = 'ready' AND file_id IN (" + ",".join("?" * len(ids)) + ")"
            params = ids
        n = db.scalar(f"SELECT COUNT(*) FROM fix_items WHERE {where}", params)
        db.execute(f"UPDATE fix_items SET status = 'apply' WHERE {where}", params)
        self._kick()
        return n

    def discard(self, ids: list[int] | None = None) -> None:
        if ids is None:
            db.execute("DELETE FROM fix_items WHERE status != 'apply'")
        else:
            db.execute(f"DELETE FROM fix_items WHERE status != 'apply' AND file_id IN ({','.join('?' * len(ids))})", ids)
        bus.publish("fix", counts())

    # -------------------------------------------------------- the worker

    async def _loop(self) -> None:
        while True:
            await self.wake.wait()
            self.wake.clear()
            try:
                await self.drain()
            except Exception:  # noqa: BLE001
                log.exception("fixing files")

    async def drain(self) -> None:
        while True:
            rows = db.rows("SELECT * FROM fix_items WHERE status IN ('queued', 'apply') ORDER BY status = 'apply' DESC, file_id LIMIT ?", [BATCH])
            if not rows:
                break
            await asyncio.gather(*(self._find(r) for r in rows if r["status"] == "queued"))
            if writes := [r for r in rows if r["status"] == "apply"]:
                await self._write(writes)
            bus.publish("fix", counts())
        for lid in sorted(self.touched):
            if loc := locations.get(lid):
                await locations.trigger_scan(loc)
        self.touched.clear()
        bus.publish("library", {**files.state})

    async def _find(self, row: dict) -> None:
        fid = row["file_id"]
        try:
            d = await asyncio.to_thread(files.detail, fid)
            if d["deleted_at"]:
                db.execute("DELETE FROM fix_items WHERE file_id = ?", [fid])
                return
            mode = db.jload(row["proposal"], {}).get("mode", "fill")
            before = {k: d["tags"].get(k) for k in FIELDS}
            song = files.lookup_song(d)
            found = await metadata.lookup(song, 1)
            best = found[0] if found and found[0].get("score", 0) >= MINIMUM else None
            after = dict(before)
            for k in song["from_name"]:
                after[k] = song[k]
            for k in FIELDS:
                if best and best.get(k) not in (None, "", 0) and (mode == "all" or not before[k]):
                    after[k] = best[k]
            art = None
            has_folder_cover = False
            if not d["has_cover"]:
                path = files.path_of(fid)
                has_folder_cover = await asyncio.to_thread(files.folder_cover, path) is not None
                art = None if has_folder_cover else (best or {}).get("cover")
            changed = [k for k in FIELDS if (after[k] or None) != (before[k] or None)]
            proposal = {"path": d["path"], "before": before, "after": after, "changed": changed, "cover": art,
                        "folder_cover": has_folder_cover, "source": (best or {}).get("source") or "file name"}
            useful = changed or art or has_folder_cover
            lacking = any(i == "cover" or i.startswith("tag:") for i in d["issues"])
            if best and not useful and not lacking:  # found and already right: there's nothing to report
                db.execute("DELETE FROM fix_items WHERE file_id = ?", [fid])
                return
            db.execute("UPDATE fix_items SET status = ?, score = ?, proposal = ?, updated_at = ? WHERE file_id = ?",
                       ["ready" if useful else "none", (best or {}).get("score", 0), db.jdump(proposal), db.now(), fid])
        except Exception as e:  # noqa: BLE001 - one bad file doesn't stop the others
            log.warning("looking up file %s: %s", fid, e)
            db.execute("UPDATE fix_items SET status = 'failed', error = ?, updated_at = ? WHERE file_id = ?", [str(e)[:200], db.now(), fid])

    async def _cover(self, fid: int, p: dict) -> bytes | None:
        if p.get("folder_cover"):
            art = await asyncio.to_thread(files.folder_cover, files.path_of(fid))
            return await asyncio.to_thread(tags.square_jpeg, Path(art).read_bytes()) if art else None
        url = metadata.hires(p.get("cover"))
        if not url or not metadata.cover_allowed(url):
            return None
        r = await stream.http().get(url)
        r.raise_for_status()
        return await asyncio.to_thread(tags.square_jpeg, r.content)

    async def _write(self, rows: list[dict]) -> None:
        changes = []
        for row in rows:
            p = db.jload(row["proposal"], {})
            try:
                cover = await self._cover(row["file_id"], p)
            except Exception as e:  # noqa: BLE001 - the tags are still worth writing
                log.warning("cover of file %s: %s", row["file_id"], e)
                cover = None
            changes.append({"id": row["file_id"], "fields": {k: p["after"].get(k) for k in p.get("changed", [])}, "cover": cover})
        out = await asyncio.to_thread(files.apply_changes, changes)
        self.touched |= set(out["locations"])
        # A rename that failed shows as a wrong name in the library; only a file that couldn't be written stays here.
        failed = {p["id"]: p["reason"] for p in out["problems"] if "path" not in p}
        for row in rows:
            if row["file_id"] in failed:
                db.execute("UPDATE fix_items SET status = 'failed', error = ?, updated_at = ? WHERE file_id = ?",
                           [failed[row["file_id"]], db.now(), row["file_id"]])
            else:
                db.execute("DELETE FROM fix_items WHERE file_id = ?", [row["file_id"]])


manager = Fixer()
