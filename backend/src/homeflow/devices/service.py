"""Device state synchronisation and canonical event emission."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from homeflow.capabilities import DeviceKind
from homeflow.clock import Clock
from homeflow.devices.models import (
    Availability,
    Device,
    DeviceState,
    ProgramState,
    Room,
    StateSource,
)
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
            state=self._with_derived(before, provider_state.state),
            availability=provider_state.availability,
            observed_at=provider_state.observed_at,
            source=source,
        )
        self._publish_transitions(before, after, correlation_id=correlation_id)
        return after

    def _with_derived(self, before: Device, observed: DeviceState) -> DeviceState:
        """Add what the gateway knows and the adapter does not.

        A controller reports whether the pump runs, not when it started. Seeing
        the transition is the only way to answer "last filtered", so the moment
        is recorded here as the pump comes on, and carried forward across every
        later observation -- an adapter never reports it, so without this the
        answer would be lost the instant the pump stopped.

        A pump already running when the gateway starts leaves this empty: the
        honest answer is that we did not see it start.
        """
        if observed.filter_pump and not before.state.filter_pump:
            return observed.model_copy(update={"filter_last_started_at": self._clock.now()})
        remembered = before.state.filter_last_started_at
        if remembered is not None and observed.filter_last_started_at is None:
            return observed.model_copy(update={"filter_last_started_at": remembered})
        return observed

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
