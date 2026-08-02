import base64
import hashlib
import hmac
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.fernet import Fernet

from app.config import get_settings

settings = get_settings()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256$310000${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_b64, digest_b64 = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(rounds))
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


def create_token(subject: str, kind: str, expires_delta: timedelta, roles: list[str]) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": kind,
        "roles": roles,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm="HS256")


def create_access_token(subject: str, roles: list[str]) -> str:
    return create_token(subject, "access", timedelta(minutes=settings.access_token_minutes), roles)


def create_refresh_token(subject: str, roles: list[str]) -> str:
    return create_token(subject, "refresh", timedelta(days=settings.refresh_token_days), roles)


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    payload = jwt.decode(token, settings.app_secret_key, algorithms=["HS256"])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("Wrong token type")
    return payload


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.encryption_master_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def mask_secret(value: str) -> str:
    return "••••••••" + value[-4:] if len(value) >= 4 else "••••••••"
