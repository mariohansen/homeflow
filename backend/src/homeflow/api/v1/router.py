"""Version 1 of the canonical HomeFlow API (see README.md).

Only semantic HomeFlow actions are exposed. There is no provider passthrough
route, and no endpoint accepts a provider identifier or a provider URL.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from homeflow.api.deps import CommandPrincipal, CorrelationId, CurrentContainer, CurrentPrincipal
from homeflow.api.schemas import (
    ActivityEntryResponse,
    CommandResponse,
    CreateScheduleRequest,
    DeviceResponse,
    MeResponse,
    RoomResponse,
    ScheduleResponse,
    SubmitCommandRequest,
)
from homeflow.container import Container

router = APIRouter(prefix="/v1")


def _device_response(container: Container, device_id: UUID) -> DeviceResponse:
    device = container.devices.get(device_id)
    return DeviceResponse.from_domain(
        device,
        now=container.clock.now(),
        stale_after_seconds=container.settings.stale_after_seconds,
    )


@router.get("/me", response_model=MeResponse)
def read_me(principal: CurrentPrincipal, container: CurrentContainer) -> MeResponse:
    return MeResponse.from_principal(principal, demo_mode=container.settings.demo_mode)


@router.get("/rooms", response_model=list[RoomResponse])
def list_rooms(
    principal: CurrentPrincipal,
    container: CurrentContainer,
) -> list[RoomResponse]:
    return [RoomResponse.from_domain(room) for room in container.devices.rooms()]


@router.get("/devices", response_model=list[DeviceResponse])
def list_devices(
    principal: CurrentPrincipal,
    container: CurrentContainer,
) -> list[DeviceResponse]:
    now = container.clock.now()
    stale_after = container.settings.stale_after_seconds
    return [
        DeviceResponse.from_domain(device, now=now, stale_after_seconds=stale_after)
        for device in container.devices.list_devices()
    ]


@router.get("/devices/{device_id}", response_model=DeviceResponse)
def read_device(
    device_id: UUID,
    principal: CurrentPrincipal,
    container: CurrentContainer,
) -> DeviceResponse:
    return _device_response(container, device_id)


@router.post("/devices/{device_id}/commands", response_model=CommandResponse)
async def submit_command(
    device_id: UUID,
    payload: SubmitCommandRequest,
    principal: CommandPrincipal,
    container: CurrentContainer,
    correlation_id: CorrelationId,
) -> CommandResponse:
    """Submit one semantic command and return its settled state.

    The call is bounded by the command and reconciliation timeouts, so a stuck
    device cannot hold the connection open indefinitely.
    """
    command = await container.commands.submit(
        principal,
        device_id,
        payload.action,
        payload.parameters,
        correlation_id=correlation_id,
    )
    return CommandResponse.from_domain(command)


@router.get("/devices/{device_id}/schedules", response_model=list[ScheduleResponse])
def list_schedules(
    device_id: UUID,
    principal: CurrentPrincipal,
    container: CurrentContainer,
) -> list[ScheduleResponse]:
    # Raises if the device is unknown, so a timer listing cannot be used to
    # probe which identifiers exist.
    container.devices.get(device_id)
    return [
        ScheduleResponse.from_domain(item) for item in container.schedules.for_device(device_id)
    ]


@router.post("/devices/{device_id}/schedules", response_model=ScheduleResponse)
async def create_schedule(
    device_id: UUID,
    payload: CreateScheduleRequest,
    principal: CommandPrincipal,
    container: CurrentContainer,
    correlation_id: CorrelationId,
) -> ScheduleResponse:
    """Arm one timer.

    A timer is the only unattended physical write in HomeFlow. It is limited to
    functions the operator has released for this device, it fires once, and it
    goes through the same command pipeline as a tap (see
    docs/adr/0012-one-shot-timers.md).
    """
    schedule = await container.schedules.create(
        principal,
        device_id,
        payload.action,
        payload.kind,
        payload.hours,
        correlation_id=correlation_id,
    )
    return ScheduleResponse.from_domain(schedule)


@router.delete("/schedules/{schedule_id}", response_model=ScheduleResponse)
def cancel_schedule(
    schedule_id: UUID,
    principal: CommandPrincipal,
    container: CurrentContainer,
) -> ScheduleResponse:
    return ScheduleResponse.from_domain(container.schedules.cancel(principal, schedule_id))


@router.get("/commands/{command_id}", response_model=CommandResponse)
def read_command(
    command_id: UUID,
    principal: CurrentPrincipal,
    container: CurrentContainer,
) -> CommandResponse:
    return CommandResponse.from_domain(container.commands.get(command_id))


@router.get("/activity", response_model=list[ActivityEntryResponse])
def read_activity(
    principal: CurrentPrincipal,
    container: CurrentContainer,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ActivityEntryResponse]:
    return [ActivityEntryResponse.from_domain(entry) for entry in container.audit.recent(limit)]
