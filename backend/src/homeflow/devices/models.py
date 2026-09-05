"""Canonical device model (see docs/adr/0008-canonical-capability-model.md).

This module deliberately knows nothing about providers: it must be impossible
for a provider identifier to reach a client through a domain object.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from homeflow.capabilities import Capability, DeviceKind


class Availability(StrEnum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


class LockState(StrEnum):
    LOCKED = "LOCKED"
    UNLOCKED = "UNLOCKED"
    UNKNOWN = "UNKNOWN"


class PlaybackState(StrEnum):
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    UNKNOWN = "UNKNOWN"


class ProgramState(StrEnum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    UNKNOWN = "UNKNOWN"


class StateSource(StrEnum):
    """Where the currently held state came from — surfaced to clients."""

    PROVIDER_SNAPSHOT = "PROVIDER_SNAPSHOT"
    PROVIDER_EVENT = "PROVIDER_EVENT"
    COMMAND_RESULT = "COMMAND_RESULT"


class DeviceState(BaseModel):
    """Normalised state. Every field is optional: absent means "not reported"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    power: bool | None = None
    brightness: int | None = Field(default=None, ge=0, le=100)

    current_temperature_c: float | None = None
    target_temperature_c: float | None = None
    heater: bool | None = None
    filter_pump: bool | None = None
    #: Derived, not reported: when the gateway last saw the pump start. Held in
    #: memory, so a restart forgets it until the pump next runs.
    filter_last_started_at: datetime | None = None
    bubbles: bool | None = None
    control_panel_lock: bool | None = None

    lock_state: LockState | None = None

    volume: int | None = Field(default=None, ge=0, le=100)
    playback: PlaybackState | None = None

    program: ProgramState | None = None
    program_name: str | None = None
    remaining_seconds: int | None = Field(default=None, ge=0)

    def merged_with(self, other: DeviceState) -> DeviceState:
        """Overlay reported fields of ``other`` onto this state."""
        updates = other.model_dump(exclude_none=True)
        return self.model_copy(update=updates)


class DeviceConstraints(BaseModel):
    """Authoritative per-device limits, declared by the adapter.

    Bounds must come from verified device behaviour, never from a guess in the
    API layer (see docs/adr/0006-bestway-direct-local-adapter.md).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_temperature_min_c: float | None = None
    target_temperature_max_c: float | None = None
    target_temperature_step_c: float | None = None


class Device(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    display_name: str
    kind: DeviceKind
    room_id: UUID | None = None
    room_name: str | None = None
    capabilities: tuple[Capability, ...] = ()
    availability: Availability = Availability.UNKNOWN
    state: DeviceState = DeviceState()
    constraints: DeviceConstraints = DeviceConstraints()
    state_observed_at: datetime
    last_seen_at: datetime
    state_source: StateSource = StateSource.PROVIDER_SNAPSHOT

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def is_stale(self, *, now: datetime, stale_after_seconds: int) -> bool:
        if self.availability is Availability.OFFLINE:
            return True
        return (now - self.state_observed_at).total_seconds() > stale_after_seconds


class Room(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    name: str
