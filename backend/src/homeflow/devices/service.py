"""Device state synchronisation and canonical event emission."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from homeflow.capabilities import DeviceKind
from homeflow.clock import Clock
from homeflow.devices.models import Availability, Device, ProgramState, Room, StateSource
from homeflow.devices.registry import DeviceRegistry
from homeflow.events.bus import EventBus
from homeflow.events.models import DomainEvent, EventType
from homeflow.integrations.base.models import ProviderDeviceRef, ProviderState
from homeflow.integrations.base.provider import DeviceProvider
from homeflow.log import get_logger

_logger = get_logger(__name__)


class DeviceService:
    """Owns the canonical device view and turns provider updates into events."""

    __slots__ = ("_bus", "_clock", "_registry")

    def __init__(self, registry: DeviceRegistry, bus: EventBus, clock: Clock) -> None:
        self._registry = registry
        self._bus = bus
        self._clock = clock

    @property
    def registry(self) -> DeviceRegistry:
        return self._registry

    async def bootstrap(self, providers: Sequence[DeviceProvider]) -> None:
        """Discover devices and take an initial state snapshot."""
        for provider in providers:
            for provider_device in await provider.discover_devices():
                state = await provider.get_state(provider_device.ref)
                device = self._registry.register(provider_device, state)
                self._bus.publish(
                    DomainEvent(
                        type=EventType.DEVICE_DISCOVERED,
                        occurred_at=self._clock.now(),
                        device_id=device.id,
                    )
                )
                _logger.info(
                    "device.discovered",
                    provider=provider.name,
                    homeflow_device_id=str(device.id),
                    kind=device.kind.value,
                )

    def ingest(
        self,
        ref: ProviderDeviceRef,
        provider_state: ProviderState,
        *,
        source: StateSource,
        correlation_id: str | None = None,
    ) -> Device | None:
        """Apply a provider observation and publish the resulting events."""
        device_id = self._registry.id_for_ref(ref)
        if device_id is None:
            return None
        before = self._registry.require(device_id)
        after = self._registry.apply(
            device_id,
            state=provider_state.state,
            availability=provider_state.availability,
            observed_at=provider_state.observed_at,
            source=source,
        )
        self._publish_transitions(before, after, correlation_id=correlation_id)
        return after

    def _publish_transitions(
        self,
        before: Device,
        after: Device,
        *,
        correlation_id: str | None,
    ) -> None:
        now = self._clock.now()

        def emit(event_type: EventType) -> None:
            self._bus.publish(
                DomainEvent(
                    type=event_type,
                    occurred_at=now,
                    device_id=after.id,
                    correlation_id=correlation_id,
                )
            )

        if before.availability is not after.availability:
            emit(EventType.DEVICE_AVAILABILITY_CHANGED)
        if before.state == after.state:
            return

        emit(EventType.DEVICE_STATE_CHANGED)

        if after.kind is DeviceKind.POOL:
            if before.state.current_temperature_c != after.state.current_temperature_c:
                emit(EventType.POOL_TEMPERATURE_CHANGED)
            if before.state.target_temperature_c != after.state.target_temperature_c:
                emit(EventType.POOL_TARGET_TEMPERATURE_CHANGED)

        appliance = after.kind in (DeviceKind.WASHING_MACHINE, DeviceKind.DISHWASHER)
        if appliance and before.state.program is not after.state.program:
            if after.state.program is ProgramState.RUNNING:
                emit(EventType.APPLIANCE_PROGRAM_STARTED)
            elif after.state.program is ProgramState.FINISHED:
                emit(EventType.APPLIANCE_PROGRAM_FINISHED)

    def list_devices(self) -> list[Device]:
        return self._registry.list_devices()

    def get(self, device_id: UUID) -> Device:
        return self._registry.require(device_id)

    def rooms(self) -> list[Room]:
        return self._registry.rooms()

    def is_reachable(self, device: Device) -> bool:
        return device.availability is not Availability.OFFLINE
