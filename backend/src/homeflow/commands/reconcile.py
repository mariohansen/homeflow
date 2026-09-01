"""Desired-state reconciliation after a command timeout.

A physical device can apply a command after the gateway stopped waiting, so a
timeout is never reported as a failure. The gateway reads state back once and
only then decides between SUCCEEDED and UNKNOWN (CLAUDE.md sections 28 and 29).
"""

from __future__ import annotations

from homeflow.commands.models import (
    Action,
    BrightnessParams,
    CommandParams,
    LockStateParams,
    OnOffParams,
    PlaybackCommand,
    PlaybackParams,
    TargetTemperatureParams,
    VolumeParams,
)
from homeflow.devices.models import DeviceState, PlaybackState

#: Tolerance for comparing a reported setpoint with the requested one.
_TEMPERATURE_EPSILON_C = 0.05


def desired_matches(action: Action, params: CommandParams, state: DeviceState) -> bool | None:
    """Return whether observed state satisfies the request.

    ``None`` means the outcome cannot be determined from state, which is the
    correct answer for momentary actions such as unlatching a door.
    """
    match action:
        case Action.SET_POWER if isinstance(params, OnOffParams):
            return _compare(state.power, params.on)
        case Action.SET_BRIGHTNESS if isinstance(params, BrightnessParams):
            return _compare(state.brightness, params.brightness)
        case Action.SET_VOLUME if isinstance(params, VolumeParams):
            return _compare(state.volume, params.volume)
        case Action.SET_PLAYBACK if isinstance(params, PlaybackParams):
            expected = (
                PlaybackState.PLAYING
                if params.playback is PlaybackCommand.PLAY
                else PlaybackState.PAUSED
            )
            return _compare(state.playback, expected)
        case Action.SET_TARGET_TEMPERATURE if isinstance(params, TargetTemperatureParams):
            if state.target_temperature_c is None:
                return None
            return abs(state.target_temperature_c - params.celsius) <= _TEMPERATURE_EPSILON_C
        case Action.SET_HEATER if isinstance(params, OnOffParams):
            return _compare(state.heater, params.on)
        case Action.SET_FILTER if isinstance(params, OnOffParams):
            return _compare(state.filter_pump, params.on)
        case Action.SET_BUBBLES if isinstance(params, OnOffParams):
            return _compare(state.bubbles, params.on)
        case Action.SET_CONTROL_PANEL_LOCK if isinstance(params, OnOffParams):
            return _compare(state.control_panel_lock, params.on)
        case Action.SET_LOCK_STATE if isinstance(params, LockStateParams):
            return _compare(state.lock_state, params.desired)
        case _:
            return None


def _compare(observed: object | None, expected: object) -> bool | None:
    if observed is None:
        return None
    return observed == expected
