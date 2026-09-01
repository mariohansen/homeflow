"""Household identity model (see SECURITY.md).

Membership of the private VPN is not authorisation. Every request carries a
credential belonging to an explicitly registered client that can be revoked on
its own, without rotating anything else in the household.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated user and the client device the request came from."""

    user_id: UUID
    client_id: UUID
    display_name: str


class RegisteredClient(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    client_id: UUID
    user_id: UUID
    display_name: str
    #: SHA-256 of the bearer credential. The credential itself is never stored.
    token_sha256: str
    revoked: bool = False

    def principal(self) -> Principal:
        return Principal(
            user_id=self.user_id,
            client_id=self.client_id,
            display_name=self.display_name,
        )
