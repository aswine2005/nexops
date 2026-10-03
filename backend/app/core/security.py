"""Password hashing, session tokens and webhook signature verification."""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import jwt

from app.config import get_settings

logger = logging.getLogger(__name__)

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode(),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


@lru_cache
def _signing_key() -> str:
    settings = get_settings()
    if settings.secret_key:
        return settings.secret_key
    if settings.is_production:
        raise RuntimeError("NEXOPS_SECRET_KEY must be set when NEXOPS_ENV=production.")
    logger.warning("NEXOPS_SECRET_KEY is not set; using an ephemeral key. Sessions end when the API restarts.")
    return secrets.token_urlsafe(48)


def create_access_token(user_id: str, role: str) -> tuple[str, datetime]:
    expires = datetime.now(timezone.utc) + timedelta(hours=get_settings().token_ttl_hours)
    token = jwt.encode({"sub": user_id, "role": role, "exp": expires, "iat": datetime.now(timezone.utc)}, _signing_key(), algorithm="HS256")
    return token, expires


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _signing_key(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def sign_payload(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(sign_payload(secret, body), signature.strip())
