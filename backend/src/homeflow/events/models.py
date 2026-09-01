"""Normalised internal events (CLAUDE.md section 31)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    DEVICE_DISCOVERED = "DeviceDiscovered"
    DEVICE_AVAILABILITY_CHANGED = "DeviceAvailabilityChanged"
    DEVICE_STATE_CHANGED = "DeviceStateChanged"

    COMMAND_REQUESTED = "CommandRequested"
    COMMAND_STARTED = "CommandStarted"
    COMMAND_SUCCEEDED = "CommandSucceeded"
    COMMAND_FAILED = "CommandFailed"
    COMMAND_TIMED_OUT = "CommandTimedOut"

    DOORBELL_PRESSED = "DoorbellPressed"
    MOTION_DETECTED = "MotionDetected"

    APPLIANCE_PROGRAM_STARTED = "ApplianceProgramStarted"
    APPLIANCE_PROGRAM_FINISHED = "ApplianceProgramFinished"

    POOL_TEMPERATURE_CHANGED = "PoolTemperatureChanged"
    POOL_TARGET_TEMPERATURE_CHANGED = "PoolTargetTemperatureChanged"

    THERMOSTAT_STATE_CHANGED = "ThermostatStateChanged"


class DomainEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: EventType
    occurred_at: datetime
    device_id: UUID | None = None
    command_id: UUID | None = None
    correlation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
