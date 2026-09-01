"""Audit records (see SECURITY.md).

Entries reference HomeFlow identifiers only. Provider identifiers, tokens and
raw parameters of high-risk actions never enter the audit trail.

The in-memory sink is bounded and therefore not durable. A PostgreSQL sink
implementing the same protocol arrives with persistence in phase 2; see
docs/adr/0009-deferred-persistence.md.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from homeflow.commands.models import RiskClass


class AuditEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime
    event: str
    actor_user_id: UUID | None = None
    actor_client_id: UUID | None = None
    device_id: UUID | None = None
    command_id: UUID | None = None
    action: str | None = None
    risk_class: RiskClass | None = None
    outcome: str | None = None
    correlation_id: str | None = None
    #: Only bounded, non-sensitive scalars belong here.
    context: dict[str, Any] = Field(default_factory=dict)


class AuditSink(Protocol):
    def record(self, entry: AuditEntry) -> None: ...

    def recent(self, limit: int = 100) -> Iterable[AuditEntry]: ...


class InMemoryAuditLog:
    __slots__ = ("_entries",)

    def __init__(self, max_entries: int = 2000) -> None:
        self._entries: deque[AuditEntry] = deque(maxlen=max_entries)

    def record(self, entry: AuditEntry) -> None:
        self._entries.append(entry)

    def recent(self, limit: int = 100) -> list[AuditEntry]:
        if limit <= 0:
            return []
        return list(self._entries)[-limit:][::-1]
