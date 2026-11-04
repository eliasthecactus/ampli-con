"""Download locations: folders mounted into the container, each optionally tied to a library scan."""
import logging
import os
from pathlib import Path

import httpx

from . import config, crypto, db

log = logging.getLogger("amplicon.locations")
SCAN_KINDS = ("none", "navidrome", "jellyfin")


def mounts() -> list[str]:
    """Folders the user mounted into the container (what docker compose maps), for the folder picker."""
    found: set[str] = set()
    try:
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            point = line.split()[4]
            if point == "/" or any(point == p or point.startswith(p + "/") for p in config.SYSTEM_PATHS):
                continue
            if Path(point).resolve() == config.CONFIG_DIR or point.startswith(str(config.CONFIG_DIR) + "/"):
                continue
            if Path(point).is_dir():
                found.add(point)
    except OSError:
        pass
    if config.DOWNLOADS_DIR.is_dir():
        found.add(str(config.DOWNLOADS_DIR))
    return sorted(found)


def allowed(path: Path) -> bool:
    path = path.resolve()
    if path == config.CONFIG_DIR or config.CONFIG_DIR in path.parents:
        return False
    roots = [Path(m).resolve() for m in mounts()]
    return any(path == r or r in path.parents for r in roots)


def browse(path: str | None) -> dict:
    roots = mounts()
    if not path:
        return {"path": None, "parent": None, "dirs": roots, "writable": False, "roots": roots}
    p = Path(path).resolve()
    if not allowed(p) or not p.is_dir():
        raise ValueError("That folder isn't inside a mounted volume")
    dirs = sorted(d.name for d in p.iterdir() if d.is_dir() and not d.name.startswith("."))
    parent = str(p.parent) if allowed(p.parent) else None
    return {"path": str(p), "parent": parent, "dirs": dirs, "writable": os.access(p, os.W_OK), "roots": roots}


def public(r: dict) -> dict:
    out = {k: v for k, v in r.items() if k != "scan_secret_enc"}
    out["has_scan_secret"] = bool(r.get("scan_secret_enc"))
    out["rename_all"] = bool(r.get("rename_all"))
    p = Path(r["path"])
    out["online"] = p.is_dir()
    out["writable"] = p.is_dir() and os.access(p, os.W_OK)
    try:
        st = os.statvfs(p)
        out["free_bytes"] = st.f_bavail * st.f_frsize
    except OSError:
        out["free_bytes"] = None
    return out


def list_all() -> list[dict]:
    return [public(r) for r in db.rows("SELECT * FROM locations ORDER BY name COLLATE NOCASE")]


def get(location_id: int | None) -> dict | None:
    return db.row("SELECT * FROM locations WHERE id = ?", [location_id]) if location_id else None


def save(data: dict, location_id: int | None = None) -> dict:
    name = (data.get("name") or "").strip()
    path = (data.get("path") or "").strip()
    if not name:
        raise ValueError("Give the location a name")
    if not path:
        raise ValueError("Pick a folder")
    p = Path(path).resolve()
    if not allowed(p):
        raise ValueError("That folder isn't inside a volume mounted into the container")
    p.mkdir(parents=True, exist_ok=True)
    if not os.access(p, os.W_OK):
        raise ValueError("Amplicon can't write to that folder (check PUID/PGID and the folder's owner)")
    kind = data.get("scan_kind") or "none"
    if kind not in SCAN_KINDS:
        raise ValueError("Unknown library type")
    fields = {"name": name, "path": str(p), "scan_kind": kind,
              "scan_url": (data.get("scan_url") or "").strip().rstrip("/") or None,
              "scan_user": (data.get("scan_user") or "").strip() or None,
              "rename_all": int(bool(data.get("rename_all")))}
    if kind != "none" and not fields["scan_url"]:
        raise ValueError("The library scan needs the server's address")
    if data.get("scan_secret"):
        fields["scan_secret_enc"] = crypto.encrypt(data["scan_secret"])
    elif kind == "none":
        fields["scan_secret_enc"] = None
    clash = db.row("SELECT id FROM locations WHERE path = ? AND id != ?", [str(p), location_id or 0])
    if clash:
        raise ValueError("That folder is already a location")
    if location_id:
        db.update("locations", location_id, fields)
    else:
        location_id = db.insert("locations", {**fields, "created_at": db.now()})
    return public(get(location_id))


def delete(location_id: int) -> None:
    if db.scalar("SELECT COUNT(*) FROM locations") <= 1:
        raise ValueError("Keep at least one location")
    db.execute("DELETE FROM locations WHERE id = ?", [location_id])


def ensure_default() -> None:
    """On the first start the /downloads volume becomes the first location."""
    if db.scalar("SELECT COUNT(*) FROM locations"):
        return
    try:
        config.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    db.insert("locations", {"name": "Downloads", "path": str(config.DOWNLOADS_DIR), "scan_kind": "none",
                            "created_at": db.now()})


async def trigger_scan(location: dict) -> str | None:
    """Asks Navidrome or Jellyfin to pick up the new files. Returns an error text or None."""
    kind = location.get("scan_kind")
    if kind in (None, "none") or not location.get("scan_url"):
        return None
    secret = crypto.decrypt(location["scan_secret_enc"]) if location.get("scan_secret_enc") else ""
    url = location["scan_url"]
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            if kind == "navidrome":
                r = await c.get(f"{url}/rest/startScan.view", params={
                    "u": location.get("scan_user") or "", "p": secret, "v": "1.16.1", "c": "amplicon", "f": "json"})
                r.raise_for_status()
                status = (r.json().get("subsonic-response") or {}).get("status")
                if status != "ok":
                    return "Navidrome refused the scan (check user and password)"
            else:
                r = await c.post(f"{url}/Library/Refresh", headers={"Authorization": f'MediaBrowser Token="{secret}"'})
                if r.status_code >= 400:
                    return f"Jellyfin answered {r.status_code} (check the API key)"
    except (httpx.HTTPError, ValueError) as e:
        return f"Library scan failed: {e}"
    log.info("asked %s at %s to scan", kind, url)
    return None
