"""Credential verification for registered clients.

Phase 1 supports a single development credential supplied through the
environment. The registration and Secure-Enclave challenge flow described in
CLAUDE.md sections 13 and 38 is a prerequisite for the Nuki phase and is not
implemented yet; until then the registry simply has no clients in production and
every request is rejected. Failing closed is intentional.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Sequence

from homeflow.auth.models import Principal, RegisteredClient
from homeflow.config.settings import Environment, Settings
from homeflow.devices.identity import derive_uuid
from homeflow.errors import UnauthenticatedError
from homeflow.log import get_logger

_logger = get_logger(__name__)


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ClientRegistry:
    __slots__ = ("_clients",)

    def __init__(self, clients: Sequence[RegisteredClient] = ()) -> None:
        self._clients = tuple(clients)

    @property
    def is_empty(self) -> bool:
        return not self._clients

    def authenticate(self, presented_token: str | None) -> Principal:
        """Return the principal for a credential, or raise ``UnauthenticatedError``.

        The digest is always computed and every candidate is always compared so
        that response timing does not reveal whether any client is configured.
        """
        digest = _sha256_hex(presented_token or "")
        match: RegisteredClient | None = None
        for client in self._clients:
            if secrets.compare_digest(digest, client.token_sha256) and not client.revoked:
                match = client
        if match is None or not presented_token:
            raise UnauthenticatedError
        return match.principal()


def build_client_registry(settings: Settings) -> ClientRegistry:
    """Build the registry for the current environment.

    ``Settings`` already refuses to start with a development credential in
    production, so this function cannot widen access by accident.
    """
    token = settings.dev_client_token
    if token is None or settings.env is Environment.PRODUCTION:
        _logger.warning(
            "auth.no_clients_registered",
            env=settings.env.value,
            note="all requests will be rejected until a client is registered",
        )
        return ClientRegistry()

    salt = settings.effective_id_salt
    client = RegisteredClient(
        client_id=derive_uuid(salt, "client", "development"),
        user_id=derive_uuid(salt, "user", "development"),
        display_name="Development client",
        token_sha256=_sha256_hex(token.get_secret_value()),
    )
    _logger.info("auth.development_client_registered", env=settings.env.value)
    return ClientRegistry([client])
