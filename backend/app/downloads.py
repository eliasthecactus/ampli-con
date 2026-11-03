"""The download queue: songs, albums and playlists from YouTube Music into a location, named by a profile.

A job is what someone asked for (one song, an album, a playlist). It is expanded into items (one per song),
and a small pool of workers downloads items one after another across all jobs, at most
`max_parallel_downloads` at a time.
"""
import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path

from . import config, db, locations, profiles, settings_store, tags, youtube, ytdlp
from .events import bus
from .stream import http

log = logging.getLogger("amplicon.downloads")

KINDS = ("song", "album", "playlist")
ACTIVE = ("preparing", "queued", "running")
EXT = {"m4a": "m4a", "opus": "opus", "mp3": "mp3"}


class DownloadError(Exception):
    pass


# ---------------------------------------------------------------- views

def job_public(j: dict, items: bool = False) -> dict:
    out = {k: j[k] for k in ("id", "kind", "source_id", "title", "subtitle", "thumbnail", "profile_id", "profile_name",
                             "location_name", "status", "total", "done", "skipped", "failed", "error", "requested_by",
                             "created_at", "started_at", "finished_at")}
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
                      location_id: int | None = None) -> dict:
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
            "created_at": db.now(),
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
        bus.publish("job_removed", {"id": job_id})

    def clear_finished(self) -> int:
        n = db.scalar("SELECT COUNT(*) FROM jobs WHERE status NOT IN ('preparing', 'queued', 'running')")
        db.execute("DELETE FROM jobs WHERE status NOT IN ('preparing', 'queued', 'running')")
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
                c.execute("UPDATE jobs SET status = 'queued', total = ?, done = 0, skipped = 0, failed = 0, title = ?, "
                          "subtitle = ?, thumbnail = COALESCE(?, thumbnail), params = ? WHERE id = ? AND status = 'preparing'",
                          [len(items), head.get("title") or j["title"], head.get("subtitle") or j["subtitle"],
                           head.get("thumbnail"), db.jdump(head), job_id])
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
            self._progress(item, 1.0)
            return "done", rel, None
        finally:
            shutil.rmtree(work, ignore_errors=True)

    async def _metadata(self, item: dict, j: dict) -> dict:
        m = db.jload(item["meta"], {})
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
        url = meta.get("thumbnail")
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
