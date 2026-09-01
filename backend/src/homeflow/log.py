"""Structured, redaction-aware logging.

CLAUDE.md section 44 forbids household identifiers, credentials and raw provider
payloads from reaching logs. Redaction is applied centrally here so that no call
site can accidentally leak a value, and it is covered by unit tests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

REDACTED: Final = "[redacted]"

#: Field names whose values never appear in a log record.
SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "client_secret",
        "secret",
        "password",
        "passwd",
        "api_key",
        "apikey",
        "cookie",
        "set-cookie",
        "mac",
        "bssid",
        "ssid",
        "uid",
        "serial",
        "serial_number",
        "device_uid",
        "entity_id",
        "provider_entity_id",
        "provider_device_id",
        "provider_id",
        "email",
        "address",
        "latitude",
        "longitude",
        "resident",
        "resident_name",
        "camera_url",
        "stream_url",
        "snapshot_url",
        "signed_url",
        "tailnet",
        "hostname",
        "host",
        "ip",
        "ip_address",
    }
)

_SENSITIVE_SUFFIXES: Final[tuple[str, ...]] = (
    "_token",
    "_secret",
    "_password",
    "_key",
    "_uid",
    "_mac",
    "_email",
    "_url",
)

_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"),  # MAC address
    re.compile(r"\beyJ[A-Za-z0-9._-]{20,}"),  # JWT / Home Assistant long-lived token
    re.compile(r"(?i)\btskey-[a-z]+-[A-Za-z0-9]{6,}"),  # Tailscale auth key
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),  # IPv4 literal
)

_PSEUDONYM_SALT: Final[bytes] = secrets.token_bytes(32)


def pseudonymize(value: str) -> str:
    """Return a stable per-process pseudonym for an operationally needed identifier.

    The mapping is one-way and the salt never leaves the process, so correlating
    two log lines stays possible while the original identifier does not leak.
    """
    digest = hmac.new(_PSEUDONYM_SALT, value.encode("utf-8"), hashlib.sha256)
    return f"px_{digest.hexdigest()[:16]}"


def _scrub_text(value: str) -> str:
    for pattern in _PATTERNS:
        value = pattern.sub(REDACTED, value)
    return value


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in SENSITIVE_KEYS or lowered.endswith(_SENSITIVE_SUFFIXES)


def redact(value: Any) -> Any:
    """Recursively remove secrets and household identifiers from a log payload."""
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _is_sensitive_key(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [redact(item) for item in value]
    if isinstance(value, bytes):
        return REDACTED
    return value


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per record with the fields allowed by CLAUDE.md 44."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
        }
        fields = getattr(record, "homeflow_fields", None)
        if isinstance(fields, Mapping):
            payload.update(fields)
        if record.exc_info is not None:
            exc_type = record.exc_info[0]
            payload["error_type"] = exc_type.__name__ if exc_type is not None else "unknown"
        return json.dumps(payload, default=str, separators=(",", ":"))


class StructuredLogger:
    """Thin wrapper that forces every extra field through :func:`redact`."""

    __slots__ = ("_logger",)

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _log(self, level: int, event: str, fields: Mapping[str, Any]) -> None:
        if not self._logger.isEnabledFor(level):
            return
        self._logger.log(level, event, extra={"homeflow_fields": redact(dict(fields))})

    def debug(self, event: str, **fields: Any) -> None:
        self._log(logging.DEBUG, event, fields)

    def info(self, event: str, **fields: Any) -> None:
        self._log(logging.INFO, event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._log(logging.WARNING, event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._log(logging.ERROR, event, fields)

    def exception(self, event: str, **fields: Any) -> None:
        self._logger.exception(event, extra={"homeflow_fields": redact(dict(fields))})


def get_logger(name: str) -> StructuredLogger:
    return StructuredLogger(logging.getLogger(name))


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
