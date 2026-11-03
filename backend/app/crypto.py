"""Encryption at rest for cookies, proxy credentials and API keys."""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

from . import config

_fernet: Fernet | None = None


def _key() -> bytes:
    if config.APP_SECRET:
        raw = config.APP_SECRET.encode()
    else:
        if not config.SECRET_FILE.exists():
            config.SECRET_FILE.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
            os.chmod(config.SECRET_FILE, 0o600)
        raw = config.SECRET_FILE.read_bytes().strip()
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())


def fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_key())
    return _fernet


def encrypt(text: str) -> str:
    return fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Cannot decrypt stored secret (APP_SECRET changed?)") from e
