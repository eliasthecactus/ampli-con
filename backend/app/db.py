"""SQLite access: one short-lived connection per operation, WAL mode."""
import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterable

from . import config

MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        created_at REAL NOT NULL,
        password_changed_at REAL
    );
    CREATE TABLE sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        last_seen REAL NOT NULL
    );
    CREATE TABLE devices (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        secret TEXT NOT NULL,
        created_at REAL NOT NULL,
        last_seen REAL,
        last_ip TEXT
    );
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE youtube_account (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        cookies_enc TEXT NOT NULL,
        name TEXT,
        handle TEXT,
        photo TEXT,
        status TEXT NOT NULL DEFAULT 'unknown',
        error TEXT,
        checked_at REAL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE locations (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        path TEXT NOT NULL UNIQUE,
        scan_kind TEXT NOT NULL DEFAULT 'none',
        scan_url TEXT,
        scan_user TEXT,
        scan_secret_enc TEXT,
        created_at REAL NOT NULL
    );
    CREATE TABLE profiles (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,
        format TEXT NOT NULL DEFAULT 'm4a',
        quality TEXT NOT NULL DEFAULT '320',
        album_template TEXT NOT NULL,
        single_template TEXT NOT NULL,
        playlist_template TEXT NOT NULL,
        playlist_layout TEXT NOT NULL DEFAULT 'album',
        write_m3u INTEGER NOT NULL DEFAULT 1,
        embed_cover INTEGER NOT NULL DEFAULT 1,
        embed_lyrics INTEGER NOT NULL DEFAULT 0,
        sponsorblock INTEGER NOT NULL DEFAULT 1,
        on_exists TEXT NOT NULL DEFAULT 'skip',
        is_default INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL
    );
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        source_id TEXT NOT NULL,
        title TEXT NOT NULL,
        subtitle TEXT,
        thumbnail TEXT,
        profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
        profile_name TEXT,
        location_name TEXT,
        status TEXT NOT NULL DEFAULT 'queued',
        total INTEGER NOT NULL DEFAULT 0,
        done INTEGER NOT NULL DEFAULT 0,
        skipped INTEGER NOT NULL DEFAULT 0,
        failed INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        requested_by TEXT,
        params TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        started_at REAL,
        finished_at REAL
    );
    CREATE INDEX jobs_created ON jobs(created_at DESC);
    CREATE TABLE job_items (
        id INTEGER PRIMARY KEY,
        job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        video_id TEXT NOT NULL,
        title TEXT NOT NULL,
        artist TEXT,
        album TEXT,
        album_id TEXT,
        status TEXT NOT NULL DEFAULT 'queued',
        progress REAL NOT NULL DEFAULT 0,
        error TEXT,
        file_path TEXT,
        meta TEXT NOT NULL DEFAULT '{}',
        updated_at REAL NOT NULL
    );
    CREATE INDEX job_items_job ON job_items(job_id, position);
    CREATE INDEX job_items_status ON job_items(status);
    """,
    # 2: one entry per app install (signing in again replaces it)
    """
    ALTER TABLE devices ADD COLUMN install_id TEXT;
    CREATE INDEX devices_install ON devices(install_id);
    """,
    # 3: a download can go to another location than its profile's
    """
    ALTER TABLE jobs ADD COLUMN location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL;
    """,
    # 4: metadata review before a download, the file library, file names that follow the rules
    """
    ALTER TABLE jobs ADD COLUMN review INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE locations ADD COLUMN rename_all INTEGER NOT NULL DEFAULT 0;
    CREATE TABLE library_files (
        id INTEGER PRIMARY KEY,
        location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
        path TEXT NOT NULL,
        target_path TEXT,
        size INTEGER NOT NULL DEFAULT 0,
        mtime REAL NOT NULL DEFAULT 0,
        format TEXT,
        duration REAL,
        bitrate INTEGER,
        title TEXT,
        artist TEXT,
        albumartist TEXT,
        album TEXT,
        track INTEGER,
        track_total INTEGER,
        disc INTEGER,
        disc_total INTEGER,
        year TEXT,
        genre TEXT,
        label TEXT,
        isrc TEXT,
        video_id TEXT,
        has_cover INTEGER NOT NULL DEFAULT 0,
        has_lyrics INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
        playlist TEXT,
        position INTEGER,
        added_at REAL NOT NULL,
        scanned_at REAL NOT NULL,
        UNIQUE (location_id, path)
    );
    CREATE INDEX library_artist ON library_files(artist COLLATE NOCASE);
    CREATE INDEX library_album ON library_files(album COLLATE NOCASE);
    CREATE INDEX library_video ON library_files(video_id);
    CREATE TABLE duplicate_ignores (
        key TEXT PRIMARY KEY,
        created_at REAL NOT NULL
    );
    UPDATE profiles SET album_template = '{artist} - {title}'
        WHERE album_template = '{albumartist}/{album}[ ({year})]/[{disc}-]{track:02} {title}';
    UPDATE profiles SET single_template = '{artist} - {title}' WHERE single_template = '{artist}/{album}/{title}';
    UPDATE profiles SET playlist_template = 'Playlists/{playlist}/{artist} - {title}'
        WHERE playlist_template = 'Playlists/{playlist}/{position:03} {artist} - {title}';
    """,
]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def tx():
    """Transaction context: yields a connection, commits on success."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init() -> None:
    config.ensure_dirs()
    conn = _connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        r = conn.execute("SELECT version FROM schema_version").fetchone()
        version = r[0] if r else 0
        if not r:
            conn.execute("INSERT INTO schema_version (version) VALUES (0)")
        for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.executescript("BEGIN;" + sql + f"; UPDATE schema_version SET version = {i}; COMMIT;")
    finally:
        conn.close()


def rows(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    finally:
        conn.close()


def row(sql: str, params: Iterable[Any] = ()) -> dict | None:
    conn = _connect()
    try:
        r = conn.execute(sql, tuple(params)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def scalar(sql: str, params: Iterable[Any] = ()) -> Any:
    conn = _connect()
    try:
        r = conn.execute(sql, tuple(params)).fetchone()
        return r[0] if r else None
    finally:
        conn.close()


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    """Run a write statement; returns lastrowid."""
    conn = _connect()
    try:
        return conn.execute(sql, tuple(params)).lastrowid
    finally:
        conn.close()


def update(table: str, id_: Any, fields: dict, key: str = "id") -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE {table} SET {cols} WHERE {key} = ?", [*fields.values(), id_])


def insert(table: str, fields: dict) -> int:
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    return execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(fields.values()))


def now() -> float:
    return time.time()


def jload(s: str | None, default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return default


def jdump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
