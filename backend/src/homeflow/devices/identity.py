"""Derivation of public HomeFlow identifiers from provider identifiers.

CLAUDE.md section 18 forbids exposing provider entity ids. A plain hash would be
brute-forceable because provider ids are low entropy, so the mapping is keyed
with a deployment secret. The result is stable across restarts without needing a
database, and cannot be reversed by a reader of the public repository.
"""

from __future__ import annotations

import hashlib
import hmac
from uuid import UUID


def derive_uuid(salt: str, *parts: str) -> UUID:
    message = "\x00".join(parts).encode("utf-8")
    digest = hmac.new(salt.encode("utf-8"), message, hashlib.sha256).digest()
    return UUID(bytes=digest[:16], version=5)


def device_uuid(salt: str, provider: str, provider_device_id: str) -> UUID:
    return derive_uuid(salt, "device", provider, provider_device_id)


def room_uuid(salt: str, room_name: str) -> UUID:
    return derive_uuid(salt, "room", room_name.strip().casefold())
