"""What the app and the web GUI share: search, browse, the account's playlists, streams and downloads.

The app signs in with the web user's name and password and gets a device token. Stream and artwork links
are signed with the device's secret, so AVPlayer can open them without a header and no token is in a URL.
"""
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import auth, config, downloads, locations, profiles, settings_store, stream, youtube
from ..events import bus

router = APIRouter(prefix="/api")


class Login(BaseModel):
    username: str
    password: str
    device: str = "Ampli"


@router.post("/app/login")
def app_login(body: Login, request: Request):
    auth.check_login(body.username, body.password, auth.client_ip(request))
    d = auth.create_device(body.device, auth.client_ip(request))
    return {"token": d["token"], "secret": d["secret"], "device_id": d["id"], **info_payload()}


@router.post("/app/logout")
def app_logout(who: dict = Depends(auth.require_client)):
    if "device_id" in who:
        auth.db.execute("DELETE FROM devices WHERE id = ?", [who["device_id"]])
    return {"ok": True}


def info_payload() -> dict:
    acc = youtube.public_account()
    return {
        "name": settings_store.get("server_name"),
        "version": config.VERSION,
        "youtube": {"connected": bool(acc and acc["status"] == "valid"), **({k: acc[k] for k in ("name", "handle", "photo", "status")} if acc else {})},
        "profiles": [{"id": p["id"], "name": p["name"], "format": p["format"], "location": p["location_name"],
                      "is_default": p["is_default"]} for p in profiles.list_all()],
        "locations": [{"id": loc["id"], "name": loc["name"], "online": loc["online"], "free_bytes": loc["free_bytes"]}
                      for loc in locations.list_all()],
    }


@router.get("/app/info")
def app_info(who: dict = Depends(auth.require_client)):
    return info_payload()


def _yt(e: Exception) -> HTTPException:
    if isinstance(e, youtube.NotSignedIn):
        return HTTPException(409, str(e))
    return HTTPException(502, str(e))


@router.get("/search")
async def search(q: str, type: str = "all", limit: int = 20, who: dict = Depends(auth.require_client)):
    try:
        return await youtube.search(q, type, min(max(limit, 1), 50))
    except youtube.YouTubeError as e:
        raise _yt(e) from e


@router.get("/albums/{browse_id}")
async def album(browse_id: str, who: dict = Depends(auth.require_client)):
    try:
        return await youtube.album(browse_id)
    except youtube.YouTubeError as e:
        raise _yt(e) from e


@router.get("/artists/{channel_id}")
async def artist(channel_id: str, who: dict = Depends(auth.require_client)):
    try:
        return await youtube.artist(channel_id)
    except youtube.YouTubeError as e:
        raise _yt(e) from e


@router.get("/playlists/{playlist_id}")
async def playlist(playlist_id: str, who: dict = Depends(auth.require_client)):
    try:
        return await youtube.playlist(playlist_id)
    except youtube.YouTubeError as e:
        raise _yt(e) from e


# ---------------------------------------------------------------- the account's playlists

class NewPlaylist(BaseModel):
    title: str
    video_ids: list[str] = []


class Rename(BaseModel):
    title: str


class Items(BaseModel):
    video_ids: list[str]


class Remove(BaseModel):
    items: list[dict]  # [{id, set_video_id}]


class Move(BaseModel):
    set_video_id: str
    before_set_video_id: str | None = None


@router.get("/library/playlists")
async def library_playlists(who: dict = Depends(auth.require_client)):
    try:
        return await youtube.library_playlists()
    except youtube.YouTubeError as e:
        raise _yt(e) from e


async def _edit(coro):
    try:
        return await coro
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except youtube.YouTubeError as e:
        raise _yt(e) from e


@router.post("/library/playlists")
async def create_playlist(body: NewPlaylist, who: dict = Depends(auth.require_client)):
    pid = await _edit(youtube.create_playlist(body.title, [stream.check_id(v) for v in body.video_ids]))
    return {"id": pid}


@router.patch("/library/playlists/{playlist_id}")
async def rename_playlist(playlist_id: str, body: Rename, who: dict = Depends(auth.require_client)):
    await _edit(youtube.rename_playlist(playlist_id, body.title))
    return {"ok": True}


@router.delete("/library/playlists/{playlist_id}")
async def delete_playlist(playlist_id: str, who: dict = Depends(auth.require_client)):
    await _edit(youtube.delete_playlist(playlist_id))
    return {"ok": True}


@router.post("/library/playlists/{playlist_id}/items")
async def add_items(playlist_id: str, body: Items, who: dict = Depends(auth.require_client)):
    await _edit(youtube.add_to_playlist(playlist_id, [stream.check_id(v) for v in body.video_ids]))
    return {"ok": True}


@router.post("/library/playlists/{playlist_id}/remove")
async def remove_items(playlist_id: str, body: Remove, who: dict = Depends(auth.require_client)):
    await _edit(youtube.remove_from_playlist(playlist_id, body.items))
    return {"ok": True}


@router.post("/library/playlists/{playlist_id}/move")
async def move_item(playlist_id: str, body: Move, who: dict = Depends(auth.require_client)):
    await _edit(youtube.move_in_playlist(playlist_id, body.set_video_id, body.before_set_video_id))
    return {"ok": True}


# ---------------------------------------------------------------- media

@router.get("/stream/{video_id}")
async def play(video_id: str, request: Request, who: dict = Depends(auth.require_media)):
    return await stream.serve(video_id, request)


@router.head("/stream/{video_id}")
async def play_head(video_id: str, request: Request, who: dict = Depends(auth.require_media)):
    return await stream.serve(video_id, request)


# ---------------------------------------------------------------- downloads

class DownloadRequest(BaseModel):
    kind: str
    id: str
    profile_id: int | None = None
    title: str | None = None
    subtitle: str | None = None
    thumbnail: str | None = None


@router.post("/downloads")
async def download(body: DownloadRequest, who: dict = Depends(auth.require_client)):
    try:
        return await downloads.manager.enqueue(body.kind, body.id, body.profile_id, who["name"],
                                               body.title, body.subtitle, body.thumbnail)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/jobs")
def jobs(active: bool = False, limit: int = 100, who: dict = Depends(auth.require_client)):
    return downloads.list_jobs(active, min(max(limit, 1), 500))


@router.get("/jobs/{job_id}")
def job(job_id: int, who: dict = Depends(auth.require_client)):
    j = downloads.get_job(job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    return downloads.job_public(j, items=True)


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: int, who: dict = Depends(auth.require_client)):
    downloads.manager.cancel(job_id)
    return {"ok": True}


@router.post("/jobs/{job_id}/retry")
def retry(job_id: int, who: dict = Depends(auth.require_client)):
    downloads.manager.retry(job_id)
    return {"ok": True}


@router.delete("/jobs/{job_id}")
def remove(job_id: int, who: dict = Depends(auth.require_client)):
    downloads.manager.remove(job_id)
    return {"ok": True}


@router.delete("/jobs")
def clear(who: dict = Depends(auth.require_client)):
    return {"removed": downloads.manager.clear_finished()}


@router.get("/events")
async def events(request: Request, who: dict = Depends(auth.require_client)):
    q = bus.subscribe()

    async def gen():
        try:
            yield "event: hello\ndata: " + json.dumps({"version": config.VERSION}) + "\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), 20)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
