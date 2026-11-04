"""Web GUI only: YouTube account, locations, profiles, settings, devices, yt-dlp."""
import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, config, db, files, locations, profiles, settings_store, stream, youtube, ytdlp

router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)])


class Cookies(BaseModel):
    cookies: str


@router.get("/youtube")
def youtube_state():
    return {"account": youtube.public_account()}


@router.put("/youtube")
async def youtube_connect(body: Cookies):
    try:
        return {"account": await youtube.connect(body.cookies)}
    except (ValueError, youtube.YouTubeError) as e:
        raise HTTPException(400, str(e)) from e


@router.post("/youtube/check")
async def youtube_check():
    return {"account": await youtube.check()}


@router.delete("/youtube")
def youtube_disconnect():
    youtube.disconnect()
    return {"ok": True}


@router.get("/locations")
def list_locations():
    return locations.list_all()


@router.get("/fs")
def browse(path: str | None = None):
    return locations.browse(path)


@router.post("/locations")
def create_location(body: dict, tasks: BackgroundTasks):
    out = locations.save(body)
    tasks.add_task(files.scan, out["id"])
    return out


@router.put("/locations/{lid}")
def update_location(lid: int, body: dict, tasks: BackgroundTasks):
    if not locations.get(lid):
        raise HTTPException(404, "Location not found")
    out = locations.save(body, lid)
    tasks.add_task(files.scan, lid)
    return out


@router.delete("/locations/{lid}")
def delete_location(lid: int):
    if db.scalar("SELECT COUNT(*) FROM profiles WHERE location_id = ?", [lid]):
        raise HTTPException(400, "A profile still saves to this location. Change the profile first.")
    locations.delete(lid)
    return {"ok": True}


@router.post("/locations/{lid}/scan")
async def scan_location(lid: int):
    loc = locations.get(lid)
    if not loc:
        raise HTTPException(404, "Location not found")
    if loc["scan_kind"] == "none":
        raise HTTPException(400, "No library is set for this location")
    if err := await locations.trigger_scan(loc):
        raise HTTPException(400, err)
    return {"ok": True}


@router.get("/profiles")
def list_profiles():
    return profiles.list_all()


@router.get("/profiles/options")
def profile_options():
    return {"formats": profiles.FORMATS, "mp3_qualities": profiles.MP3_QUALITIES, "fields": profiles.FIELDS,
            "requirable": profiles.REQUIRABLE,
            "defaults": {"album_template": profiles.DEFAULT_ALBUM, "single_template": profiles.DEFAULT_SINGLE,
                         "playlist_template": profiles.DEFAULT_PLAYLIST}}


@router.post("/profiles/preview")
def profile_preview(body: dict):
    return profiles.preview(body)


@router.post("/profiles")
def create_profile(body: dict):
    out = profiles.save(body)
    files.refresh_targets()
    return {**out, "rename": files.rename_preview()}


@router.put("/profiles/{pid}")
def update_profile(pid: int, body: dict):
    if not db.row("SELECT id FROM profiles WHERE id = ?", [pid]):
        raise HTTPException(404, "Profile not found")
    out = profiles.save(body, pid)
    files.refresh_targets()
    return {**out, "rename": files.rename_preview()}  # the web interface asks before moving existing files


@router.delete("/profiles/{pid}")
def delete_profile(pid: int):
    profiles.delete(pid)
    files.refresh_targets()
    return {"ok": True}


@router.get("/settings")
def get_settings():
    return {"settings": settings_store.all_settings(), "cache": stream.cache_size()}


@router.put("/settings")
def put_settings(body: dict):
    return {"settings": settings_store.update(body), "cache": stream.cache_size()}


@router.delete("/cache")
def clear_cache():
    stream.clear_cache()
    return {"cache": stream.cache_size()}


@router.get("/devices")
def devices():
    return db.rows("SELECT id, name, created_at, last_seen, last_ip FROM devices ORDER BY last_seen DESC")


@router.delete("/devices/{did}")
def revoke_device(did: int):
    db.execute("DELETE FROM devices WHERE id = ?", [did])
    return {"ok": True}


@router.get("/system")
def system():
    return {"version": config.VERSION, "ytdlp": {k: ytdlp.state.get(k) for k in ("version", "latest", "last_check",
                                                                                 "last_result", "updating")}}


@router.post("/system/ytdlp/update")
async def update_ytdlp():
    try:
        await ytdlp.update(force=False, channel=settings_store.get("ytdlp_channel"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Update failed: {e}") from e
    return system()
