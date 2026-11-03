"""Key/value settings with defaults."""
from typing import Any

from . import db

DEFAULTS: dict[str, Any] = {
    "server_name": "Amplicon",
    "max_parallel_downloads": 2,
    "stream_cache_mb": 2048,
    "stream_with_account": False,
    "ytdlp_channel": "stable",
    "ytdlp_auto_update": True,
    "keep_finished_jobs_days": 30,
}


def get(key: str) -> Any:
    raw = db.scalar("SELECT value FROM settings WHERE key = ?", [key])
    if raw is None:
        return DEFAULTS.get(key)
    return db.jload(raw, DEFAULTS.get(key))


def all_settings() -> dict:
    out = dict(DEFAULTS)
    for r in db.rows("SELECT key, value FROM settings"):
        if r["key"] in DEFAULTS:
            out[r["key"]] = db.jload(r["value"], DEFAULTS[r["key"]])
    return out


def update(values: dict) -> dict:
    for k, v in values.items():
        if k not in DEFAULTS:
            raise ValueError(f"Unknown setting {k}")
        d = DEFAULTS[k]
        if isinstance(d, bool):
            v = bool(v)
        elif isinstance(d, int):
            v = int(v)
            if v < 0:
                raise ValueError(f"{k} can't be negative")
        elif isinstance(d, str):
            v = str(v).strip()
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                   [k, db.jdump(v)])
    return all_settings()
