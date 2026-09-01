"""The Bestway adapter end to end against a synthetic controller.

No hardware is involved. What these tests establish is that the read path
decodes what the controller reported, and that the write path cannot fire while
the datapoint layout is unproven or the capability unreleased.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import pytest

from conftest import run
from homeflow.capabilities import Capability
from homeflow.commands.models import Action, OnOffParams, TargetTemperatureParams
from homeflow.devices.models import DeviceState
from homeflow.integrations.base.errors import ProviderRejectedError, ProviderUnavailableError
from homeflow.integrations.base.models import (
    CommandOutcome,
    ProviderCommand,
    ProviderDeviceRef,
)
from homeflow.integrations.bestway.client import BestwayClient
from homeflow.integrations.bestway.datapoints import (
    CANDIDATE_PROFILE,
    Datapoint,
    DatapointProfile,
)
from homeflow.integrations.bestway.provider import BestwayProvider, airjet_ref
from simulators.bestway_simulator import BestwaySimulator, initial_payload


def profile(*writable: Datapoint, trusted: bool = True) -> DatapointProfile:
    return DatapointProfile.model_validate(
        CANDIDATE_PROFILE.model_dump() | {"trusted": trusted, "writable": frozenset(writable)}
    )


@contextlib.asynccontextmanager
async def controller(**kwargs: object) -> AsyncIterator[BestwaySimulator]:
    simulator = BestwaySimulator(**kwargs)  # type: ignore[arg-type]
    await simulator.start()
    try:
        yield simulator
    finally:
        await simulator.stop()


@contextlib.asynccontextmanager
async def connected(
    simulator: BestwaySimulator,
    layout: DatapointProfile,
) -> AsyncIterator[BestwayProvider]:
    """An adapter that always hangs up, so no test leaks a connection."""
    provider = BestwayProvider(
        client=BestwayClient("127.0.0.1", simulator.port, connect_timeout=2.0, request_timeout=2.0),
        profile=layout,
        poll_seconds=0.05,
    )
    try:
        yield provider
    finally:
        await provider.aclose()


def heater_command(*, on: bool) -> ProviderCommand:
    return ProviderCommand(action=Action.SET_HEATER, params=OnOffParams(on=on))


# -- discovery ------------------------------------------------------------


def test_an_unproven_layout_exposes_no_device() -> None:
    """A wrong temperature must never reach a screen."""

    async def scenario() -> list[object]:
        async with controller() as simulator, connected(simulator, profile(trusted=False)) as pool:
            return list(await pool.discover_devices())

    assert run(scenario()) == []


def test_a_proven_layout_exposes_a_read_only_pool() -> None:
    async def scenario() -> tuple[tuple[Capability, ...], float | None]:
        async with controller() as simulator, connected(simulator, profile()) as pool:
            device = (await pool.discover_devices())[0]
            return device.capabilities, device.constraints.target_temperature_max_c

    capabilities, maximum = run(scenario())
    assert capabilities == (Capability.CURRENT_TEMPERATURE,)
    # No control capability is advertised, so no client can render one.
    assert Capability.HEATING not in capabilities
    assert maximum == 40.0


def test_releasing_a_datapoint_advertises_exactly_that_capability() -> None:
    async def scenario() -> tuple[Capability, ...]:
        released = profile(Datapoint.HEATER, Datapoint.FILTER_PUMP)
        async with controller() as simulator, connected(simulator, released) as pool:
            return (await pool.discover_devices())[0].capabilities

    capabilities = run(scenario())
    assert set(capabilities) == {
        Capability.CURRENT_TEMPERATURE,
        Capability.HEATING,
        Capability.FILTER,
    }
    assert Capability.BUBBLES not in capabilities


# -- reading --------------------------------------------------------------


def test_state_reflects_what_the_controller_reported() -> None:
    async def scenario() -> DeviceState:
        payload = bytearray(initial_payload(current_c=29, target_c=37, heater=True, bubbles=True))
        async with (
            controller(payload=payload) as simulator,
            connected(simulator, profile()) as pool,
        ):
            return (await pool.get_state(airjet_ref())).state

    state = run(scenario())
    assert state.current_temperature_c == 29.0
    assert state.target_temperature_c == 37.0
    assert state.heater is True
    assert state.bubbles is True
    assert state.control_panel_lock is False


def test_a_controller_reporting_fahrenheit_is_normalised() -> None:
    async def scenario() -> float | None:
        payload = bytearray(initial_payload(current_c=100, target_c=104, fahrenheit=True))
        async with (
            controller(payload=payload) as simulator,
            connected(simulator, profile()) as pool,
        ):
            return (await pool.get_state(airjet_ref())).state.current_temperature_c

    assert run(scenario()) == pytest.approx(37.8, abs=0.05)


def test_an_unreachable_controller_is_reported_as_unavailable() -> None:
    async def scenario() -> None:
        async with controller() as simulator, connected(simulator, profile()) as pool:
            await simulator.stop()
            with pytest.raises(ProviderUnavailableError):
                await pool.get_state(airjet_ref())

    run(scenario())


def test_a_malformed_frame_ends_the_conversation() -> None:
    async def scenario() -> None:
        async with (
            controller(corrupt_next_response=True) as simulator,
            connected(simulator, profile()) as pool,
        ):
            with pytest.raises(ProviderUnavailableError):
                await pool.get_state(airjet_ref())

    run(scenario())


def test_a_status_block_that_does_not_fit_the_layout_is_refused() -> None:
    async def scenario() -> None:
        async with (
            controller(payload=bytearray(b"\x01\x02\x03")) as simulator,
            connected(simulator, profile()) as pool,
        ):
            with pytest.raises(ProviderUnavailableError, match="unexpected status block"):
                await pool.get_state(airjet_ref())

    run(scenario())


def test_polling_yields_a_snapshot_when_the_controller_changes() -> None:
    async def scenario() -> float | None:
        async with controller() as simulator, connected(simulator, profile()) as pool:
            stream = pool.subscribe()
            simulator.payload = bytearray(initial_payload(current_c=31))
            event = await anext(stream)
            await stream.aclose()
            return event.state.state.current_temperature_c

    assert run(scenario()) == 31.0


def test_an_unknown_device_reference_is_refused() -> None:
    async def scenario() -> None:
        async with controller() as simulator, connected(simulator, profile()) as pool:
            elsewhere = ProviderDeviceRef(provider="bestway", provider_device_id="elsewhere")
            with pytest.raises(ProviderUnavailableError):
                await pool.get_state(elsewhere)

    run(scenario())


# -- writing --------------------------------------------------------------


def test_no_write_leaves_the_gateway_while_the_layout_is_unproven() -> None:
    async def scenario() -> int:
        unproven = profile(Datapoint.HEATER, trusted=False)
        async with controller() as simulator, connected(simulator, unproven) as pool:
            with pytest.raises(ProviderRejectedError, match="not verified"):
                await pool.execute(airjet_ref(), heater_command(on=True))
            return simulator.write_count

    assert run(scenario()) == 0


def test_no_write_leaves_the_gateway_for_an_unreleased_capability() -> None:
    async def scenario() -> int:
        async with (
            controller() as simulator,
            connected(simulator, profile(Datapoint.HEATER)) as pool,
        ):
            command = ProviderCommand(action=Action.SET_BUBBLES, params=OnOffParams(on=True))
            with pytest.raises(ProviderRejectedError, match="not been released"):
                await pool.execute(airjet_ref(), command)
            return simulator.write_count

    assert run(scenario()) == 0


def test_a_released_capability_writes_and_is_confirmed() -> None:
    async def scenario() -> tuple[CommandOutcome, bool | None, int]:
        async with (
            controller() as simulator,
            connected(simulator, profile(Datapoint.HEATER)) as pool,
        ):
            result = await pool.execute(airjet_ref(), heater_command(on=True))
            state = result.state.state if result.state else None
            return result.outcome, state.heater if state else None, simulator.write_count

    outcome, heater, writes = run(scenario())
    assert outcome is CommandOutcome.APPLIED
    assert heater is True
    assert writes == 1


def test_a_write_the_controller_ignores_settles_as_unknown() -> None:
    """Read-after-write is the point: silence is not success."""

    async def scenario() -> tuple[CommandOutcome, str | None]:
        async with (
            controller(honour_writes=False) as simulator,
            connected(simulator, profile(Datapoint.HEATER)) as pool,
        ):
            result = await pool.execute(airjet_ref(), heater_command(on=True))
            return result.outcome, result.failure_code

    outcome, failure = run(scenario())
    assert outcome is CommandOutcome.UNKNOWN
    assert failure == "not_confirmed_by_device"


def test_a_setpoint_outside_the_verified_range_never_reaches_the_device() -> None:
    async def scenario() -> int:
        async with (
            controller() as simulator,
            connected(simulator, profile(Datapoint.TARGET_TEMPERATURE)) as pool,
        ):
            command = ProviderCommand(
                action=Action.SET_TARGET_TEMPERATURE,
                params=TargetTemperatureParams(celsius=55.0),
            )
            with pytest.raises(ProviderRejectedError, match="verified device range"):
                await pool.execute(airjet_ref(), command)
            return simulator.write_count

    assert run(scenario()) == 0


def test_a_setpoint_is_written_in_the_unit_the_controller_uses() -> None:
    async def scenario() -> int:
        payload = bytearray(initial_payload(current_c=100, target_c=100, fahrenheit=True))
        async with (
            controller(payload=payload) as simulator,
            connected(simulator, profile(Datapoint.TARGET_TEMPERATURE)) as pool,
        ):
            command = ProviderCommand(
                action=Action.SET_TARGET_TEMPERATURE,
                params=TargetTemperatureParams(celsius=38.0),
            )
            await pool.execute(airjet_ref(), command)
            return simulator.payload[5]

    # 38 C is a little over 100 F, and the controller is addressed in its own unit.
    assert run(scenario()) == 100


def test_a_write_changes_nothing_else_on_the_controller() -> None:
    async def scenario() -> tuple[bytes, bytes]:
        async with (
            controller() as simulator,
            connected(simulator, profile(Datapoint.BUBBLES)) as pool,
        ):
            before = bytes(simulator.payload)
            command = ProviderCommand(action=Action.SET_BUBBLES, params=OnOffParams(on=True))
            await pool.execute(airjet_ref(), command)
            return before, bytes(simulator.payload)

    before, after = run(scenario())
    differing = [index for index, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    assert differing == [4]


def test_a_controller_that_hangs_up_after_every_read_still_works() -> None:
    """Controllers in the field close the connection after an exchange."""

    async def scenario() -> list[float | None]:
        async with (
            controller(close_after_response=True) as simulator,
            connected(simulator, profile()) as pool,
        ):
            readings = []
            for _ in range(3):
                readings.append((await pool.get_state(airjet_ref())).state.current_temperature_c)
            return readings

    assert run(scenario()) == [24.0, 24.0, 24.0]
