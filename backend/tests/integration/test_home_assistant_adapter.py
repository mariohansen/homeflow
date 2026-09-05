"""The Home Assistant adapter, against a stand-in that speaks both interfaces.

CI never reaches the household, so everything here runs against
``simulators.home_assistant``: REST through an httpx transport, and a real
WebSocket server on loopback so the authentication handshake is exercised
rather than assumed.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import httpx
import pytest
from pydantic import SecretStr

from conftest import run
from homeflow.capabilities import Capability, DeviceKind
from homeflow.clock import ManualClock
from homeflow.commands.models import (
    Action,
    BrightnessParams,
    LockStateParams,
    OnOffParams,
    TargetTemperatureParams,
)
from homeflow.devices.models import Availability, LockState
from homeflow.integrations.base.errors import ProviderRejectedError, ProviderUnavailableError
from homeflow.integrations.base.models import CommandOutcome, ProviderCommand
from homeflow.integrations.home_assistant.client import HomeAssistantClient
from homeflow.integrations.home_assistant.provider import HomeAssistantProvider, ref_for
from simulators.home_assistant import TOKEN, FakeHomeAssistant


@contextlib.asynccontextmanager
async def adapter(
    home: FakeHomeAssistant,
    *,
    released: frozenset[str] = frozenset(),
    token: str = TOKEN,
    events: list[dict] | None = None,
) -> AsyncIterator[HomeAssistantProvider]:
    async with home.running(events) as base_url:
        http = httpx.AsyncClient(transport=home.transport())
        provider = HomeAssistantProvider(
            client=HomeAssistantClient(base_url=base_url, token=SecretStr(token), http=http),
            released_domains=released,
            clock=ManualClock(),
            # The waiting is what the confirm loop is for; the test does not
            # need to sit through it.
            confirm_attempts=2,
            confirm_delay_seconds=0.01,
        )
        try:
            yield provider
        finally:
            await http.aclose()


def named(devices, name: str):
    return next(device for device in devices if device.ref.provider_device_id == name)


# -- discovery -------------------------------------------------------------


def test_only_understood_entities_become_devices() -> None:
    async def scenario() -> set[str]:
        async with adapter(FakeHomeAssistant()) as provider:
            devices = await provider.discover_devices()
            return {device.ref.provider_device_id for device in devices}

    found = run(scenario())
    assert "light.living_room_ceiling" in found
    assert "climate.living_room" in found
    assert "sensor.terrace_temperature" in found
    # The noise a household instance is full of stays out of the app.
    assert "sensor.speaker_battery" not in found
    assert "update.home_assistant_core" not in found
    assert "automation.wake_up" not in found


def test_devices_land_in_the_room_home_assistant_says_they_are_in() -> None:
    async def scenario() -> dict[str, str | None]:
        async with adapter(FakeHomeAssistant()) as provider:
            devices = await provider.discover_devices()
            return {device.ref.provider_device_id: device.room_hint for device in devices}

    rooms = run(scenario())
    assert rooms["light.living_room_ceiling"] == "Living Room"
    assert rooms["light.hallway_spot"] == "Hallway"
    # This one has no area of its own and inherits it from its device.
    assert rooms["media_player.living_room_speaker"] == "Living Room"
    # Nothing was assigned for this one, and nothing is invented.
    assert rooms["sensor.terrace_temperature"] is None


def test_devices_still_arrive_when_the_registries_cannot_be_read() -> None:
    """A narrower token costs rooms, not the whole household."""

    async def scenario() -> tuple[int, set[str | None]]:
        async with adapter(FakeHomeAssistant(registries=False)) as provider:
            devices = await provider.discover_devices()
            return len(devices), {device.room_hint for device in devices}

    count, rooms = run(scenario())
    assert count == 7
    assert rooms == {None}


def test_a_thermostat_carries_its_own_limits() -> None:
    async def scenario():
        async with adapter(FakeHomeAssistant()) as provider:
            return named(await provider.discover_devices(), "climate.living_room").constraints

    constraints = run(scenario())
    assert constraints.target_temperature_min_c == 5.0
    assert constraints.target_temperature_max_c == 30.0
    assert constraints.target_temperature_step_c == 0.5


# -- the release gate ------------------------------------------------------


def test_nothing_is_writable_until_a_domain_is_released() -> None:
    """State is still shown: knowing a light is on is useful before switching it."""

    async def scenario():
        async with adapter(FakeHomeAssistant()) as provider:
            devices = await provider.discover_devices()
            light = named(devices, "light.living_room_ceiling")
            state = await provider.get_state(ref_for("light.living_room_ceiling"))
            return light.capabilities, light.kind, state.state.power

    capabilities, kind, power = run(scenario())
    assert capabilities == (), "an unreleased domain advertises no control"
    assert kind is DeviceKind.LIGHT
    assert power is True, "but the state is reported all the same"


def test_releasing_a_domain_reveals_its_controls() -> None:
    async def scenario():
        async with adapter(FakeHomeAssistant(), released=frozenset({"light"})) as provider:
            devices = await provider.discover_devices()
            return (
                set(named(devices, "light.living_room_ceiling").capabilities),
                set(named(devices, "switch.terrace_socket").capabilities),
            )

    light, switch = run(scenario())
    assert light == {Capability.POWER, Capability.BRIGHTNESS}
    assert switch == set(), "releasing one domain releases only that domain"


def test_a_door_stays_read_only_however_it_is_configured() -> None:
    """Configuration cannot widen the allowlist; the adapter refuses regardless."""

    async def scenario():
        released = frozenset({"lock", "light"})
        async with adapter(FakeHomeAssistant(), released=released) as provider:
            devices = await provider.discover_devices()
            door = named(devices, "lock.front_door")
            state = await provider.get_state(ref_for("lock.front_door"))
            command = ProviderCommand(
                action=Action.SET_LOCK_STATE,
                params=LockStateParams(desired=LockState.UNLOCKED),
            )
            with pytest.raises(ProviderRejectedError):
                await provider.execute(ref_for("lock.front_door"), command)
            return door.capabilities, state.state.lock_state

    capabilities, lock_state = run(scenario())
    assert capabilities == ()
    assert lock_state is LockState.LOCKED


def test_an_unreleased_domain_is_refused_even_if_asked_directly() -> None:
    async def scenario() -> None:
        async with adapter(FakeHomeAssistant()) as provider:
            await provider.discover_devices()
            command = ProviderCommand(action=Action.SET_POWER, params=OnOffParams(on=True))
            with pytest.raises(ProviderRejectedError):
                await provider.execute(ref_for("light.hallway_spot"), command)

    run(scenario())


def test_an_unknown_reference_is_refused() -> None:
    async def scenario() -> None:
        async with adapter(FakeHomeAssistant()) as provider:
            await provider.discover_devices()
            with pytest.raises(ProviderUnavailableError):
                await provider.get_state(ref_for("light.does_not_exist"))

    run(scenario())


# -- writing ---------------------------------------------------------------


def test_switching_a_light_on_is_confirmed_by_reading_it_back() -> None:
    async def scenario():
        home = FakeHomeAssistant()
        async with adapter(home, released=frozenset({"light"})) as provider:
            await provider.discover_devices()
            command = ProviderCommand(action=Action.SET_POWER, params=OnOffParams(on=True))
            result = await provider.execute(ref_for("light.hallway_spot"), command)
            return result.outcome, home.entities["light.hallway_spot"]["state"], home.calls

    outcome, state, calls = run(scenario())
    assert outcome is CommandOutcome.APPLIED
    assert state == "on"
    assert calls[0][:2] == ("light", "turn_on")


def test_brightness_travels_as_a_percentage_and_comes_back_as_one() -> None:
    async def scenario():
        home = FakeHomeAssistant()
        async with adapter(home, released=frozenset({"light"})) as provider:
            await provider.discover_devices()
            command = ProviderCommand(
                action=Action.SET_BRIGHTNESS, params=BrightnessParams(brightness=40)
            )
            result = await provider.execute(ref_for("light.living_room_ceiling"), command)
            return result.outcome, result.state, home.calls[0][2]

    outcome, state, body = run(scenario())
    assert outcome is CommandOutcome.APPLIED
    assert body["brightness_pct"] == 40
    assert state is not None
    assert state.state.brightness == 40


def test_dimming_to_zero_turns_the_light_off() -> None:
    """Home Assistant refuses a zero-percent brightness on some platforms."""

    async def scenario():
        home = FakeHomeAssistant()
        async with adapter(home, released=frozenset({"light"})) as provider:
            await provider.discover_devices()
            command = ProviderCommand(
                action=Action.SET_BRIGHTNESS, params=BrightnessParams(brightness=0)
            )
            result = await provider.execute(ref_for("light.living_room_ceiling"), command)
            return result.outcome, home.calls[0][1], home.entities["light.living_room_ceiling"]

    outcome, service, entity = run(scenario())
    assert outcome is CommandOutcome.APPLIED
    assert service == "turn_off"
    assert entity["state"] == "off"


def test_a_setpoint_reaches_the_thermostat() -> None:
    async def scenario():
        home = FakeHomeAssistant()
        async with adapter(home, released=frozenset({"climate"})) as provider:
            await provider.discover_devices()
            command = ProviderCommand(
                action=Action.SET_TARGET_TEMPERATURE,
                params=TargetTemperatureParams(celsius=22.5),
            )
            result = await provider.execute(ref_for("climate.living_room"), command)
            return result.outcome, home.entities["climate.living_room"]["attributes"]["temperature"]

    outcome, temperature = run(scenario())
    assert outcome is CommandOutcome.APPLIED
    assert temperature == 22.5


def test_a_call_that_changes_nothing_is_unknown_not_success() -> None:
    """An integration that quietly does not act must not look like one that did."""

    async def scenario():
        home = FakeHomeAssistant()
        home.apply_calls = False
        async with adapter(home, released=frozenset({"light"})) as provider:
            await provider.discover_devices()
            command = ProviderCommand(action=Action.SET_POWER, params=OnOffParams(on=True))
            result = await provider.execute(ref_for("light.hallway_spot"), command)
            return result.outcome, result.failure_code

    outcome, failure = run(scenario())
    assert outcome is CommandOutcome.UNKNOWN
    assert failure == "not_confirmed_by_device"


def test_a_refused_service_call_is_a_rejection() -> None:
    async def scenario() -> None:
        home = FakeHomeAssistant()
        home.reject_service = True
        async with adapter(home, released=frozenset({"light"})) as provider:
            await provider.discover_devices()
            command = ProviderCommand(action=Action.SET_POWER, params=OnOffParams(on=True))
            with pytest.raises(ProviderRejectedError):
                await provider.execute(ref_for("light.hallway_spot"), command)

    run(scenario())


# -- credentials and live state --------------------------------------------


def test_a_wrong_credential_fails_closed() -> None:
    async def scenario() -> None:
        async with adapter(FakeHomeAssistant(), token="not-the-token") as provider:
            with pytest.raises(ProviderUnavailableError):
                await provider.discover_devices()

    run(scenario())


def test_the_socket_refuses_a_wrong_credential_too() -> None:
    """The registries go through the WebSocket, which authenticates separately."""

    async def scenario() -> dict[str, str]:
        home = FakeHomeAssistant()
        async with home.running() as base_url:
            http = httpx.AsyncClient(transport=home.transport())
            client = HomeAssistantClient(base_url=base_url, token=SecretStr("wrong"), http=http)
            try:
                # Best effort by design: a refused socket costs rooms, not a crash.
                return await client.rooms()
            finally:
                await http.aclose()

    assert run(scenario()) == {}


def test_live_changes_arrive_as_canonical_events() -> None:
    async def scenario():
        changed = [
            {
                "entity_id": "light.hallway_spot",
                "state": "on",
                "attributes": {"friendly_name": "Hallway Spot", "supported_color_modes": ["onoff"]},
            }
        ]
        async with adapter(FakeHomeAssistant(), events=changed) as provider:
            await provider.discover_devices()
            seen = []
            async for event in provider.subscribe():
                seen.append(event)
                # The stream opens with a snapshot of everything, then the change.
                if event.ref.provider_device_id == "light.hallway_spot" and event.state.state.power:
                    break
            return seen

    events = run(scenario())
    assert len(events) > 1, "the stream re-synchronises before it follows along"
    assert events[-1].ref.provider_device_id == "light.hallway_spot"
    assert events[-1].state.state.power is True
    assert events[-1].state.availability is Availability.ONLINE


def test_an_entity_that_is_not_a_device_is_not_streamed() -> None:
    async def scenario() -> list[str]:
        changed = [
            {"entity_id": "sensor.speaker_battery", "state": "87", "attributes": {}},
            {
                "entity_id": "switch.terrace_socket",
                "state": "on",
                "attributes": {"friendly_name": "Terrace Socket"},
            },
        ]
        async with adapter(FakeHomeAssistant(), events=changed) as provider:
            await provider.discover_devices()
            seen: list[str] = []
            async for event in provider.subscribe():
                seen.append(event.ref.provider_device_id)
                if event.ref.provider_device_id == "switch.terrace_socket" and len(seen) > 7:
                    break
            return seen

    assert "sensor.speaker_battery" not in run(scenario())
