"""Semantic commands (CLAUDE.md sections 14, 28 and 29).

Only desired-state commands exist; there is no toggle and no raw passthrough, so
a repeated request cannot flip a device into an unintended state.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from homeflow.devices.models import LockState


class RiskClass(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class CommandStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"
    #: The device may or may not have executed the command. Never reported as a
    #: failure, because a physical device can act after the gateway gave up.
    UNKNOWN = "UNKNOWN"


class Action(StrEnum):
    SET_POWER = "SET_POWER"
    SET_BRIGHTNESS = "SET_BRIGHTNESS"
    SET_VOLUME = "SET_VOLUME"
    SET_PLAYBACK = "SET_PLAYBACK"
    SET_TARGET_TEMPERATURE = "SET_TARGET_TEMPERATURE"
    SET_HEATER = "SET_HEATER"
    SET_FILTER = "SET_FILTER"
    SET_BUBBLES = "SET_BUBBLES"
    SET_CONTROL_PANEL_LOCK = "SET_CONTROL_PANEL_LOCK"
    SET_LOCK_STATE = "SET_LOCK_STATE"
    UNLATCH = "UNLATCH"


class PlaybackCommand(StrEnum):
    PLAY = "PLAY"
    PAUSE = "PAUSE"


class CommandParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OnOffParams(CommandParams):
    on: bool


class BrightnessParams(CommandParams):
    brightness: int = Field(ge=0, le=100)


class VolumeParams(CommandParams):
    volume: int = Field(ge=0, le=100)


class PlaybackParams(CommandParams):
    playback: PlaybackCommand


class TargetTemperatureParams(CommandParams):
    """Absolute sanity bounds only.

    The authoritative range is the per-device ``DeviceConstraints`` published by
    the adapter and enforced by the command service.
    """

    celsius: float = Field(ge=0.0, le=60.0)


class LockStateParams(CommandParams):
    desired: LockState

    @field_validator("desired")
    @classmethod
    def _reject_unknown(cls, value: LockState) -> LockState:
        if value is LockState.UNKNOWN:
            raise ValueError("desired lock state must be LOCKED or UNLOCKED")
        return value


class NoParams(CommandParams):
    pass


class Command(BaseModel):
    """Immutable command record; transitions create a new instance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    device_id: UUID
    requested_by_user_id: UUID
    requested_by_client_id: UUID
    action: Action
    parameters: dict[str, Any]
    risk_class: RiskClass
    correlation_id: str
    created_at: datetime
    expires_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    status: CommandStatus = CommandStatus.PENDING
    failure_code: str | None = None

    def with_status(
        self,
        status: CommandStatus,
        *,
        at: datetime,
        failure_code: str | None = None,
    ) -> Command:
        updates: dict[str, Any] = {"status": status, "failure_code": failure_code}
        if status is CommandStatus.RUNNING:
            updates["started_at"] = at
        else:
            updates["completed_at"] = at
        return self.model_copy(update=updates)
