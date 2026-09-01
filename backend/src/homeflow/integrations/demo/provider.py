"""Synthetic provider for Demo Mode (see docs/security/privacy-model.md).

Everything here is fictional. The module performs no I/O of any kind and imports
no other adapter, so a demo build cannot reach a real household device — which
is what makes public screenshots and README material safe to produce.

The simulation is deterministic: physics advance from an explicit elapsed time
and randomness comes from an injected, seeded generator, so tests and demo
recordings reproduce exactly.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from homeflow.capabilities import Capability, DeviceKind
from homeflow.clock import Clock, SystemClock
from homeflow.commands.models import (
    Action,
    BrightnessParams,
    LockStateParams,
    OnOffParams,
    PlaybackCommand,
    PlaybackParams,
    TargetTemperatureParams,
    VolumeParams,
)
from homeflow.devices.models import (
    Availability,
    DeviceConstraints,
    DeviceState,
    LockState,
    PlaybackState,
    ProgramState,
)
from homeflow.integrations.base.errors import ProviderUnavailableError
from homeflow.integrations.base.models import (
    CommandOutcome,
    ProviderCommand,
    ProviderCommandResult,
    ProviderDevice,
    ProviderDeviceRef,
    ProviderEvent,
    ProviderState,
)

PROVIDER_NAME = "demo"

#: Demo pool limits. Real Bestway bounds must come from a verified controller
#: before any write is enabled (see docs/adr/0006-bestway-direct-local-adapter.md).
POOL_MIN_C = 20.0
POOL_MAX_C = 40.0
POOL_STEP_C = 0.5

_AMBIENT_C = 22.0
_HEAT_RATE_C_PER_SIM_MINUTE = 0.035
_COOL_COEFFICIENT_PER_SIM_MINUTE = 0.004
_BUBBLE_COOLING_FACTOR = 2.0

#: One real second of demo time represents this many simulated seconds, so a
#: heating curve is visible in a screen recording without being dishonest about
#: the underlying model.
DEFAULT_TIME_SCALE = 60.0


@dataclass(slots=True)
class _DemoDevice:
    definition: ProviderDevice
    state: DeviceState
    availability: Availability = Availability.ONLINE

    @property
    def ref(self) -> ProviderDeviceRef:
        return self.definition.ref


def _ref(device_id: str) -> ProviderDeviceRef:
    return ProviderDeviceRef(provider=PROVIDER_NAME, provider_device_id=device_id)


def _build_devices() -> list[_DemoDevice]:
    return [
        _DemoDevice(
            definition=ProviderDevice(
                ref=_ref("living-room-ceiling-light"),
                suggested_name="Ceiling Light",
                kind=DeviceKind.LIGHT,
                capabilities=(Capability.POWER, Capability.BRIGHTNESS),
                room_hint="Living Room",
            ),
            state=DeviceState(power=True, brightness=60),
        ),
        _DemoDevice(
            definition=ProviderDevice(
                ref=_ref("living-room-speaker"),
                suggested_name="Speaker",
                kind=DeviceKind.MEDIA_PLAYER,
                capabilities=(Capability.MEDIA_PLAYBACK, Capability.VOLUME),
                room_hint="Living Room",
            ),
            state=DeviceState(playback=PlaybackState.PAUSED, volume=25),
        ),
        _DemoDevice(
            definition=ProviderDevice(
                ref=_ref("hallway-lock"),
                suggested_name="Demo Lock",
                kind=DeviceKind.LOCK,
                capabilities=(Capability.LOCK, Capability.UNLOCK),
                room_hint="Hallway",
            ),
            state=DeviceState(lock_state=LockState.LOCKED),
        ),
        _DemoDevice(
            definition=ProviderDevice(
                ref=_ref("terrace-pool"),
                suggested_name="Demo Pool",
                kind=DeviceKind.POOL,
                capabilities=(
                    Capability.CURRENT_TEMPERATURE,
                    Capability.TARGET_TEMPERATURE,
                    Capability.HEATING,
                    Capability.FILTER,
                    Capability.BUBBLES,
                    Capability.CONTROL_PANEL_LOCK,
                ),
                room_hint="Terrace",
                constraints=DeviceConstraints(
                    target_temperature_min_c=POOL_MIN_C,
                    target_temperature_max_c=POOL_MAX_C,
                    target_temperature_step_c=POOL_STEP_C,
                ),
            ),
            state=DeviceState(
                current_temperature_c=24.5,
                target_temperature_c=36.0,
                heater=False,
                filter_pump=True,
                bubbles=False,
                control_panel_lock=False,
            ),
        ),
        _DemoDevice(
            definition=ProviderDevice(
                ref=_ref("utility-washer"),
                suggested_name="Demo Washer",
                kind=DeviceKind.WASHING_MACHINE,
                capabilities=(Capability.PROGRAM_STATUS, Capability.REMAINING_TIME),
                room_hint="Utility Room",
            ),
            state=DeviceState(
                program=ProgramState.RUNNING,
                program_name="Cottons 40",
                remaining_seconds=45 * 60,
            ),
        ),
        # Starts unreachable so that offline handling is always visible in the
        # demo, in tests and in screenshots.
        _DemoDevice(
            definition=ProviderDevice(
                ref=_ref("utility-dishwasher"),
                suggested_name="Demo Dishwasher",
                kind=DeviceKind.DISHWASHER,
                capabilities=(Capability.PROGRAM_STATUS, Capability.REMAINING_TIME),
                room_hint="Utility Room",
            ),
            state=DeviceState(program=ProgramState.IDLE),
            availability=Availability.OFFLINE,
        ),
    ]


@dataclass(slots=True)
class DemoProvider:
    """A complete, self-contained fake household."""

    clock: Clock = field(default_factory=SystemClock)
    rng: random.Random = field(default_factory=lambda: random.Random(20260901))  # noqa: S311
    failure_rate: float = 0.0
    command_latency_seconds: float = 0.05
    tick_seconds: float = 2.0
    time_scale: float = DEFAULT_TIME_SCALE
    _devices: dict[str, _DemoDevice] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._devices = {device.ref.provider_device_id: device for device in _build_devices()}

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    async def discover_devices(self) -> Sequence[ProviderDevice]:
        return [device.definition for device in self._devices.values()]

    async def get_state(self, device_ref: ProviderDeviceRef) -> ProviderState:
        device = self._require(device_ref)
        return self._snapshot(device)

    async def execute(
        self,
        device_ref: ProviderDeviceRef,
        command: ProviderCommand,
    ) -> ProviderCommandResult:
        device = self._require(device_ref)
        if device.availability is Availability.OFFLINE:
            raise ProviderUnavailableError("demo device is offline")

        if self.command_latency_seconds > 0:
            await asyncio.sleep(self.command_latency_seconds)

        if self.failure_rate > 0 and self.rng.random() < self.failure_rate:
            raise ProviderUnavailableError("simulated demo failure")

        device.state = _apply(device.state, command)
        return ProviderCommandResult(
            outcome=CommandOutcome.APPLIED,
            state=self._snapshot(device),
        )

    async def subscribe(self) -> AsyncIterator[ProviderEvent]:
        """Advance the simulation and yield the resulting state changes."""
        while True:
            await asyncio.sleep(self.tick_seconds)
            for event in self.tick(self.tick_seconds):
                yield event

    def tick(self, elapsed_seconds: float) -> list[ProviderEvent]:
        """Advance simulated time and return events for devices that changed."""
        simulated = elapsed_seconds * self.time_scale
        events: list[ProviderEvent] = []
        for device in self._devices.values():
            if device.availability is Availability.OFFLINE:
                continue
            before = device.state
            device.state = _advance(device, simulated)
            if device.state != before:
                events.append(ProviderEvent(ref=device.ref, state=self._snapshot(device)))
        return events

    def _require(self, device_ref: ProviderDeviceRef) -> _DemoDevice:
        device = self._devices.get(device_ref.provider_device_id)
        if device is None or device_ref.provider != PROVIDER_NAME:
            raise ProviderUnavailableError("unknown demo device")
        return device

    def _snapshot(self, device: _DemoDevice) -> ProviderState:
        return ProviderState(
            state=device.state,
            availability=device.availability,
            observed_at=self.clock.now(),
        )


def _apply(state: DeviceState, command: ProviderCommand) -> DeviceState:
    """Return the state a compliant demo device would report after the command."""
    params = command.params
    match command.action:
        case Action.SET_POWER if isinstance(params, OnOffParams):
            return state.model_copy(update={"power": params.on})
        case Action.SET_BRIGHTNESS if isinstance(params, BrightnessParams):
            return state.model_copy(update={"brightness": params.brightness, "power": True})
        case Action.SET_VOLUME if isinstance(params, VolumeParams):
            return state.model_copy(update={"volume": params.volume})
        case Action.SET_PLAYBACK if isinstance(params, PlaybackParams):
            playing = params.playback is PlaybackCommand.PLAY
            return state.model_copy(
                update={"playback": PlaybackState.PLAYING if playing else PlaybackState.PAUSED}
            )
        case Action.SET_TARGET_TEMPERATURE if isinstance(params, TargetTemperatureParams):
            return state.model_copy(update={"target_temperature_c": params.celsius})
        case Action.SET_HEATER if isinstance(params, OnOffParams):
            # A real AirJet runs the filter pump whenever it heats.
            update = {"heater": params.on}
            if params.on:
                update["filter_pump"] = True
            return state.model_copy(update=update)
        case Action.SET_FILTER if isinstance(params, OnOffParams):
            # Switching the pump off also stops heating; the hardware interlock
            # is mirrored rather than bypassed (see docs/adr/0006-bestway-direct-local-adapter.md).
            update = {"filter_pump": params.on}
            if not params.on:
                update["heater"] = False
            return state.model_copy(update=update)
        case Action.SET_BUBBLES if isinstance(params, OnOffParams):
            return state.model_copy(update={"bubbles": params.on})
        case Action.SET_CONTROL_PANEL_LOCK if isinstance(params, OnOffParams):
            return state.model_copy(update={"control_panel_lock": params.on})
        case Action.SET_LOCK_STATE if isinstance(params, LockStateParams):
            return state.model_copy(update={"lock_state": params.desired})
        case _:
            return state


def _advance(device: _DemoDevice, simulated_seconds: float) -> DeviceState:
    kind = device.definition.kind
    if kind is DeviceKind.POOL:
        return _advance_pool(device.state, simulated_seconds)
    if kind in (DeviceKind.WASHING_MACHINE, DeviceKind.DISHWASHER):
        return _advance_appliance(device.state, simulated_seconds)
    return device.state


def _advance_pool(state: DeviceState, simulated_seconds: float) -> DeviceState:
    current = state.current_temperature_c
    target = state.target_temperature_c
    if current is None:
        return state

    minutes = simulated_seconds / 60.0
    if state.heater and target is not None:
        if current >= target:
            # The controller holds the setpoint instead of drifting.
            return state
        current = min(target, current + _HEAT_RATE_C_PER_SIM_MINUTE * minutes)
    else:
        loss = _COOL_COEFFICIENT_PER_SIM_MINUTE * (_BUBBLE_COOLING_FACTOR if state.bubbles else 1.0)
        current = current - loss * (current - _AMBIENT_C) * minutes
        current = max(_AMBIENT_C, current)

    rounded = round(current, 2)
    if rounded == state.current_temperature_c:
        return state
    return state.model_copy(update={"current_temperature_c": rounded})


def _advance_appliance(state: DeviceState, simulated_seconds: float) -> DeviceState:
    if state.program is not ProgramState.RUNNING or state.remaining_seconds is None:
        return state
    remaining = max(0, state.remaining_seconds - int(simulated_seconds))
    if remaining == state.remaining_seconds:
        return state
    if remaining == 0:
        return state.model_copy(update={"remaining_seconds": 0, "program": ProgramState.FINISHED})
    return state.model_copy(update={"remaining_seconds": remaining})
