"""The download queue: songs, albums and playlists from YouTube Music into a location, named by a profile.

A job is what someone asked for (one song, an album, a playlist). It is expanded into items (one per song),
and a small pool of workers downloads items one after another across all jobs, at most
`max_parallel_downloads` at a time.
"""
import asyncio
import base64
import binascii
import json
import logging
import os
import shutil
import time
from pathlib import Path

from . import config, db, files, locations, metadata, profiles, settings_store, tags, youtube, ytdlp
from .events import bus
from .stream import http

log = logging.getLogger("amplicon.downloads")

KINDS = ("song", "album", "playlist")
ACTIVE = ("preparing", "review", "queued", "running")
EXT = {"m4a": "m4a", "opus": "opus", "mp3": "mp3"}
REVIEW_FIELDS = ("title", "artist", "albumartist", "album", "track", "track_total", "disc", "disc_total", "year", "genre",
                 "label", "isrc")
MAX_COVER = 15 * 1024 * 1024


class DownloadError(Exception):
    pass


# ---------------------------------------------------------------- views

def job_public(j: dict, items: bool = False) -> dict:
    out = {k: j[k] for k in ("id", "kind", "source_id", "title", "subtitle", "thumbnail", "profile_id", "profile_name",
                             "location_name", "status", "total", "done", "skipped", "failed", "error", "requested_by",
                             "created_at", "started_at", "finished_at")}
    out["review"] = bool(j.get("review"))
    finished = (j["done"] or 0) + (j["skipped"] or 0) + (j["failed"] or 0)
    running = db.rows("SELECT id, title, artist, progress FROM job_items WHERE job_id = ? AND status = 'downloading' "
                      "ORDER BY position", [j["id"]])
    partial = sum(r["progress"] for r in running)
    out["progress"] = round((finished + partial) / j["total"], 4) if j["total"] else (1.0 if j["status"] == "done" else 0.0)
    out["current"] = running
    if items:
        out["items"] = [item_public(i) for i in db.rows("SELECT * FROM job_items WHERE job_id = ? ORDER BY position", [j["id"]])]
    return out


def item_public(i: dict) -> dict:
    return {k: i[k] for k in ("id", "job_id", "position", "video_id", "title", "artist", "album", "status", "progress",
                              "error", "file_path")}


def get_job(job_id: int) -> dict | None:
    return db.row("SELECT * FROM jobs WHERE id = ?", [job_id])


def list_jobs(active_only: bool = False, limit: int = 100) -> list[dict]:
    if active_only:
        js = db.rows(f"SELECT * FROM jobs WHERE status IN ({','.join('?' * len(ACTIVE))}) ORDER BY id", ACTIVE)
    else:
        js = db.rows("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", [limit])
    return [job_public(j) for j in js]


def _publish_job(job_id: int) -> None:
    j = get_job(job_id)
    if j:
        bus.publish("job", job_public(j))


# ---------------------------------------------------------------- manager

class Manager:
    def __init__(self) -> None:
        self.wake = asyncio.Event()
        self.running: dict[int, asyncio.Task] = {}   # item id → task
        self.preparing: dict[int, asyncio.Task] = {}  # job id → task
        self.loop_task: asyncio.Task | None = None
        self._last_progress: dict[int, float] = {}

    def start(self) -> None:
        self.wake = asyncio.Event()  # bound to the running loop
        self.running.clear()
        self.preparing.clear()
        # Downloads interrupted by a restart start over; jobs still being expanded are expanded again.
        db.execute("UPDATE job_items SET status = 'queued', progress = 0 WHERE status = 'downloading'")
        db.execute("UPDATE jobs SET status = 'queued' WHERE status = 'running'")
        for j in db.rows("SELECT id FROM jobs WHERE status = 'preparing'"):
            self.preparing[j["id"]] = asyncio.create_task(self._prepare(j["id"]))
        self.loop_task = asyncio.create_task(self._loop())
        self.wake.set()

    async def stop(self) -> None:
        for t in [*self.running.values(), *self.preparing.values(), *( [self.loop_task] if self.loop_task else [])]:
            t.cancel()
        await asyncio.gather(*self.running.values(), *self.preparing.values(), return_exceptions=True)

    # -------------------------------------------------------- requests

    async def enqueue(self, kind: str, source_id: str, profile_id: int | None, requested_by: str,
                      title: str | None = None, subtitle: str | None = None, thumbnail: str | None = None,
                      location_id: int | None = None, review: bool = False) -> dict:
        if kind not in KINDS:
            raise ValueError("Unknown download kind")
        source_id = source_id.strip()
        if kind == "song":
            from .stream import check_id
            check_id(source_id)
        profile = profiles.get(profile_id)
        if not profile:
            raise ValueError("There's no download profile yet. Create one in Amplicon's web interface.")
        loc = locations.get(location_id or profile["location_id"])
        if not loc:
            raise ValueError("That location doesn't exist" if location_id else f"The profile {profile['name']} has no location")
        if not Path(loc["path"]).is_dir():
            raise ValueError(f"The location {loc['name']} ({loc['path']}) isn't there. Is the volume mounted?")
        # The same thing asked for twice while the first is still going: hand back the running job.
        dup = db.row(f"SELECT * FROM jobs WHERE kind = ? AND source_id = ? AND profile_id = ? AND location_id = ? AND status IN "
                     f"({','.join('?' * len(ACTIVE))})", [kind, source_id, profile["id"], loc["id"], *ACTIVE])
        if dup:
            return job_public(dup)
        job_id = db.insert("jobs", {
            "kind": kind, "source_id": source_id, "title": title or source_id, "subtitle": subtitle,
            "thumbnail": thumbnail, "profile_id": profile["id"], "profile_name": profile["name"],
            "location_id": loc["id"], "location_name": loc["name"], "status": "preparing", "requested_by": requested_by,
            "created_at": db.now(), "review": int(bool(review and settings_store.get("metadata_review"))),
        })
        self.preparing[job_id] = asyncio.create_task(self._prepare(job_id))
        _publish_job(job_id)
        return job_public(get_job(job_id))

    def cancel(self, job_id: int) -> None:
        j = get_job(job_id)
        if not j:
            raise ValueError("Job not found")
        if j["status"] not in ACTIVE:
            return
        db.execute("UPDATE job_items SET status = 'cancelled' WHERE job_id = ? AND status IN ('queued', 'downloading')", [job_id])
        db.update("jobs", job_id, {"status": "cancelled", "finished_at": db.now()})
        if t := self.preparing.pop(job_id, None):
            t.cancel()
        for i in db.rows("SELECT id FROM job_items WHERE job_id = ?", [job_id]):
            if t := self.running.get(i["id"]):
                t.cancel()
        _publish_job(job_id)

    def retry(self, job_id: int) -> None:
        j = get_job(job_id)
        if not j:
            raise ValueError("Job not found")
        if j["status"] in ACTIVE:
            return
        if not j["total"]:
            db.update("jobs", job_id, {"status": "preparing", "error": None, "finished_at": None})
            self.preparing[job_id] = asyncio.create_task(self._prepare(job_id))
        else:
            db.execute("UPDATE job_items SET status = 'queued', progress = 0, error = NULL WHERE job_id = ? "
                       "AND status IN ('failed', 'cancelled')", [job_id])
            self._recount(job_id)
            db.update("jobs", job_id, {"status": "queued", "error": None, "finished_at": None})
            self.wake.set()
        _publish_job(job_id)

    def remove(self, job_id: int) -> None:
        j = get_job(job_id)
        if j and j["status"] in ACTIVE:
            self.cancel(job_id)
        db.execute("DELETE FROM jobs WHERE id = ?", [job_id])
        _drop_covers(job_id)
        bus.publish("job_removed", {"id": job_id})

    def clear_finished(self) -> int:
        marks = ",".join("?" * len(ACTIVE))
        n = db.scalar(f"SELECT COUNT(*) FROM jobs WHERE status NOT IN ({marks})", ACTIVE)
        db.execute(f"DELETE FROM jobs WHERE status NOT IN ({marks})", ACTIVE)
        bus.publish("jobs_cleared", {})
        return n

    # -------------------------------------------------------- expanding

    async def _prepare(self, job_id: int) -> None:
        j = get_job(job_id)
        try:
            items, head = await self._expand(j["kind"], j["source_id"])
            if not items:
                raise DownloadError("Nothing to download (all songs are unavailable)")
            with db.tx() as c:
                c.execute("DELETE FROM job_items WHERE job_id = ?", [job_id])
                for pos, it in enumerate(items, start=1):
                    c.execute("INSERT INTO job_items (job_id, position, video_id, title, artist, album, album_id, meta, "
                              "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              [job_id, pos, it["id"], it["title"], it.get("artist"), it.get("album"), it.get("album_id"),
                               db.jdump(it), db.now()])
                c.execute("UPDATE jobs SET status = ?, total = ?, done = 0, skipped = 0, failed = 0, title = ?, "
                          "subtitle = ?, thumbnail = COALESCE(?, thumbnail), params = ? WHERE id = ? AND status = 'preparing'",
                          ["preparing" if j["review"] else "queued", len(items), head.get("title") or j["title"],
                           head.get("subtitle") or j["subtitle"], head.get("thumbnail"), db.jdump(head), job_id])
            if j["review"]:
                _publish_job(job_id)
                await self._prepare_review(job_id)
                db.execute("UPDATE jobs SET status = 'review' WHERE id = ? AND status = 'preparing'", [job_id])
            else:
                self.wake.set()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("job %s: %s", job_id, e)
            db.update("jobs", job_id, {"status": "failed", "error": str(e)[:500], "finished_at": db.now()})
        finally:
            self.preparing.pop(job_id, None)
            _publish_job(job_id)

    async def _expand(self, kind: str, source_id: str) -> tuple[list[dict], dict]:
        if kind == "song":
            s = await youtube.song_details(source_id)
            return [s], {"title": s["title"], "subtitle": s.get("artist"), "thumbnail": s.get("thumbnail")}
        if kind == "album":
            a = await youtube.album(source_id)
            tracks = [t for t in a["tracks"] if t["available"]]
            return tracks, {"title": a["title"], "subtitle": a["artist"], "thumbnail": a["thumbnail"], "album": a["title"],
                            "albumartist": a["artist"], "year": a.get("year")}
        p = await youtube.playlist(source_id)
        tracks = [t for t in p["tracks"] if t["available"]]
        return tracks, {"title": p["title"], "subtitle": p.get("author"), "thumbnail": p.get("thumbnail"),
                        "playlist": p["title"]}

    # -------------------------------------------------------- review before the download

    async def _prepare_review(self, job_id: int) -> None:
        """Looks every song up and keeps what was found on its item, so the review survives a restart."""
        j = get_job(job_id)
        head = db.jload(j["params"], {})
        rows = db.rows("SELECT * FROM job_items WHERE job_id = ? ORDER BY position", [job_id])
        bases = []
        for item in rows:
            base = await self._metadata(item, j)
            song = {k: base.get(k) for k in REVIEW_FIELDS}
            song["album"] = base["album"] if base.get("has_album") else ""
            song["cover"] = base.get("thumbnail")
            song["duration"] = db.jload(item["meta"], {}).get("duration")
            bases.append(song)

        found: list[list[dict]] = [[] for _ in rows]
        albums: list[dict] = []
        if j["kind"] == "album":
            albums = await metadata.lookup_album(head.get("albumartist") or j["subtitle"] or "", head.get("album") or j["title"], bases)
            for a in albums:
                a["songs"] = {str(rows[i]["id"]): c for i, c in a.pop("tracks").items()}
            if albums:
                for item, out in zip(rows, found):
                    if c := albums[0]["songs"].get(str(item["id"])):
                        out.append({**c, "score": albums[0]["score"]})
        else:
            gate = asyncio.Semaphore(4)
            details = 1 if j["kind"] == "playlist" else 3

            async def one(i: int) -> None:
                async with gate:
                    found[i] = await metadata.lookup(bases[i], details)
            await asyncio.gather(*(one(i) for i in range(len(rows))))

        extra = None
        if j["kind"] != "playlist":  # Apple's limit is too low for a look-up per song of a long playlist
            first = bases[0]
            extra = await metadata.itunes_cover(first.get("albumartist") or first.get("artist") or "", first.get("album") or "")

        for item, song, candidates in zip(rows, bases, found):
            best = candidates[0] if candidates else None
            confident = bool(best and best.get("score", 0) >= metadata.CONFIDENT)
            suggested = metadata.apply(song, best) if confident else dict(song)
            covers = [{"source": "youtube", "url": song.get("cover")}] if song.get("cover") else []
            covers += [{"source": c["source"], "url": c["cover"]} for c in candidates if c.get("cover")]
            if extra:
                covers.append({"source": "itunes", "url": extra})
                suggested["cover"] = extra
            seen: set[str] = set()
            covers = [c for c in covers if not (c["url"] in seen or seen.add(c["url"]))]
            m = db.jload(item["meta"], {})
            m["review"] = {"original": song, "suggested": suggested, "candidates": candidates, "covers": covers,
                           "confidence": best.get("score", 0) if best else 0, "confident": confident}
            db.update("job_items", item["id"], {"meta": db.jdump(m)})
        if albums:
            db.update("jobs", job_id, {"params": db.jdump({**head, "album_candidates": albums})})

    def _paths(self, j: dict, item: dict, fields: dict) -> tuple[str | None, bool]:
        """Where a song would end up with these tags, and whether that place is taken."""
        profile = db.row("SELECT * FROM profiles WHERE id = ?", [j["profile_id"]]) or profiles.get(None)
        loc = locations.get(j["location_id"] or (profile and profile["location_id"]))
        if not profile or not loc:
            return None, False
        meta = _override_meta(item, j, fields)
        try:
            rel = target_path(profile, j, meta) + "." + EXT[profile["format"]]
        except ValueError:
            return None, False
        root = Path(loc["path"])
        return rel, (root / rel).exists() or bool(already_downloaded(item["video_id"], root, profile["format"]))

    def review(self, job_id: int) -> dict:
        j = get_job(job_id)
        if not j:
            raise ValueError("Job not found")
        songs = []
        for item in db.rows("SELECT * FROM job_items WHERE job_id = ? ORDER BY position", [job_id]):
            r = db.jload(item["meta"], {}).get("review") or {}
            suggested = r.get("suggested") or {}
            path, exists = self._paths(j, item, suggested)
            songs.append({"item_id": item["id"], "position": item["position"], "video_id": item["video_id"],
                          "original": r.get("original") or {}, "suggested": suggested, "candidates": r.get("candidates") or [],
                          "covers": r.get("covers") or [], "confidence": r.get("confidence") or 0,
                          "confident": bool(r.get("confident")), "path": path, "exists": exists})
        params = db.jload(j["params"], {})
        return {"job": job_public(j), "songs": songs, "albums": params.get("album_candidates") or []}

    def review_item(self, job_id: int, item_id: int) -> tuple[dict, dict]:
        j = get_job(job_id)
        item = db.row("SELECT * FROM job_items WHERE id = ? AND job_id = ?", [item_id, job_id])
        if not j or not item:
            raise ValueError("That song isn't in this download")
        return j, item

    def confirm(self, job_id: int, shared: dict, songs: list[dict], cover: dict | None) -> dict:
        """Starts a reviewed download. What's sent wins over the suggestions; a song that isn't sent keeps them."""
        j = get_job(job_id)
        if not j:
            raise ValueError("Job not found")
        if j["status"] != "review":
            raise ValueError("This download isn't waiting for a review")
        by_item = {int(s["item_id"]): s for s in songs}
        shared = {k: v for k, v in (shared or {}).items() if k in REVIEW_FIELDS}
        common = _store_cover(job_id, "all", cover) if cover else None
        kept = 0
        for item in db.rows("SELECT * FROM job_items WHERE job_id = ?", [job_id]):
            s = by_item.get(item["id"], {})
            if s.get("skip"):
                db.execute("DELETE FROM job_items WHERE id = ?", [item["id"]])
                continue
            m = db.jload(item["meta"], {})
            suggested = (m.get("review") or {}).get("suggested") or {}
            fields = {**{k: suggested.get(k) for k in REVIEW_FIELDS}, **shared,
                      **{k: v for k, v in (s.get("fields") or {}).items() if k in REVIEW_FIELDS}}
            if not str(fields.get("title") or "").strip():
                raise ValueError(f"{item['title']} needs a title")
            art = (_store_cover(job_id, str(item["id"]), s["cover"]) if s.get("cover") else common) or {"url": suggested.get("cover")}
            m["override"] = {**fields, **art}
            m.pop("review", None)
            title, artist = str(fields["title"]).strip(), str(fields.get("artist") or "").strip()
            db.update("job_items", item["id"], {"meta": db.jdump(m), "title": title, "artist": artist or None,
                                                "album": fields.get("album") or None})
            kept += 1
        if not kept:
            self.cancel(job_id)
            return job_public(get_job(job_id))
        db.update("jobs", job_id, {"status": "queued", "total": kept})
        self.wake.set()
        _publish_job(job_id)
        return job_public(get_job(job_id))

    # -------------------------------------------------------- worker loop

    async def _loop(self) -> None:
        while True:
            await self.wake.wait()
            self.wake.clear()
            limit = max(1, int(settings_store.get("max_parallel_downloads") or 1))
            while len(self.running) < limit:
                busy = list(self.running) or [0]
                item = db.row(
                    "SELECT ji.* FROM job_items ji JOIN jobs j ON j.id = ji.job_id WHERE ji.status = 'queued' "
                    f"AND j.status IN ('queued', 'running') AND ji.id NOT IN ({','.join('?' * len(busy))}) "
                    "ORDER BY j.id, ji.position LIMIT 1", busy)
                if not item:
                    break
                db.update("job_items", item["id"], {"status": "downloading", "progress": 0, "updated_at": db.now()})
                j = get_job(item["job_id"])
                if j["status"] == "queued":
                    db.update("jobs", j["id"], {"status": "running", "started_at": j["started_at"] or db.now()})
                    _publish_job(j["id"])
                task = asyncio.create_task(self._run_item(item))
                self.running[item["id"]] = task
                task.add_done_callback(lambda _t, iid=item["id"]: self._item_finished(iid))

    def _item_finished(self, item_id: int) -> None:
        self.running.pop(item_id, None)
        self._last_progress.pop(item_id, None)
        self.wake.set()

    async def _run_item(self, item: dict) -> None:
        job_id = item["job_id"]
        try:
            status, path, error = await self._download(item)
        except asyncio.CancelledError:
            db.update("job_items", item["id"], {"status": "cancelled", "updated_at": db.now()})
            self._recount(job_id)
            self._maybe_finish(job_id)
            raise
        except Exception as e:  # noqa: BLE001 - one broken song must not stop the queue
            log.exception("item %s failed", item["id"])
            status, path, error = "failed", None, str(e)[:500]
        db.update("job_items", item["id"], {"status": status, "file_path": path, "error": error,
                                            "progress": 1.0 if status in ("done", "skipped") else 0,
                                            "updated_at": db.now()})
        bus.publish("item", {"job_id": job_id, "id": item["id"], "status": status, "progress": 1.0 if status == "done" else 0,
                             "title": item["title"], "error": error})
        self._recount(job_id)
        self._maybe_finish(job_id)

    def _recount(self, job_id: int) -> None:
        c = {r["status"]: r["n"] for r in db.rows(
            "SELECT status, COUNT(*) AS n FROM job_items WHERE job_id = ? GROUP BY status", [job_id])}
        db.update("jobs", job_id, {"done": c.get("done", 0), "skipped": c.get("skipped", 0), "failed": c.get("failed", 0)})
        _publish_job(job_id)

    def _maybe_finish(self, job_id: int) -> None:
        open_items = db.scalar("SELECT COUNT(*) FROM job_items WHERE job_id = ? AND status IN ('queued', 'downloading')", [job_id])
        j = get_job(job_id)
        if open_items or not j or j["status"] not in ("queued", "running"):
            return
        status = "done" if not j["failed"] else ("partial" if j["done"] or j["skipped"] else "failed")
        db.update("jobs", job_id, {"status": status, "finished_at": db.now(),
                                   "error": None if status == "done" else f"{j['failed']} of {j['total']} songs failed"})
        _publish_job(job_id)
        asyncio.create_task(self._after_job(job_id))

    async def _after_job(self, job_id: int) -> None:
        j = get_job(job_id)
        profile = db.row("SELECT * FROM profiles WHERE id = ?", [j["profile_id"]]) if j else None
        loc = locations.get(j["location_id"] or (profile and profile["location_id"])) if j else None
        if not j or not loc:
            return
        try:
            if j["kind"] == "playlist" and profile["write_m3u"]:
                write_m3u(j, loc)
        except OSError as e:
            log.warning("m3u for job %s: %s", job_id, e)
        if not j["failed"]:
            _drop_covers(job_id)
        if j["done"]:
            if err := await locations.trigger_scan(loc):
                log.warning(err)
                db.update("jobs", job_id, {"error": err})
                _publish_job(job_id)

    # -------------------------------------------------------- one song

    def _progress(self, item: dict, pct: float) -> None:
        now = time.monotonic()
        if now - self._last_progress.get(item["id"], 0) < 0.5 and pct < 1:
            return
        self._last_progress[item["id"]] = now
        db.update("job_items", item["id"], {"progress": pct})
        bus.publish("progress", {"job_id": item["job_id"], "id": item["id"], "title": item["title"],
                                 "artist": item["artist"], "progress": round(pct, 3)})

    async def _download(self, item: dict) -> tuple[str, str | None, str | None]:
        j = get_job(item["job_id"])
        profile = db.row("SELECT * FROM profiles WHERE id = ?", [j["profile_id"]]) or profiles.get(None)
        loc = locations.get(j["location_id"] or (profile and profile["location_id"]))
        if not profile or not loc:
            return "failed", None, "The profile or its location was deleted"
        root = Path(loc["path"])
        if not root.is_dir():
            return "failed", None, f"The location {loc['name']} isn't mounted"

        meta = await self._metadata(item, j)
        fmt = profile["format"]
        rel = target_path(profile, j, meta) + "." + EXT[fmt]
        dest = (root / rel).resolve()
        if root.resolve() not in dest.parents:
            return "failed", None, "The file name template leads outside the location"

        if dest.exists() and profile["on_exists"] == "skip":
            return "skipped", rel, None
        if profile["on_exists"] == "skip" and (same := already_downloaded(item["video_id"], root, fmt)):
            return "skipped", str(same.relative_to(root)), None

        work = config.TMP_DIR / "downloads" / f"item-{item['id']}"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            produced = await self._fetch(item, profile, work)
            cover = await self._cover(meta) if profile["embed_cover"] else None
            lyrics = await youtube.lyrics(item["video_id"]) if profile["embed_lyrics"] else None
            await asyncio.to_thread(tags.write, produced, meta, cover, lyrics)
            dest.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.move, str(produced), str(dest))
            try:
                await asyncio.to_thread(files.index_file, loc, rel, profile["id"], meta.get("playlist"),
                                        meta.get("position") if meta.get("playlist") else None)
            except Exception:  # noqa: BLE001 - the next scan picks it up
                log.exception("indexing %s", rel)
            self._progress(item, 1.0)
            return "done", rel, None
        finally:
            shutil.rmtree(work, ignore_errors=True)

    async def _metadata(self, item: dict, j: dict) -> dict:
        m = db.jload(item["meta"], {})
        if m.get("override"):
            return _override_meta(item, j, m["override"])
        head = db.jload(j["params"], {})
        album_id = m.get("album_id")
        info: dict = {}
        if album_id:
            try:
                info = await youtube.album(album_id)
            except youtube.YouTubeError:
                info = {}
        track = m.get("track")
        if info and not track:
            track = next((t["track"] for t in info.get("tracks", []) if t["id"] == item["video_id"]), None)
        album = info.get("title") or m.get("album")
        artist = m.get("artist") or info.get("artist") or ""
        return {
            "video_id": item["video_id"], "title": m.get("title") or item["title"], "artist": artist,
            "albumartist": info.get("artist") or head.get("albumartist") or artist.split(",")[0].strip(),
            "album": album or m.get("title") or item["title"], "has_album": bool(album),
            "track": track, "track_total": info.get("track_count"), "disc": 1, "disc_total": 1,
            "year": info.get("year") or head.get("year"), "playlist": head.get("playlist"),
            "position": item["position"], "thumbnail": info.get("thumbnail") or m.get("thumbnail"),
        }

    async def _cover(self, meta: dict) -> bytes | None:
        if meta.get("cover_file"):
            try:
                return await asyncio.to_thread(Path(meta["cover_file"]).read_bytes)
            except OSError as e:
                log.info("cover file of %s: %s", meta["video_id"], e)
        url = metadata.hires(meta.get("thumbnail"))
        if not url:
            return None
        try:
            r = await http().get(url)
            r.raise_for_status()
            return await asyncio.to_thread(tags.square_jpeg, r.content)
        except Exception as e:  # noqa: BLE001 - a missing cover is no reason to fail
            log.info("cover for %s: %s", meta["video_id"], e)
            return None

    async def _fetch(self, item: dict, profile: dict, work: Path) -> Path:
        fmt = profile["format"]
        args = ["--newline", "--progress", "--no-playlist", "--no-mtime", "--no-warnings",
                "--progress-template", "download:" + ytdlp.PROG +
                "%(progress.{status,downloaded_bytes,total_bytes,total_bytes_estimate})j",
                "--print", "after_move:" + ytdlp.DONE + "%(.{filepath})j",
                "-P", "home:" + str(work), "-P", "temp:" + str(work / "tmp"), "-o", "%(id)s.%(ext)s", "-x"]
        if fmt == "m4a":
            args += ["-f", "140/bestaudio[ext=m4a]/bestaudio", "--audio-format", "m4a"]
        elif fmt == "opus":
            args += ["-f", "251/bestaudio[acodec=opus]/bestaudio", "--audio-format", "opus"]
        else:
            q = profile["quality"]
            args += ["-f", "bestaudio", "--audio-format", "mp3",
                     "--audio-quality", q[1:] if q.startswith("V") else f"{q}K"]
        if profile["sponsorblock"]:
            args += ["--sponsorblock-remove", "music_offtopic"]

        def on_line(stream: str, line: str) -> None:
            if line.startswith(ytdlp.PROG):
                try:
                    p = json.loads(line[len(ytdlp.PROG):])
                    total = p.get("total_bytes") or p.get("total_bytes_estimate")
                    if total and p.get("downloaded_bytes") is not None:
                        # The last 5 % are for converting and tagging.
                        self._progress(item, min(0.95, 0.95 * p["downloaded_bytes"] / total))
                except (ValueError, TypeError):
                    pass

        url = f"https://music.youtube.com/watch?v={item['video_id']}"
        last_error = "yt-dlp failed"
        for attempt in range(2):
            use_account = attempt == 1 or bool(settings_store.get("stream_with_account"))
            if attempt == 1 and not youtube.account():
                break
            async with youtube.cookies_file(use_account) as cf:
                res = await ytdlp.run(ytdlp.base_args(cf) + args + ["--", url], on_line=on_line, timeout=1800)
            if res.returncode == 0 and res.done:
                path = Path(res.done[-1]["filepath"])
                if path.exists():
                    return path
            last_error = res.error_text or last_error
            if ytdlp.classify(last_error) == "unavailable":
                break
        raise DownloadError(last_error.removeprefix("ERROR: ")[:400])


manager: Manager = Manager()


# ---------------------------------------------------------------- reviewed data

def _number(v) -> int | None:
    try:
        return int(v) or None
    except (TypeError, ValueError):
        return None


def _override_meta(item: dict, j: dict, fields: dict) -> dict:
    """The tags of a song as they were confirmed in the review."""
    head = db.jload(j["params"], {})
    title = str(fields.get("title") or item["title"]).strip()
    artist = str(fields.get("artist") or "").strip()
    album = str(fields.get("album") or "").strip()
    return {
        "video_id": item["video_id"], "title": title, "artist": artist,
        "albumartist": str(fields.get("albumartist") or "").strip() or artist.split(",")[0].strip(),
        "album": album or title, "has_album": bool(album), "track": _number(fields.get("track")),
        "track_total": _number(fields.get("track_total")), "disc": _number(fields.get("disc")) or 1,
        "disc_total": _number(fields.get("disc_total")) or 1, "year": str(fields.get("year") or "").strip() or None,
        "genre": str(fields.get("genre") or "").strip() or None, "label": str(fields.get("label") or "").strip() or None,
        "isrc": str(fields.get("isrc") or "").strip() or None, "playlist": head.get("playlist"), "position": item["position"],
        "thumbnail": fields.get("url") or fields.get("cover"), "cover_file": fields.get("file"),
    }


def _covers_dir() -> Path:
    d = config.CACHE_DIR / "review-covers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _store_cover(job_id: int, key: str, spec: dict) -> dict:
    """{url} from a source, or {data} (base64) uploaded by the person reviewing."""
    if spec.get("data"):
        return {"file": str(save_cover(_covers_dir() / f"{job_id}-{key}.jpg", spec["data"]))}
    url = spec.get("url")
    if not metadata.cover_allowed(url):
        raise ValueError("Covers can only come from the suggestions or an upload")
    return {"url": url}


def decode_cover(data: str) -> bytes:
    raw = data.split(",", 1)[1] if data.startswith("data:") else data
    try:
        blob = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError("That image couldn't be read") from e
    if len(blob) > MAX_COVER:
        raise ValueError("That image is too large (15 MB at most)")
    try:
        return tags.square_jpeg(blob)
    except Exception as e:  # noqa: BLE001 - Pillow raises many kinds
        raise ValueError("That file isn't an image") from e


def save_cover(path: Path, data: str) -> Path:
    path.write_bytes(decode_cover(data))
    return path


def _drop_covers(job_id: int) -> None:
    for f in _covers_dir().glob(f"{job_id}-*.jpg"):
        f.unlink(missing_ok=True)


# ---------------------------------------------------------------- paths

def target_path(profile: dict, job: dict, meta: dict) -> str:
    if job["kind"] == "playlist" and profile["playlist_layout"] == "playlist":
        template = profile["playlist_template"]
    elif meta.get("has_album") and meta.get("track"):
        template = profile["album_template"]
    else:
        template = profile["single_template"]
    return profiles.render(template, meta)


def already_downloaded(video_id: str, root: Path, fmt: str) -> Path | None:
    """A file this location already has for the video (from an earlier job, possibly under another name)."""
    for r in db.rows("SELECT file_path FROM job_items WHERE video_id = ? AND status IN ('done', 'skipped') "
                     "AND file_path IS NOT NULL ORDER BY id DESC", [video_id]):
        p = (root / r["file_path"])
        if p.suffix.lstrip(".") == EXT[fmt] and p.exists() and tags.video_id_of(p) == video_id:
            return p
    return None


def write_m3u(job: dict, loc: dict) -> Path | None:
    """Playlists/<name>.m3u8 in the location, with paths relative to it (Navidrome and Jellyfin import these)."""
    items = db.rows("SELECT * FROM job_items WHERE job_id = ? AND status IN ('done', 'skipped') AND file_path IS NOT NULL "
                    "ORDER BY position", [job["id"]])
    if not items:
        return None
    root = Path(loc["path"])
    folder = root / "Playlists"
    folder.mkdir(parents=True, exist_ok=True)
    m3u = folder / (profiles.clean(job["title"]) + ".m3u8")
    lines = ["#EXTM3U", f"#PLAYLIST:{job['title']}"]
    for it in items:
        meta = db.jload(it["meta"], {})
        lines.append(f"#EXTINF:{int(meta.get('duration') or -1)},{it['artist'] or ''} - {it['title']}")
        lines.append(os.path.relpath(root / it["file_path"], folder))
    m3u.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return m3u
