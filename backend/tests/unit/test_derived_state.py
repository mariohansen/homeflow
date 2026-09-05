"""State the gateway derives because no adapter reports it."""

from __future__ import annotations

from datetime import datetime

from conftest import run
from homeflow.clock import ManualClock
from homeflow.devices.models import Availability, DeviceState, StateSource
from homeflow.devices.registry import DeviceRegistry
from homeflow.devices.service import DeviceService
from homeflow.events.bus import EventBus
from homeflow.integrations.base.models import ProviderState
from simulators.fake_provider import FakeProvider, fake_ref


async def _pool(pump_running: bool) -> tuple[DeviceService, ManualClock]:
    """A pool device whose pump starts in a known position."""
    provider = FakeProvider()
    provider.state = provider.state.model_copy(update={"filter_pump": pump_running})
    clock = ManualClock()
    devices = DeviceService(
        registry=DeviceRegistry(id_salt="test-salt"),
        bus=EventBus(),
        clock=clock,
    )
    await devices.bootstrap([provider])
    return devices, clock


def _observe(devices: DeviceService, clock: ManualClock, **state: object) -> datetime | None:
    """Feed one provider observation in and read back the derived answer."""
    device = devices.ingest(
        fake_ref(),
        ProviderState(
            state=DeviceState(**state),  # type: ignore[arg-type]
            availability=Availability.ONLINE,
            observed_at=clock.now(),
        ),
        source=StateSource.PROVIDER_EVENT,
    )
    assert device is not None
    return device.state.filter_last_started_at


def test_the_pump_starting_is_timestamped() -> None:
    async def scenario() -> tuple[datetime | None, datetime]:
        devices, clock = await _pool(pump_running=False)
        started = clock.advance(3600)
        return _observe(devices, clock, filter_pump=True), started

    seen, started = run(scenario())
    assert seen == started


def test_a_running_pump_keeps_the_moment_it_started() -> None:
    """Later observations must not restamp a pump that never stopped."""

    async def scenario() -> tuple[datetime | None, datetime]:
        devices, clock = await _pool(pump_running=False)
        started = clock.advance(60)
        _observe(devices, clock, filter_pump=True)
        clock.advance(600)
        return _observe(devices, clock, filter_pump=True, current_temperature_c=26.0), started

    seen, started = run(scenario())
    assert seen == started


def test_stopping_the_pump_does_not_erase_the_answer() -> None:
    """ "Last filtered" is only useful once the filtering has finished."""

    async def scenario() -> tuple[datetime | None, datetime]:
        devices, clock = await _pool(pump_running=False)
        started = clock.advance(60)
        _observe(devices, clock, filter_pump=True)
        clock.advance(1800)
        return _observe(devices, clock, filter_pump=False), started

    seen, started = run(scenario())
    assert seen == started


def test_a_pump_already_running_at_startup_is_not_invented() -> None:
    """We did not see it start, so we do not claim to know when."""

    async def scenario() -> datetime | None:
        devices, clock = await _pool(pump_running=True)
        clock.advance(60)
        return _observe(devices, clock, filter_pump=True)

    assert run(scenario()) is None
