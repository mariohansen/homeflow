"""Wire format for the canonical API.

Response models are built explicitly from domain objects. Nothing here accepts a
``ProviderDeviceRef``, so a provider identifier cannot reach a client even if a
future domain object starts carrying one (see docs/security/privacy-model.md).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from homeflow.audit.log import AuditEntry
from homeflow.auth.models import Principal
from homeflow.capabilities import Capability, DeviceKind
from homeflow.commands.models import Action, Command, CommandStatus, RiskClass
from homeflow.devices.models import (
    Availability,
    Device,
    DeviceConstraints,
    DeviceState,
    LockState,
    PlaybackState,
    ProgramState,
    Room,
    StateSource,
)
from homeflow.schedules.models import Schedule, ScheduleKind, ScheduleStatus


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class DeviceStateResponse(ApiModel):
    power: bool | None = None
    brightness: int | None = None
    current_temperature_c: float | None = None
    target_temperature_c: float | None = None
    heater: bool | None = None
    filter_pump: bool | None = None
    filter_last_started_at: datetime | None = None
    bubbles: bool | None = None
    control_panel_lock: bool | None = None
    lock_state: LockState | None = None
    volume: int | None = None
    playback: PlaybackState | None = None
    program: ProgramState | None = None
    program_name: str | None = None
    remaining_seconds: int | None = None

    @classmethod
    def from_domain(cls, state: DeviceState) -> DeviceStateResponse:
        return cls(**state.model_dump())


class DeviceConstraintsResponse(ApiModel):
    target_temperature_min_c: float | None = None
    target_temperature_max_c: float | None = None
    target_temperature_step_c: float | None = None

    @classmethod
    def from_domain(cls, constraints: DeviceConstraints) -> DeviceConstraintsResponse:
        return cls(**constraints.model_dump())


class DeviceResponse(ApiModel):
    id: UUID
    display_name: str
    kind: DeviceKind
    room_id: UUID | None
    room_name: str | None
    capabilities: tuple[Capability, ...]
    availability: Availability
    state: DeviceStateResponse
    constraints: DeviceConstraintsResponse
    state_observed_at: datetime
    last_seen_at: datetime
    state_source: StateSource
    #: True when the held state can no longer be presented as current.
    is_stale: bool

    @classmethod
    def from_domain(
        cls,
        device: Device,
        *,
        now: datetime,
        stale_after_seconds: int,
    ) -> DeviceResponse:
        return cls(
            id=device.id,
            display_name=device.display_name,
            kind=device.kind,
            room_id=device.room_id,
            room_name=device.room_name,
            capabilities=device.capabilities,
            availability=device.availability,
            state=DeviceStateResponse.from_domain(device.state),
            constraints=DeviceConstraintsResponse.from_domain(device.constraints),
            state_observed_at=device.state_observed_at,
            last_seen_at=device.last_seen_at,
            state_source=device.state_source,
            is_stale=device.is_stale(now=now, stale_after_seconds=stale_after_seconds),
        )


class ScheduleResponse(ApiModel):
    id: UUID
    device_id: UUID
    kind: ScheduleKind
    action: Action
    #: What will be applied when the timer runs out.
    desired: bool
    created_at: datetime
    due_at: datetime
    status: ScheduleStatus
    failure_code: str | None = None

    @classmethod
    def from_domain(cls, schedule: Schedule) -> ScheduleResponse:
        return cls(
            id=schedule.id,
            device_id=schedule.device_id,
            kind=schedule.kind,
            action=schedule.action,
            desired=schedule.desired,
            created_at=schedule.created_at,
            due_at=schedule.due_at,
            status=schedule.status,
            failure_code=schedule.failure_code,
        )


class CreateScheduleRequest(ApiModel):
    """Ask for one action, once, in a bounded number of hours.

    The moment is expressed as a delay rather than an absolute time so that a
    client with a wrong clock cannot schedule a physical write into next week.
    """

    action: Action
    kind: ScheduleKind
    hours: float = Field(gt=0.0, le=48.0)


class RoomResponse(ApiModel):
    id: UUID
    name: str

    @classmethod
    def from_domain(cls, room: Room) -> RoomResponse:
        return cls(id=room.id, name=room.name)


class SubmitCommandRequest(ApiModel):
    action: Action
    parameters: dict[str, Any] = Field(default_factory=dict)


class CommandResponse(ApiModel):
    id: UUID
    device_id: UUID
    action: Action
    parameters: dict[str, Any]
    risk_class: RiskClass
    status: CommandStatus
    failure_code: str | None
    correlation_id: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    @classmethod
    def from_domain(cls, command: Command) -> CommandResponse:
        return cls(
            id=command.id,
            device_id=command.device_id,
            action=command.action,
            parameters=command.parameters,
            risk_class=command.risk_class,
            status=command.status,
            failure_code=command.failure_code,
            correlation_id=command.correlation_id,
            created_at=command.created_at,
            started_at=command.started_at,
            completed_at=command.completed_at,
        )


class MeResponse(ApiModel):
    user_id: UUID
    client_id: UUID
    display_name: str
    demo_mode: bool

    @classmethod
    def from_principal(cls, principal: Principal, *, demo_mode: bool) -> MeResponse:
        return cls(
            user_id=principal.user_id,
            client_id=principal.client_id,
            display_name=principal.display_name,
            demo_mode=demo_mode,
        )


class WebSocketTicketResponse(ApiModel):
    ticket: str
    expires_in_seconds: int


class ActivityEntryResponse(ApiModel):
    id: UUID
    occurred_at: datetime
    event: str
    device_id: UUID | None
    command_id: UUID | None
    action: str | None
    risk_class: RiskClass | None
    outcome: str | None

    @classmethod
    def from_domain(cls, entry: AuditEntry) -> ActivityEntryResponse:
        return cls(
            id=entry.id,
            occurred_at=entry.occurred_at,
            event=entry.event,
            device_id=entry.device_id,
            command_id=entry.command_id,
            action=entry.action,
            risk_class=entry.risk_class,
            outcome=entry.outcome,
        )
