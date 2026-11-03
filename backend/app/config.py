"""Paths and environment configuration."""
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config")).resolve()
DOWNLOADS_DIR = Path(os.environ.get("DOWNLOADS_DIR", "/downloads")).resolve()
STATIC_DIR = Path(os.environ.get("STATIC_DIR", Path(__file__).resolve().parent.parent / "static"))
BUNDLED_YTDLP = Path(os.environ.get("BUNDLED_YTDLP", "/opt/yt-dlp/yt-dlp"))

DB_PATH = CONFIG_DIR / "amplicon.db"
BIN_DIR = CONFIG_DIR / "bin"
TMP_DIR = CONFIG_DIR / "tmp"
CACHE_DIR = CONFIG_DIR / "cache"
STREAM_CACHE_DIR = CACHE_DIR / "streams"
LOG_DIR = CONFIG_DIR / "logs"
SECRET_FILE = CONFIG_DIR / "secret.key"

# The one web user. When both are set they win on every start; otherwise a password is generated on the
# first start and printed to the logs once.
ENV_USERNAME = os.environ.get("AMPLICON_USERNAME", "").strip()
ENV_PASSWORD = os.environ.get("AMPLICON_PASSWORD", "")
APP_SECRET = os.environ.get("APP_SECRET", "").strip()
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "30"))
_VERSION_FILE = Path(__file__).resolve().parent / "VERSION"
VERSION = _VERSION_FILE.read_text().strip() if _VERSION_FILE.exists() else "dev"
# Set to 1 to disable background loops (yt-dlp updates, cookie checks) in tests.
DISABLE_BACKGROUND = os.environ.get("AMPLICON_DISABLE_BACKGROUND", "") == "1"
# Folders that are never offered as download locations.
SYSTEM_PATHS = ("/proc", "/sys", "/dev", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/var", "/run", "/boot",
                "/app", "/opt", "/root", "/tmp", "/home", "/srv", "/mnt/wsl")


def env_user_set() -> bool:
    return bool(ENV_USERNAME and ENV_PASSWORD)


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, BIN_DIR, TMP_DIR, CACHE_DIR, STREAM_CACHE_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
