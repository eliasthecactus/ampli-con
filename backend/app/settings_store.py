"""Key/value settings with defaults."""
from typing import Any

from . import crypto, db

DEFAULT_AI_MODEL = "deepseek/deepseek-v4.1-flash-1017"
DEFAULT_AI_INSTRUCTION = (
    'Split artist and title and remove noise like (Official Video) or [Lyrics]. Use the real artist name instead of '
    'VEVO or "- Topic" channels. List all artists comma-separated, including featured artists and any remixer, mashup '
    'or cover artist. Keep feat./with out of the title, but keep versions in it as (Name Remix), (Name Mashup) or '
    '(Name Cover).')

DEFAULTS: dict[str, Any] = {
    "server_name": "Amplicon",
    "max_parallel_downloads": 2,
    "stream_cache_mb": 2048,
    "stream_with_account": False,
    "ytdlp_channel": "stable",
    "ytdlp_auto_update": True,
    "keep_finished_jobs_days": 30,
    # metadata
    "metadata_review": False,
    "metadata_deezer": True,
    "metadata_itunes_covers": False,
    "enforce_file_names": True,
    # AI tag helper
    "openrouter_model": DEFAULT_AI_MODEL,
    "ai_instruction": DEFAULT_AI_INSTRUCTION,
    "ai_batch_size": 25,
    "openrouter_key_enc": "",
}
SECRET_KEYS = {"openrouter_key_enc"}


def get(key: str) -> Any:
    raw = db.scalar("SELECT value FROM settings WHERE key = ?", [key])
    if raw is None:
        return DEFAULTS.get(key)
    return db.jload(raw, DEFAULTS.get(key))


def all_settings() -> dict:
    """What the web interface sees: never a stored key, only whether there is one."""
    out = dict(DEFAULTS)
    for r in db.rows("SELECT key, value FROM settings"):
        if r["key"] in DEFAULTS:
            out[r["key"]] = db.jload(r["value"], DEFAULTS[r["key"]])
    out["openrouter_key_set"] = bool(out.get("openrouter_key_enc"))
    for k in SECRET_KEYS:
        out.pop(k, None)
    return out


def openrouter_key() -> str:
    enc = get("openrouter_key_enc")
    return crypto.decrypt(enc) if enc else ""


def update(values: dict) -> dict:
    values = dict(values)
    values.pop("openrouter_key_set", None)
    if "openrouter_key" in values:
        key = str(values.pop("openrouter_key") or "").strip()
        values["openrouter_key_enc"] = crypto.encrypt(key) if key else ""
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
            v = str(v).strip() or (DEFAULTS[k] if k in ("openrouter_model", "ai_instruction") else "")
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                   [k, db.jdump(v)])
    return all_settings()
