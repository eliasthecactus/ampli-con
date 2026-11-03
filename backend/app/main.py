"""Amplicon: FastAPI app, serves the API and the built web interface."""
import asyncio
import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from . import auth, config, db, downloads, locations, profiles, settings_store, youtube, ytdlp
from .api import admin, auth_routes, library
from .events import bus

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("amplicon")
logging.getLogger("httpx").setLevel(logging.WARNING)  # it logs every googlevideo URL otherwise


async def _background() -> None:
    """yt-dlp updates once a day, the YouTube sign-in checked every 6 hours, old jobs cleaned up."""
    tick = 0
    while True:
        try:
            if tick % 24 == 0 and settings_store.get("ytdlp_auto_update"):
                try:
                    await ytdlp.update(channel=settings_store.get("ytdlp_channel"))
                except Exception as e:  # noqa: BLE001
                    log.warning("yt-dlp update: %s", e)
            if tick % 6 == 0 and youtube.account():
                await youtube.check()
            days = int(settings_store.get("keep_finished_jobs_days") or 0)
            if days:
                db.execute("DELETE FROM jobs WHERE status IN ('done', 'cancelled') AND finished_at < ?",
                           [db.now() - days * 86400])
        except Exception:  # noqa: BLE001
            log.exception("background task")
        tick += 1
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    import anyio
    anyio.to_thread.current_default_thread_limiter().total_tokens = 100
    auth.bootstrap()
    locations.ensure_default()
    profiles.ensure_default()
    bus.bind(asyncio.get_running_loop())
    try:
        await ytdlp.ensure_installed()
    except Exception as e:  # noqa: BLE001
        log.warning("yt-dlp not available: %s", e)
    downloads.manager.start()
    bg = None if config.DISABLE_BACKGROUND else asyncio.create_task(_background())
    log.info("Amplicon %s ready", config.VERSION)
    yield
    if bg:
        bg.cancel()
    await downloads.manager.stop()


app = FastAPI(title="Amplicon", version=config.VERSION, lifespan=lifespan, docs_url="/api/docs", redoc_url=None,
              openapi_url="/api/openapi.json")


@app.exception_handler(ValueError)
async def value_error(_: Request, e: ValueError):
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, e: RequestValidationError):
    first = e.errors()[0] if e.errors() else {}
    loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    return JSONResponse({"detail": f"{loc}: {first.get('msg', 'invalid')}".strip(": ")}, status_code=422)


@app.middleware("http")
async def headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return resp


@app.get("/api/health")
async def health():
    return {"ok": True, "app": "amplicon", "version": config.VERSION, "ytdlp": ytdlp.state.get("version")}


app.include_router(auth_routes.router)
app.include_router(admin.router)
app.include_router(library.router)

_static_cache: dict[str, tuple[bytes, str]] = {}


def _static_file(path: str) -> tuple[bytes, str] | None:
    if path in _static_cache:
        return _static_cache[path]
    static = Path(config.STATIC_DIR).resolve()
    f = (static / path).resolve()
    if not path or not f.is_file() or static not in f.parents:
        return None
    mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
    data = f.read_bytes()
    if len(data) < 5 * 1024 * 1024:
        _static_cache[path] = (data, mime)
    return data, mime


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Not found")
    if hit := _static_file(path):
        cache = "public, max-age=31536000, immutable" if "/_app/immutable/" in f"/{path}" else "no-cache"
        return Response(hit[0], media_type=hit[1], headers={"Cache-Control": cache})
    if index := _static_file("index.html"):
        return Response(index[0], media_type="text/html", headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "Frontend not built"}, status_code=404)
