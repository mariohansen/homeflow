"""A configurable provider double for pipeline tests.

Lets a test choose the behaviour that matters — slow, failing, silently
non-applying — without any I/O.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

from homeflow.capabilities import Capability, DeviceKind
from homeflow.devices.models import Availability, DeviceConstraints, DeviceState
from homeflow.integrations.base.errors import ProviderError
from homeflow.integrations.base.models import (
    CommandOutcome,
    ProviderCommand,
    ProviderCommandResult,
    ProviderDevice,
    ProviderDeviceRef,
    ProviderEvent,
    ProviderState,
)

FAKE_PROVIDER = "fake"
FAKE_DEVICE_ID = "pool-1"


def fake_ref() -> ProviderDeviceRef:
    return ProviderDeviceRef(provider=FAKE_PROVIDER, provider_device_id=FAKE_DEVICE_ID)


class FakeProvider:
    """Pool-like device with switchable failure behaviour."""

    def __init__(
        self,
        *,
        execute_delay: float = 0.0,
        raise_error: ProviderError | None = None,
        outcome: CommandOutcome = CommandOutcome.APPLIED,
        apply_writes: bool = True,
        availability: Availability = Availability.ONLINE,
        extra_capabilities: tuple[Capability, ...] = (),
    ) -> None:
        self.execute_delay = execute_delay
        self.raise_error = raise_error
        self.outcome = outcome
        self.apply_writes = apply_writes
        self.availability = availability
        self.extra_capabilities = extra_capabilities
        self.execute_calls = 0
        self.state = DeviceState(
            current_temperature_c=25.0,
            target_temperature_c=30.0,
            heater=False,
            filter_pump=True,
            bubbles=False,
        )

    @property
    def name(self) -> str:
        return FAKE_PROVIDER

    async def discover_devices(self) -> Sequence[ProviderDevice]:
        return [
            ProviderDevice(
                ref=fake_ref(),
                suggested_name="Fake Pool",
                kind=DeviceKind.POOL,
                capabilities=(
                    Capability.CURRENT_TEMPERATURE,
                    Capability.TARGET_TEMPERATURE,
                    Capability.HEATING,
                    *self.extra_capabilities,
                ),
                room_hint="Terrace",
                constraints=DeviceConstraints(
                    target_temperature_min_c=20.0,
                    target_temperature_max_c=40.0,
                    target_temperature_step_c=0.5,
                ),
            )
        ]

    async def get_state(self, device_ref: ProviderDeviceRef) -> ProviderState:
        assert device_ref == fake_ref()
        return self._snapshot()

    async def execute(
        self,
        device_ref: ProviderDeviceRef,
        command: ProviderCommand,
    ) -> ProviderCommandResult:
        assert device_ref == fake_ref()
        self.execute_calls += 1
        if self.execute_delay:
            await asyncio.sleep(self.execute_delay)
        if self.raise_error is not None:
            raise self.raise_error
        if self.apply_writes:
            self._apply(command)
        return ProviderCommandResult(outcome=self.outcome, state=self._snapshot())

    async def subscribe(self) -> AsyncIterator[ProviderEvent]:
        while True:  # pragma: no cover - never consumed in these tests
            await asyncio.sleep(3600)
            yield ProviderEvent(ref=fake_ref(), state=self._snapshot())

    def _apply(self, command: ProviderCommand) -> None:
        params = command.params.model_dump()
        if "celsius" in params:
            self.state = self.state.model_copy(update={"target_temperature_c": params["celsius"]})
        elif "on" in params:
            self.state = self.state.model_copy(update={"heater": params["on"]})

    def _snapshot(self) -> ProviderState:
        return ProviderState(
            state=self.state,
            availability=self.availability,
            observed_at=datetime.now(UTC),
        )
