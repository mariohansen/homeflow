"""Action policy: required capability, parameter schema and risk class.

Risk classification is data, not scattered conditionals, so CLAUDE.md section 14
can be verified by reading one table and one function.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from homeflow.capabilities import Capability
from homeflow.commands.models import (
    Action,
    BrightnessParams,
    CommandParams,
    LockStateParams,
    NoParams,
    OnOffParams,
    PlaybackParams,
    RiskClass,
    TargetTemperatureParams,
    VolumeParams,
)
from homeflow.devices.models import LockState


@dataclass(frozen=True, slots=True)
class ActionSpec:
    required_capability: Capability
    params_model: type[CommandParams]
    base_risk: RiskClass


ACTION_SPECS: Mapping[Action, ActionSpec] = {
    Action.SET_POWER: ActionSpec(Capability.POWER, OnOffParams, RiskClass.LOW),
    Action.SET_BRIGHTNESS: ActionSpec(Capability.BRIGHTNESS, BrightnessParams, RiskClass.LOW),
    Action.SET_VOLUME: ActionSpec(Capability.VOLUME, VolumeParams, RiskClass.LOW),
    Action.SET_PLAYBACK: ActionSpec(Capability.MEDIA_PLAYBACK, PlaybackParams, RiskClass.LOW),
    Action.SET_TARGET_TEMPERATURE: ActionSpec(
        Capability.TARGET_TEMPERATURE, TargetTemperatureParams, RiskClass.MEDIUM
    ),
    Action.SET_HEATER: ActionSpec(Capability.HEATING, OnOffParams, RiskClass.MEDIUM),
    Action.SET_FILTER: ActionSpec(Capability.FILTER, OnOffParams, RiskClass.MEDIUM),
    Action.SET_BUBBLES: ActionSpec(Capability.BUBBLES, OnOffParams, RiskClass.MEDIUM),
    Action.SET_CONTROL_PANEL_LOCK: ActionSpec(
        Capability.CONTROL_PANEL_LOCK, OnOffParams, RiskClass.MEDIUM
    ),
    Action.SET_LOCK_STATE: ActionSpec(Capability.LOCK, LockStateParams, RiskClass.MEDIUM),
    Action.UNLATCH: ActionSpec(Capability.UNLATCH, NoParams, RiskClass.HIGH),
}


def classify(action: Action, params: CommandParams) -> RiskClass:
    """Return the effective risk class for a validated command.

    Locking a door is reversible from outside and stays MEDIUM; unlocking grants
    physical access and is HIGH (CLAUDE.md section 15).
    """
    spec = ACTION_SPECS[action]
    if action is Action.SET_LOCK_STATE:
        assert isinstance(params, LockStateParams)  # noqa: S101 - narrowing, spec-enforced
        return RiskClass.HIGH if params.desired is LockState.UNLOCKED else RiskClass.MEDIUM
    return spec.base_risk


def required_capability_for(action: Action, params: CommandParams) -> Capability:
    """Unlocking additionally requires the UNLOCK capability, not just LOCK."""
    if action is Action.SET_LOCK_STATE:
        assert isinstance(params, LockStateParams)  # noqa: S101 - narrowing, spec-enforced
        if params.desired is LockState.UNLOCKED:
            return Capability.UNLOCK
    return ACTION_SPECS[action].required_capability
