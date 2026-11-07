"""Playing YouTube songs in the app: Amplicon resolves the audio with yt-dlp and passes it through.

googlevideo links only work from the IP that resolved them, so the app can't fetch them itself. The proxy
forwards Range requests (AVPlayer seeks with them) and fills an on-disk cache in the background, so a song
played twice comes from the disk.
"""
import asyncio
import logging
import os
import re
import struct
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from . import config, settings_store, youtube, ytdlp

log = logging.getLogger("amplicon.stream")

VIDEO_ID = re.compile(r"^[\w-]{11}$")
# AAC in MP4 (itag 140) plays in AVPlayer as is; opus/webm doesn't.
FORMAT = "140/bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio"
_slots: dict[int, asyncio.Semaphore] = {}
_resolved: dict[str, dict] = {}
_resolving: dict[str, asyncio.Future] = {}
_filling: set[str] = set()
_http: httpx.AsyncClient | None = None


_http_loop: int | None = None


def http() -> httpx.AsyncClient:
    global _http, _http_loop
    loop = id(asyncio.get_running_loop())
    if _http is None or (_http_loop is not None and _http_loop != loop):
        _http_loop = loop
        _http = httpx.AsyncClient(timeout=httpx.Timeout(30, read=60), follow_redirects=True,
                                  limits=httpx.Limits(max_connections=40))
    return _http


def check_id(video_id: str) -> str:
    if not VIDEO_ID.match(video_id):
        raise HTTPException(400, "Not a song id")
    return video_id


def cached_file(video_id: str) -> Path | None:
    p = config.STREAM_CACHE_DIR / f"{video_id}.m4a"
    if p.exists():
        try:
            os.utime(p)  # least recently used goes first when the cache is full
        except OSError:
            pass
        if video_id not in _checked:
            _checked.add(video_id)
            _fix_file(p)  # cached before the header fix
        return p
    return None


# ---------------------------------------------------------------- header fix
#
# YouTube's audio is fragmented MP4 (DASH). Its track header (mdhd) still states the whole length, and
# AVFoundation adds that to the length of the fragments, so the app showed every song at twice its length
# and played silence for the second half. A fragmented file's mdhd should say 0 (yt-dlp remuxes it for
# downloads), so the proxy zeroes that field on the way through. The size stays the same, so ranges match.

HEADER_BYTES = 64 * 1024
_patches: dict[tuple[str, int], list[tuple[int, int]]] = {}
_checked: set[str] = set()


def _boxes(buf: bytes, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack(">I4s", buf[pos:pos + 8])
        head = 8
        if size == 1:
            if pos + 16 > end:
                return
            size, head = struct.unpack(">Q", buf[pos + 8:pos + 16])[0], 16
        elif size == 0:
            size = end - pos
        if size < head or pos + size > end:
            return
        yield kind, pos, pos + head, pos + size
        pos += size


def header_patches(buf: bytes) -> list[tuple[int, int]]:
    """Where the mdhd durations of a fragmented MP4 sit, as (offset, length), when they aren't 0 already."""
    for kind, _, body, end in _boxes(buf, 0, len(buf)):
        if kind != b"moov":
            continue
        children = list(_boxes(buf, body, end))
        if not any(k == b"mvex" for k, *_ in children):
            return []  # not fragmented: the durations are right
        out = []
        for k, _, tbody, tend in children:
            if k != b"trak":
                continue
            for k2, _, mbody, mend in _boxes(buf, tbody, tend):
                if k2 != b"mdia":
                    continue
                for k3, _, hbody, hend in _boxes(buf, mbody, mend):
                    if k3 != b"mdhd" or hend - hbody < 20:
                        continue
                    at, n = (hbody + 16, 4) if buf[hbody] == 0 else (hbody + 24, 8)
                    if at + n <= hend and any(buf[at:at + n]):
                        out.append((at, n))
        return out
    return []


def apply_patches(chunk: bytes, pos: int, patches: list[tuple[int, int]]) -> bytes:
    """Zeroes the patched ranges inside a chunk that starts at file offset pos."""
    hits = [(a, n) for a, n in patches if a < pos + len(chunk) and a + n > pos]
    if not hits:
        return chunk
    b = bytearray(chunk)
    for a, n in hits:
        lo, hi = max(a, pos) - pos, min(a + n, pos + len(chunk)) - pos
        b[lo:hi] = bytes(hi - lo)
    return bytes(b)


def _fix_file(path: Path) -> None:
    try:
        with open(path, "r+b") as f:
            for at, n in header_patches(f.read(HEADER_BYTES)):
                f.seek(at)
                f.write(bytes(n))
    except OSError as e:
        log.warning("couldn't fix the header of %s: %s", path.name, e)


async def _header_patches(video_id: str, info: dict) -> list[tuple[int, int]]:
    key = (video_id, info["size"])
    if key not in _patches:
        r = await _open(video_id, 0, min(HEADER_BYTES, info["size"]) - 1)
        try:
            if r.status_code >= 400:
                return []  # the stream itself will report the error
            _patches[key] = header_patches(await r.aread())
        finally:
            await r.aclose()
    return _patches[key]


def _expiry(url: str) -> float:
    try:
        exp = float(parse_qs(urlparse(url).query).get("expire", ["0"])[0])
    except ValueError:
        exp = 0
    limit = time.time() + 4 * 3600
    return min(exp - 300, limit) if exp else limit


def _clen(url: str) -> int | None:
    try:
        return int(parse_qs(urlparse(url).query).get("clen", ["0"])[0]) or None
    except ValueError:
        return None


async def _run_resolve(video_id: str) -> dict:
    url = f"https://music.youtube.com/watch?v={video_id}"
    loop = id(asyncio.get_running_loop())
    slots = _slots.setdefault(loop, asyncio.Semaphore(2))
    async with slots:
        attempts = [settings_store.get("stream_with_account")]
        if not attempts[0] and youtube.account():
            attempts.append(True)  # retry signed in for age-restricted or "not a bot" answers
        error = "yt-dlp couldn't read this video"
        for use_account in attempts:
            async with youtube.cookies_file(use_account) as cf:
                data, res = await ytdlp.run_json(
                    ytdlp.base_args(cf) + ["-J", "--no-playlist", "--no-warnings", "-f", FORMAT, "--", url], timeout=90)
            if data and data.get("url"):
                return {
                    "url": data["url"],
                    "headers": data.get("http_headers") or {},
                    "ext": data.get("ext") or "m4a",
                    "acodec": data.get("acodec") or "",
                    "filesize": data.get("filesize") or data.get("filesize_approx"),
                    "duration": data.get("duration"),
                    "expires": _expiry(data["url"]),
                    "resolved_at": time.time(),
                    "size": _clen(data["url"]) or data.get("filesize"),
                }
            error = res.error_text or error
            if ytdlp.classify(error) == "unavailable":
                break
        raise HTTPException(502 if ytdlp.classify(error) != "unavailable" else 404, error.removeprefix("ERROR: ")[:300])


async def resolve(video_id: str, fresh: bool = False) -> dict:
    hit = _resolved.get(video_id)
    if hit and not fresh and hit["expires"] > time.time():
        return hit
    if video_id in _resolving:
        return await asyncio.shield(_resolving[video_id])
    fut = asyncio.get_running_loop().create_future()
    _resolving[video_id] = fut
    try:
        info = await _run_resolve(video_id)
        _resolved[video_id] = info
        fut.set_result(info)
        return info
    except BaseException as e:
        fut.set_exception(e)
        fut.exception()  # mark retrieved
        raise
    finally:
        _resolving.pop(video_id, None)


def playable_directly(info: dict) -> bool:
    return info["ext"] in ("m4a", "mp4") or info["acodec"].startswith("mp4a")


# ---------------------------------------------------------------- cache

def _cache_limit() -> int:
    return int(settings_store.get("stream_cache_mb") or 0) * 1024 * 1024


def trim_cache(extra: int = 0) -> None:
    limit = _cache_limit()
    files = sorted(config.STREAM_CACHE_DIR.glob("*.m4a"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files) + extra
    while files and total > limit:
        p = files.pop(0)
        total -= p.stat().st_size
        p.unlink(missing_ok=True)


def cache_size() -> dict:
    files = list(config.STREAM_CACHE_DIR.glob("*.m4a"))
    return {"files": len(files), "bytes": sum(p.stat().st_size for p in files), "limit": _cache_limit()}


def clear_cache() -> None:
    for p in config.STREAM_CACHE_DIR.glob("*"):
        p.unlink(missing_ok=True)


async def fill_cache(video_id: str) -> Path | None:
    """Downloads the whole song into the cache (or transcodes it to AAC when YouTube has no MP4 audio)."""
    if video_id in _filling or cached_file(video_id) or _cache_limit() <= 0 and not _needs_transcode.get(video_id):
        return cached_file(video_id)
    _filling.add(video_id)
    dest = config.STREAM_CACHE_DIR / f"{video_id}.m4a"
    part = dest.with_suffix(".part")
    try:
        info = await resolve(video_id)
        if playable_directly(info) and info.get("size"):
            patches = await _header_patches(video_id, info)
            with open(part, "wb") as f:
                async for chunk in _chunks(video_id, 0, info["size"] - 1, patches=patches):
                    f.write(chunk)
            if part.stat().st_size != info["size"]:
                raise RuntimeError(f"got {part.stat().st_size} of {info['size']} bytes")
        else:
            headers = "".join(f"{k}: {v}\r\n" for k, v in info["headers"].items())
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-loglevel", "error", "-y", "-headers", headers, "-i", info["url"], "-vn",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-f", "mp4", str(part),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(proc.communicate(), 600)
            if proc.returncode != 0:
                raise RuntimeError(err.decode(errors="replace")[-300:])
        trim_cache(part.stat().st_size)
        os.replace(part, dest)
        return dest
    except Exception as e:
        log.warning("caching %s failed: %s", video_id, e)
        part.unlink(missing_ok=True)
        return None
    finally:
        _filling.discard(video_id)


_needs_transcode: dict[str, bool] = {}


# ---------------------------------------------------------------- serving

CHUNK = 4 * 1024 * 1024  # googlevideo throttles requests without a range; ask for pieces like yt-dlp does


async def _open(video_id: str, start: int, end: int) -> httpx.Response:
    """One range from googlevideo. A freshly resolved link answers 403 for the first few seconds, so that is
    waited out; an older link that fails is resolved again once."""
    refreshed = False
    tries = 0
    while True:
        info = await resolve(video_id, fresh=False)
        headers = {**info["headers"], "Range": f"bytes={start}-{end}"}
        r = await http().send(http().build_request("GET", info["url"], headers=headers), stream=True)
        if r.status_code not in (403, 410):
            return r
        await r.aclose()
        tries += 1
        if time.time() - info["resolved_at"] < 20 and tries < 12:
            await asyncio.sleep(1)
            continue
        if refreshed:
            return r
        refreshed = True
        await resolve(video_id, fresh=True)


async def _chunks(video_id: str, start: int, end: int, first: httpx.Response | None = None,
                  patches: list[tuple[int, int]] = ()):
    pos = start
    while pos <= end:
        stop = min(pos + CHUNK - 1, end)
        r = first if first is not None else await _open(video_id, pos, stop)
        first = None
        try:
            if r.status_code >= 400:
                raise RuntimeError(f"The source answered {r.status_code}")
            async for chunk in r.aiter_bytes(64 * 1024):
                if patches:
                    chunk = apply_patches(chunk, pos, patches)
                pos += len(chunk)
                yield chunk
        finally:
            await r.aclose()
        if pos <= stop:
            raise RuntimeError("The source closed the connection early")


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    m = re.match(r"bytes=(\d*)-(\d*)$", (header or "").strip())
    if not m or (not m.group(1) and not m.group(2)):
        return None
    if not m.group(1):
        n = int(m.group(2))
        return max(size - n, 0), size - 1
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else size - 1
    if start >= size:
        raise HTTPException(416, "Range not satisfiable", headers={"Content-Range": f"bytes */{size}"})
    return start, min(end, size - 1)


async def serve(video_id: str, request: Request) -> Response:
    check_id(video_id)
    if f := cached_file(video_id):
        return FileResponse(f, media_type="audio/mp4", headers={"Cache-Control": "private, max-age=86400"})
    info = await resolve(video_id)
    if not playable_directly(info) or not info.get("size"):
        # No MP4 audio on YouTube (or no known size): convert once into the cache, then serve the file.
        _needs_transcode[video_id] = True
        f = await fill_cache(video_id)
        if not f:
            raise HTTPException(502, "Couldn't prepare this song for playback")
        return FileResponse(f, media_type="audio/mp4")

    if _cache_limit() > 0 and video_id not in _filling:
        asyncio.create_task(fill_cache(video_id))

    size = info["size"]
    rng = _parse_range(request.headers.get("range"), size)
    start, end = rng or (0, size - 1)
    # the field sits past the first 64 bytes (ftyp, moov, trak, mdia and mdhd headers come first)
    if start < HEADER_BYTES and end >= 64 and request.method != "HEAD":
        patches, first = await asyncio.gather(_header_patches(video_id, info),
                                              _open(video_id, start, min(start + CHUNK - 1, end)))
    else:
        patches, first = [], await _open(video_id, start, min(start + CHUNK - 1, end))
    if first.status_code >= 400:
        await first.aclose()
        raise HTTPException(502, f"The source answered {first.status_code}")
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1), "Cache-Control": "private, max-age=3600"}
    if rng:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if request.method == "HEAD":
        await first.aclose()
        return Response(status_code=206 if rng else 200, media_type="audio/mp4", headers=headers)
    return StreamingResponse(_chunks(video_id, start, end, first, patches), status_code=206 if rng else 200,
                             media_type="audio/mp4", headers=headers)
