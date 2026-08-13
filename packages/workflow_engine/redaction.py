"""Keep runtime capabilities out of workflow diagnostics and artifacts."""

from __future__ import annotations

from typing import Any

REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "cookies",
    "encrypted_password",
    "encrypted_value",
    "encrypted_storage_state",
    "password",
    "proxy_authorization",
    "secret",
    "secrets",
    "storage_state",
    "token",
}


def redact_text(value: str, secret_values: list[str] | tuple[str, ...] | set[str]) -> str:
    result = value
    for secret in sorted({item for item in secret_values if len(item) >= 3}, key=len, reverse=True):
        result = result.replace(secret, REDACTED)
    return result


def redact_value(value: Any, secret_values: list[str] | tuple[str, ...] | set[str]) -> Any:
    """Return a serialisable copy with known values and sensitive fields masked."""
    if isinstance(value, str):
        return redact_text(value, secret_values)
    if isinstance(value, list):
        return [redact_value(item, secret_values) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item, secret_values) for item in value]
    if isinstance(value, dict):
        return {
            key: REDACTED if str(key).lower() in SENSITIVE_KEYS else redact_value(item, secret_values)
            for key, item in value.items()
        }
    return value


def redact_artifact_bytes(data: bytes, content_type: str, secret_values: list[str] | tuple[str, ...] | set[str]) -> bytes:
    """Mask credential values in textual evidence before durable storage."""
    if not any(token in content_type.lower() for token in ("json", "text", "xml", "html", "javascript")):
        return data
    try:
        return redact_text(data.decode("utf-8"), secret_values).encode("utf-8")
    except UnicodeDecodeError:
        return data
