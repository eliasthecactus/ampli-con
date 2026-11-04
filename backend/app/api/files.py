"""The library in the web interface: browse, edit, rename, deduplicate."""
import asyncio
import hashlib
import io
import shutil

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from PIL import Image
from pydantic import BaseModel

from .. import ai, auth, config, db, downloads, files, fixes, locations, metadata, stream, tags

router = APIRouter(prefix="/api/files", dependencies=[Depends(auth.require_user)])

MAX_IDS = 30000
MAX_PREVIEW = 5000
BROWSER_FORMATS = (".m4a", ".m4b", ".mp4", ".mp3", ".opus", ".ogg", ".oga", ".flac", ".wav", ".aac")


class Ids(BaseModel):
    ids: list[int]


class Ops(BaseModel):
    ids: list[int]
    ops: dict


class Apply(BaseModel):
    changes: list[dict]


class Rename(BaseModel):
    location_id: int | None = None
    ids: list[int] | None = None
    limit: int = 8


class Ai(BaseModel):
    ids: list[int]
    instruction: str | None = None
    fields: list[str] | None = None


class Lookup(BaseModel):
    query: str | None = None


class Key(BaseModel):
    key: str


class Selection(BaseModel):
    ids: list[int] | None = None
    folders: list[dict] | None = None
    state: str = "new"


class Import(Selection):
    profile_id: int


class FixStart(BaseModel):
    ids: list[int] | None = None
    mode: str = "fill"


class FixAccept(BaseModel):
    ids: list[int] | None = None
    group: str | None = None


class FixDiscard(BaseModel):
    ids: list[int] | None = None


def _check(ids: list[int], most: int = MAX_IDS) -> None:
    if not ids:
        raise HTTPException(400, "Select at least one file")
    if len(ids) > most:
        raise HTTPException(400, f"At most {most} files at once")


async def _rescan(location_ids: list[int]) -> None:
    for lid in location_ids:
        if loc := locations.get(lid):
            await locations.trigger_scan(loc)


@router.get("")
def list_files(request: Request):
    return files.search(dict(request.query_params))


@router.get("/ids")
def list_ids(request: Request):
    return {"ids": files.matching_ids(dict(request.query_params))}


@router.get("/facets")
def facets():
    return {**files.facets(), "fix": fixes.counts()}


@router.post("/scan")
def scan(tasks: BackgroundTasks):
    tasks.add_task(files.scan)
    return {"ok": True}


@router.get("/import")
def waiting(state: str = "new"):
    return files.folders("ignored" if state == "ignored" else "new")


def _selected(body: Selection) -> list[int]:
    ids = files.ids_of(body.ids, body.folders, body.state)
    if not ids:
        raise HTTPException(400, "Select at least one file")
    return ids


@router.post("/import")
def import_files(body: Import):
    try:
        return files.import_files(_selected(body), body.profile_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/ignore")
def ignore_files(body: Selection):
    return files.ignore_files(_selected(body))


@router.post("/restore")
def restore_files(body: Selection):
    return files.restore_files(_selected(body))


@router.post("/forget")
def forget(body: Ids):
    _check(body.ids)
    return files.forget(body.ids)


@router.get("/fix")
def fix_state(group: str = "confident", offset: int = 0):
    return {"counts": fixes.counts(), "items": fixes.items(group, offset), "eligible": fixes.eligible()}


@router.post("/fix")
def fix_start(body: FixStart):
    if body.ids is not None:
        _check(body.ids)
    return {"queued": fixes.manager.queue(body.ids, body.mode)}


@router.post("/fix/apply")
def fix_apply(body: FixAccept):
    if body.ids is not None:
        _check(body.ids)
    return {"accepted": fixes.manager.accept(body.ids, body.group)}


@router.post("/fix/discard")
def fix_discard(body: FixDiscard):
    fixes.manager.discard(body.ids)
    return {"ok": True}


@router.get("/rename")
def rename_preview(location_id: int | None = None):
    return files.rename_preview(location_id)


@router.post("/rename/preview")
def rename_preview_of(body: Rename):
    return files.rename_preview(body.location_id, body.ids, max(1, min(body.limit, MAX_PREVIEW)))


@router.post("/rename")
async def rename(body: Rename):
    out = await asyncio.to_thread(files.rename, body.location_id, body.ids)
    await _rescan(out["locations"])
    return out


@router.post("/preview")
def preview(body: Ops):
    _check(body.ids)
    return {"proposals": files.preview_ops(body.ids, body.ops)}


@router.post("/apply")
async def apply(body: Apply):
    _check([c.get("id") for c in body.changes])
    changes = []
    for c in body.changes:
        ch = {"id": c["id"], "fields": c.get("fields") or {}, "remove_cover": bool(c.get("remove_cover"))}
        if c.get("cover"):
            ch["cover"] = await _cover_bytes(c["cover"])
        changes.append(ch)
    out = await asyncio.to_thread(files.apply_changes, changes)
    await _rescan(out["locations"])
    return out


async def _cover_bytes(spec: dict) -> bytes:
    """{data} uploaded, or {url} of a suggestion."""
    if spec.get("data"):
        return downloads.decode_cover(spec["data"])
    url = metadata.hires(spec.get("url"))
    if not metadata.cover_allowed(url):
        raise HTTPException(400, "Covers can only come from the suggestions or an upload")
    r = await stream.http().get(url)
    r.raise_for_status()
    return await asyncio.to_thread(tags.square_jpeg, r.content)


@router.post("/ai")
async def ai_suggest(body: Ai):
    _check(body.ids, 500)
    songs, before = [], {}
    for fid in body.ids:
        d = files.detail(fid)
        t = d["tags"]
        before[fid] = (d, t)
        songs.append({"id": fid, "file": d["path"].rsplit("/", 1)[-1],
                      **{k: t.get(k) for k in ai.FIELDS if t.get(k)}, "duration": d.get("duration")})
    out = await ai.suggest(songs, body.instruction, body.fields)
    proposals = []
    for fid in body.ids:
        d, t = before[fid]
        old = {k: t.get(k) for k in tags.FIELDS if k not in ("comment", "lyrics")}
        new = {**old, **out["suggestions"].get(str(fid), {})}
        proposals.append({"id": fid, "path": d["path"], "before": old, "after": new,
                          "changed": [k for k in new if (new[k] or None) != (old[k] or None)]})
    return {"proposals": proposals, "errors": out["errors"], "model": out["model"]}


@router.post("/lookup")
async def lookup_many(body: Ids):
    """Bulk: the best match of every file, as proposals. Only confident matches count."""
    _check(body.ids, 500)
    gate = asyncio.Semaphore(4)

    async def one(fid: int) -> dict:
        d = files.detail(fid)
        t = d["tags"]
        old = {k: t.get(k) for k in tags.FIELDS if k not in ("comment", "lyrics")}
        async with gate:
            found = await metadata.lookup(files.lookup_song(d), 1)
        best = found[0] if found and found[0].get("score", 0) >= metadata.CONFIDENT else None
        new = {k: v for k, v in metadata.apply(old, best or {}).items() if k in old}
        return {"id": fid, "path": d["path"], "before": old, "after": new, "score": best["score"] if best else 0,
                "cover": best.get("cover") if best else None, "source": best.get("source") if best else None,
                "changed": [k for k in new if (new[k] or None) != (old[k] or None)]}
    return {"proposals": await asyncio.gather(*(one(i) for i in body.ids))}


@router.get("/duplicates")
async def duplicates(ignored: bool = False):
    return {"groups": await asyncio.to_thread(files.duplicates, ignored)}


@router.post("/duplicates/ignore")
def ignore(body: Key):
    files.ignore_duplicates(body.key)
    return {"ok": True}


@router.post("/delete")
async def delete(body: Ids):
    _check(body.ids)
    locs = sorted({r["location_id"] for r in db.rows(
        f"SELECT location_id FROM library_files WHERE id IN ({','.join('?' * len(body.ids))})", body.ids)})
    out = await asyncio.to_thread(files.delete_files, body.ids)
    await _rescan(locs)
    return out


@router.get("/{file_id}")
def detail(file_id: int):
    return files.detail(file_id)


@router.post("/{file_id}/lookup")
async def lookup(file_id: int, body: Lookup):
    d = files.detail(file_id)
    song = files.lookup_song(d)
    query = (body.query or "").strip() or None
    found = await metadata.lookup(song, 3, query)
    extra = await metadata.itunes_cover(song.get("albumartist") or song.get("artist") or "", song.get("album") or "")
    return {"candidates": found, "covers": [{"source": "itunes", "url": extra}] if extra else []}


@router.get("/{file_id}/cover")
async def cover(file_id: int, size: int = 0):
    path = files.path_of(file_id)
    if not path.exists():
        raise HTTPException(404, "File not found")
    st = path.stat()
    tag = hashlib.sha1(f"{file_id}:{st.st_mtime}:{st.st_size}:{size}".encode()).hexdigest()[:16]
    thumb = config.CACHE_DIR / "library-covers" / f"{tag}.jpg"
    headers = {"Cache-Control": "private, max-age=3600"}
    if thumb.exists():
        return Response(thumb.read_bytes(), media_type="image/jpeg", headers=headers)
    data = await asyncio.to_thread(tags.cover_of, path)
    if not data:
        raise HTTPException(404, "No cover")
    if size:
        def small() -> bytes:
            img = Image.open(io.BytesIO(data)).convert("RGB")
            img.thumbnail((min(size, 600), min(size, 600)))
            out = io.BytesIO()
            img.save(out, "JPEG", quality=82)
            return out.getvalue()
        try:
            data = await asyncio.to_thread(small)
            thumb.parent.mkdir(parents=True, exist_ok=True)
            thumb.write_bytes(data)
        except Exception:  # noqa: BLE001 - the original still shows
            pass
    return Response(data, media_type="image/jpeg", headers=headers)


@router.get("/{file_id}/folder-cover")
def folder_cover(file_id: int):
    art = files.folder_cover(files.path_of(file_id))
    if not art:
        raise HTTPException(404, "No cover")
    return FileResponse(art)


@router.get("/{file_id}/audio")
async def audio(file_id: int):
    path = files.path_of(file_id)
    if not path.exists():
        raise HTTPException(404, "File not found")
    if path.suffix.lower() in BROWSER_FORMATS:
        return FileResponse(path)
    ffmpeg = shutil.which("ffmpeg")  # WMA, APE and others: browsers can't play them, so the preview is converted
    if not ffmpeg:
        raise HTTPException(415, "This format can't be played here")
    proc = await asyncio.create_subprocess_exec(ffmpeg, "-loglevel", "error", "-i", str(path), "-vn", "-c:a", "libmp3lame", "-b:a", "192k",
                                                "-f", "mp3", "pipe:1", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)

    async def pump():
        try:
            while chunk := await proc.stdout.read(65536):
                yield chunk
        finally:
            if proc.returncode is None:
                proc.kill()
    return StreamingResponse(pump(), media_type="audio/mpeg")
