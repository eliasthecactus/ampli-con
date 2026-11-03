"""The one web user, browser sessions, app devices and request guards."""
import hashlib
import hmac
import logging
import secrets
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request

from . import config, db

log = logging.getLogger("amplicon.auth")
ph = PasswordHasher()
COOKIE = "amplicon_session"
CSRF_HEADER = "amplicon"
_attempts: dict[str, deque] = defaultdict(deque)
MAX_ATTEMPTS = 8
WINDOW = 300
DEFAULT_USERNAME = "admin"


def hash_password(pw: str) -> str:
    return ph.hash(pw)


def verify_password(pw_hash: str, pw: str) -> bool:
    try:
        return ph.verify(pw_hash, pw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def validate_password(pw: str) -> None:
    if len(pw) < 8:
        raise ValueError("The password needs at least 8 characters")


def generate_password() -> str:
    # Readable: no look-alike characters, grouped for typing it off a log line.
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(18))
    return "-".join(raw[i:i + 6] for i in range(0, 18, 6))


def the_user() -> dict | None:
    return db.row("SELECT * FROM users ORDER BY id LIMIT 1")


def _banner(lines: list[str]) -> str:
    width = max(len(x) for x in lines) + 4
    bar = "=" * width
    return "\n".join(["", bar, *[f"  {x}" for x in lines], bar, ""])


def set_credentials(username: str, password: str, *, sign_out: bool) -> None:
    """Creates the user or changes its name and password. sign_out drops all browser sessions and app devices."""
    username = username.strip() or DEFAULT_USERNAME
    validate_password(password)
    with db.tx() as c:
        u = c.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        if u:
            c.execute("UPDATE users SET username = ?, password_hash = ?, password_changed_at = ? WHERE id = ?",
                      [username, hash_password(password), time.time(), u[0]])
        else:
            c.execute("INSERT INTO users (username, password_hash, created_at, password_changed_at) VALUES (?, ?, ?, ?)",
                      [username, hash_password(password), time.time(), time.time()])
        if sign_out:
            c.execute("DELETE FROM sessions")
            c.execute("DELETE FROM devices")


def bootstrap() -> str | None:
    """Runs at startup. Returns the generated password when one was created (also printed to the log)."""
    if config.env_user_set():
        u = the_user()
        if not u or u["username"] != config.ENV_USERNAME or not verify_password(u["password_hash"], config.ENV_PASSWORD):
            try:
                set_credentials(config.ENV_USERNAME, config.ENV_PASSWORD, sign_out=bool(u))
            except ValueError as e:
                raise SystemExit(f"AMPLICON_PASSWORD: {e}") from e
            log.info("Web user '%s' set from AMPLICON_USERNAME / AMPLICON_PASSWORD", config.ENV_USERNAME)
        return None
    if the_user():
        return None
    username = config.ENV_USERNAME or DEFAULT_USERNAME
    password = generate_password()
    set_credentials(username, password, sign_out=False)
    print(_banner([
        "Amplicon created the web user on this first start:",
        "",
        f"  Username: {username}",
        f"  Password: {password}",
        "",
        "It is shown only this once. Change it under Settings, or reset it with",
        "  docker exec -it amplicon amplicon reset-password",
    ]), flush=True)
    return password


def check_rate_limit(ip: str) -> None:
    q = _attempts[ip]
    now = time.time()
    while q and q[0] < now - WINDOW:
        q.popleft()
    if len(q) >= MAX_ATTEMPTS:
        raise HTTPException(429, "Too many sign-in attempts, try again in a few minutes")


def record_failure(ip: str) -> None:
    _attempts[ip].append(time.time())


def check_login(username: str, password: str, ip: str) -> dict:
    check_rate_limit(ip)
    u = db.row("SELECT * FROM users WHERE username = ?", [username.strip()])
    if not u or not verify_password(u["password_hash"], password):
        record_failure(ip)
        raise HTTPException(401, "Wrong username or password")
    return u


# ---------------------------------------------------------------- browser sessions

def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = db.now()
    db.insert("sessions", {"token": token, "user_id": user_id, "created_at": now,
                           "expires_at": now + config.SESSION_DAYS * 86400, "last_seen": now})
    db.execute("DELETE FROM sessions WHERE expires_at < ?", [now])
    return token


def session_user(token: str | None) -> dict | None:
    if not token:
        return None
    r = db.row("SELECT u.id, u.username, s.last_seen FROM sessions s JOIN users u ON u.id = s.user_id "
               "WHERE s.token = ? AND s.expires_at > ?", [token, db.now()])
    if not r:
        return None
    if db.now() - r["last_seen"] > 300:
        db.execute("UPDATE sessions SET last_seen = ? WHERE token = ?", [db.now(), token])
    return {"id": r["id"], "username": r["username"]}


# ---------------------------------------------------------------- app devices

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_device(name: str, ip: str | None, install_id: str | None = None) -> dict:
    """A signed-in app. The token authenticates requests, the secret signs stream links (never sent in URLs).
    An app that signs in again (same install id) replaces its old entry."""
    token = secrets.token_urlsafe(32)
    secret = secrets.token_urlsafe(32)
    install_id = (install_id or "").strip()[:100] or None
    if install_id:
        db.execute("DELETE FROM devices WHERE install_id = ?", [install_id])
    did = db.insert("devices", {"name": (name or "Ampli").strip()[:80], "token_hash": _token_hash(token),
                                "secret": secret, "created_at": db.now(), "last_seen": db.now(), "last_ip": ip,
                                "install_id": install_id})
    return {"id": did, "token": token, "secret": secret}


def device_for_token(token: str | None, ip: str | None = None) -> dict | None:
    if not token:
        return None
    d = db.row("SELECT id, name, secret, last_seen FROM devices WHERE token_hash = ?", [_token_hash(token)])
    if d and db.now() - (d["last_seen"] or 0) > 300:
        db.execute("UPDATE devices SET last_seen = ?, last_ip = ? WHERE id = ?", [db.now(), ip, d["id"]])
    return d


def sign(secret: str, path: str, exp: int) -> str:
    return hmac.new(secret.encode(), f"{path}:{exp}".encode(), hashlib.sha256).hexdigest()


def check_signature(request: Request) -> dict | None:
    """Stream and artwork links carry ?d=<device>&exp=<unix>&sig=<hmac(secret, path:exp)>, so AVPlayer needs no header."""
    q = request.query_params
    try:
        did, exp, sig = int(q.get("d", "")), int(q.get("exp", "")), q.get("sig", "")
    except ValueError:
        return None
    if exp < time.time():
        return None
    d = db.row("SELECT id, name, secret FROM devices WHERE id = ?", [did])
    if not d or not hmac.compare_digest(sign(d["secret"], request.url.path, exp), sig):
        return None
    return d


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else None


def require_user(request: Request) -> dict:
    """The web GUI: a session cookie, plus a custom header on writes against CSRF."""
    user = session_user(request.cookies.get(COOKIE))
    if not user:
        raise HTTPException(401, "Not signed in")
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-requested-with") != CSRF_HEADER:
        raise HTTPException(403, "Missing X-Requested-With header")
    return user


def require_client(request: Request) -> dict:
    """The web GUI or a signed-in app (Bearer token)."""
    token = _bearer(request)
    if token:
        d = device_for_token(token, client_ip(request))
        if not d:
            raise HTTPException(401, "This device was signed out. Sign in again.")
        return {"device_id": d["id"], "name": d["name"], "secret": d["secret"]}
    u = require_user(request)
    return {"user_id": u["id"], "name": "Web"}


def require_media(request: Request) -> dict:
    """Streams and artwork: Bearer, a signed link or the web session."""
    d = check_signature(request)
    if d:
        return {"device_id": d["id"], "name": d["name"]}
    return require_client(request)
