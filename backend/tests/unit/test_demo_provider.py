"""The demo household must be deterministic, self-contained and honest."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from conftest import run
from homeflow.capabilities import Capability, DeviceKind
from homeflow.clock import ManualClock
from homeflow.commands.models import Action, OnOffParams, TargetTemperatureParams
from homeflow.devices.models import Availability, ProgramState
from homeflow.integrations.base.errors import ProviderUnavailableError
from homeflow.integrations.base.models import ProviderCommand, ProviderDeviceRef
from homeflow.integrations.demo import provider as demo_module
from homeflow.integrations.demo.provider import PROVIDER_NAME, DemoProvider

FORBIDDEN_IMPORTS = {
    "socket",
    "ssl",
    "http",
    "httpx",
    "requests",
    "aiohttp",
    "urllib",
    "subprocess",
    "os",
}


def _provider(**kwargs: object) -> DemoProvider:
    return DemoProvider(clock=ManualClock(), command_latency_seconds=0.0, **kwargs)  # type: ignore[arg-type]


def _ref(device_id: str) -> ProviderDeviceRef:
    return ProviderDeviceRef(provider=PROVIDER_NAME, provider_device_id=device_id)


def test_demo_provider_cannot_reach_the_network() -> None:
    """A demo build must be structurally incapable of touching a real device."""
    source = Path(inspect.getfile(demo_module)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)

    assert not ({module.split(".")[0] for module in modules} & FORBIDDEN_IMPORTS)

    # The shared adapter contract is allowed; another vendor adapter is not.
    other_adapters = {
        module
        for module in modules
        if module.startswith("homeflow.integrations.")
        and not module.startswith(("homeflow.integrations.base", "homeflow.integrations.demo"))
    }
    assert not other_adapters


def test_demo_devices_cover_the_documented_rooms() -> None:
    devices = run(_provider().discover_devices())
    names = {device.suggested_name for device in devices}
    rooms = {device.room_hint for device in devices}
    assert {"Ceiling Light", "Speaker", "Demo Lock", "Demo Pool", "Demo Washer"} <= names
    assert {"Living Room", "Hallway", "Terrace", "Utility Room"} <= rooms


def test_pool_publishes_its_own_verified_limits() -> None:
    devices = run(_provider().discover_devices())
    pool = next(device for device in devices if device.kind is DeviceKind.POOL)
    assert pool.constraints.target_temperature_min_c == 20.0
    assert pool.constraints.target_temperature_max_c == 40.0
    assert Capability.HEATING in pool.capabilities


def test_one_device_is_offline_so_offline_handling_stays_visible() -> None:
    provider = _provider()
    state = run(provider.get_state(_ref("utility-dishwasher")))
    assert state.availability is Availability.OFFLINE


def test_offline_device_refuses_commands() -> None:
    provider = _provider()
    command = ProviderCommand(action=Action.SET_POWER, params=OnOffParams(on=True))
    with pytest.raises(ProviderUnavailableError):
        run(provider.execute(_ref("utility-dishwasher"), command))


def test_heater_warms_the_pool_towards_the_target() -> None:
    provider = _provider()
    pool = _ref("terrace-pool")
    run(
        provider.execute(
            pool, ProviderCommand(action=Action.SET_HEATER, params=OnOffParams(on=True))
        )
    )
    before = run(provider.get_state(pool)).state.current_temperature_c
    provider.tick(60.0)
    after = run(provider.get_state(pool)).state.current_temperature_c
    assert before is not None and after is not None
    assert after > before


def test_heating_never_overshoots_the_target() -> None:
    provider = _provider()
    pool = _ref("terrace-pool")
    run(
        provider.execute(
            pool,
            ProviderCommand(
                action=Action.SET_TARGET_TEMPERATURE,
                params=TargetTemperatureParams(celsius=25.0),
            ),
        )
    )
    run(
        provider.execute(
            pool, ProviderCommand(action=Action.SET_HEATER, params=OnOffParams(on=True))
        )
    )
    for _ in range(200):
        provider.tick(60.0)
    current = run(provider.get_state(pool)).state.current_temperature_c
    assert current == pytest.approx(25.0, abs=0.01)


def test_simulation_is_reproducible() -> None:
    first, second = _provider(), _provider()
    for provider in (first, second):
        run(
            provider.execute(
                _ref("terrace-pool"),
                ProviderCommand(action=Action.SET_HEATER, params=OnOffParams(on=True)),
            )
        )
        for _ in range(10):
            provider.tick(30.0)
    left = run(first.get_state(_ref("terrace-pool"))).state
    right = run(second.get_state(_ref("terrace-pool"))).state
    assert left == right


def test_switching_the_filter_off_also_stops_the_heater() -> None:
    """The demo mirrors the hardware interlock instead of bypassing it."""
    provider = _provider()
    pool = _ref("terrace-pool")
    run(
        provider.execute(
            pool, ProviderCommand(action=Action.SET_HEATER, params=OnOffParams(on=True))
        )
    )
    run(
        provider.execute(
            pool, ProviderCommand(action=Action.SET_FILTER, params=OnOffParams(on=False))
        )
    )
    state = run(provider.get_state(pool)).state
    assert state.filter_pump is False
    assert state.heater is False


def test_appliance_counts_down_and_finishes() -> None:
    provider = _provider()
    washer = _ref("utility-washer")
    start = run(provider.get_state(washer)).state
    assert start.program is ProgramState.RUNNING
    provider.tick(60.0)
    mid = run(provider.get_state(washer)).state
    assert mid.remaining_seconds is not None
    assert start.remaining_seconds is not None
    assert mid.remaining_seconds < start.remaining_seconds

    for _ in range(200):
        provider.tick(60.0)
    finished = run(provider.get_state(washer)).state
    assert finished.program is ProgramState.FINISHED
    assert finished.remaining_seconds == 0


def test_simulated_failures_are_opt_in() -> None:
    provider = _provider(failure_rate=1.0)
    command = ProviderCommand(action=Action.SET_HEATER, params=OnOffParams(on=True))
    with pytest.raises(ProviderUnavailableError):
        run(provider.execute(_ref("terrace-pool"), command))
