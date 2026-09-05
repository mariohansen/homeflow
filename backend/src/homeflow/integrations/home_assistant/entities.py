"""Translating Home Assistant entities into canonical devices.

This module is pure: it takes a payload and returns domain objects, and does no
I/O at all. That is deliberate, because it is where every decision that matters
lives -- which entities become devices, which capabilities they may claim, and
what their state means -- and those decisions should be readable and testable
without a Home Assistant anywhere near them.

Three rules shape it.

**Entity ids never leave.** ``light.wohnzimmer_stehlampe`` says which vendor is
installed and which rooms exist. It stays in the provider reference, which the
registry turns into a keyed HMAC identifier; nothing above the adapter sees it
(see docs/security/privacy-model.md).

**An entity claims a capability only when it says it has it.** Home Assistant
advertises what each entity supports, and that is what decides whether HomeFlow
offers a control. A dimmer that cannot dim must not grow a brightness slider.

**Only entities we understand become devices.** A household instance holds
hundreds of entities -- diagnostics, update sensors, battery levels. Importing
them all would bury the six things somebody actually wants to press.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from homeflow.capabilities import Capability, DeviceKind
from homeflow.devices.models import (
    Availability,
    DeviceConstraints,
    DeviceState,
    LockState,
    PlaybackState,
)

#: Home Assistant's documented media player feature flags. Only the ones
#: HomeFlow can act on are listed; the rest are ignored rather than guessed at.
_FEATURE_PAUSE = 1
_FEATURE_VOLUME_SET = 4
_FEATURE_PREVIOUS_TRACK = 16
_FEATURE_NEXT_TRACK = 32
_FEATURE_PLAY = 16384

#: Brightness travels 0-255 on the wire and 0-100 in the canonical model.
_HA_BRIGHTNESS_MAX = 255

_UNAVAILABLE = "unavailable"
_UNKNOWN = "unknown"

#: What Home Assistant reports while a lock is mid-movement. Neither state is a
#: fact about the door yet, so both read as unknown rather than as a guess.
_LOCK_STATES: dict[str, LockState] = {
    "locked": LockState.LOCKED,
    "unlocked": LockState.UNLOCKED,
    "open": LockState.UNLOCKED,
    "opening": LockState.UNKNOWN,
    "locking": LockState.UNKNOWN,
    "unlocking": LockState.UNKNOWN,
    "jammed": LockState.UNKNOWN,
}

_PLAYBACK_STATES: dict[str, PlaybackState] = {
    "playing": PlaybackState.PLAYING,
    "paused": PlaybackState.PAUSED,
    "idle": PlaybackState.STOPPED,
    "standby": PlaybackState.STOPPED,
    "off": PlaybackState.STOPPED,
    "buffering": PlaybackState.PLAYING,
}


class HaEntity(BaseModel):
    """One entity as Home Assistant reports it.

    Validated rather than trusted: this crosses a network boundary, and an
    integration can put anything in ``attributes``.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    entity_id: str = Field(min_length=3, max_length=255, pattern=r"^[a-z_]+\.[a-z0-9_]+$")
    state: str = Field(max_length=255)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    def number(self, key: str) -> float | None:
        """An attribute only if it really is a finite number."""
        value = self.attributes.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return float(value) if value == value and abs(value) != float("inf") else None

    def features(self) -> int:
        value = self.attributes.get("supported_features")
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    def text(self, key: str, limit: int = 120) -> str | None:
        value = self.attributes.get(key)
        return value[:limit] if isinstance(value, str) and value else None


@dataclass(frozen=True, slots=True)
class Mapping:
    """What one entity becomes, before the operator's release is applied."""

    kind: DeviceKind
    #: Everything the entity can do, whether or not writing has been released.
    capabilities: tuple[Capability, ...]
    #: The subset that only appears once the operator releases this domain.
    writable: tuple[Capability, ...]
    constraints: DeviceConstraints = field(default_factory=DeviceConstraints)


def _light(entity: HaEntity) -> Mapping:
    modes = entity.attributes.get("supported_color_modes")
    dimmable = isinstance(modes, list) and any(
        isinstance(mode, str) and mode not in ("onoff", "unknown") for mode in modes
    )
    capabilities: list[Capability] = [Capability.POWER]
    if dimmable:
        capabilities.append(Capability.BRIGHTNESS)
    return Mapping(DeviceKind.LIGHT, tuple(capabilities), tuple(capabilities))


def _switch(entity: HaEntity) -> Mapping:
    return Mapping(DeviceKind.SWITCH, (Capability.POWER,), (Capability.POWER,))


def _media_player(entity: HaEntity) -> Mapping:
    features = entity.features()
    capabilities: list[Capability] = []
    if features & (_FEATURE_PLAY | _FEATURE_PAUSE):
        capabilities.append(Capability.MEDIA_PLAYBACK)
    if features & _FEATURE_NEXT_TRACK:
        capabilities.append(Capability.MEDIA_NEXT)
    if features & _FEATURE_PREVIOUS_TRACK:
        capabilities.append(Capability.MEDIA_PREVIOUS)
    if features & _FEATURE_VOLUME_SET:
        capabilities.append(Capability.VOLUME)
    return Mapping(DeviceKind.MEDIA_PLAYER, tuple(capabilities), tuple(capabilities))


def _lock(entity: HaEntity) -> Mapping:
    # State only, and no writable capability at all. Door control needs the
    # fresh device-owner authorisation that SECURITY.md describes and this
    # deployment does not have yet, so the adapter cannot offer it even if an
    # operator tried to release it.
    return Mapping(DeviceKind.LOCK, (), ())


def _climate(entity: HaEntity) -> Mapping:
    capabilities: list[Capability] = []
    if entity.number("current_temperature") is not None:
        capabilities.append(Capability.CURRENT_TEMPERATURE)
    if entity.number("humidity") is not None or entity.number("current_humidity") is not None:
        capabilities.append(Capability.HUMIDITY)

    writable: list[Capability] = []
    constraints = DeviceConstraints()
    if entity.number("temperature") is not None:
        capabilities.append(Capability.TARGET_TEMPERATURE)
        writable.append(Capability.TARGET_TEMPERATURE)
        # The room's own limits, taken from the thermostat rather than assumed.
        constraints = DeviceConstraints(
            target_temperature_min_c=entity.number("min_temp"),
            target_temperature_max_c=entity.number("max_temp"),
            target_temperature_step_c=entity.number("target_temp_step") or 0.5,
        )
    return Mapping(DeviceKind.THERMOSTAT, tuple(capabilities), tuple(writable), constraints)


def _sensor(entity: HaEntity) -> Mapping | None:
    # A household instance is mostly sensors. Only a temperature reading has a
    # place on a home screen; the rest would be noise with no control attached.
    if entity.attributes.get("device_class") != "temperature":
        return None
    return Mapping(DeviceKind.SENSOR, (Capability.CURRENT_TEMPERATURE,), ())


_MAPPERS = {
    "light": _light,
    "switch": _switch,
    "media_player": _media_player,
    "lock": _lock,
    "climate": _climate,
    "sensor": _sensor,
}

#: Domains an operator may release for writing. ``lock`` is absent on purpose
#: and naming it in configuration is refused, not quietly ignored.
RELEASABLE_DOMAINS = frozenset({"light", "switch", "media_player", "climate"})

SUPPORTED_DOMAINS = frozenset(_MAPPERS)


def describe(entity: HaEntity) -> Mapping | None:
    """What this entity becomes, or None if HomeFlow has no use for it."""
    mapper = _MAPPERS.get(entity.domain)
    return mapper(entity) if mapper is not None else None


def availability(entity: HaEntity) -> Availability:
    if entity.state == _UNAVAILABLE:
        return Availability.OFFLINE
    if entity.state == _UNKNOWN:
        return Availability.UNKNOWN
    return Availability.ONLINE


def normalise(entity: HaEntity, kind: DeviceKind) -> DeviceState:
    """Canonical state for one entity.

    An unreported value stays absent rather than becoming a default: "not
    reported" and "reported as zero" are different facts.
    """
    if entity.state in (_UNAVAILABLE, _UNKNOWN):
        # Nothing about the entity is known right now, including its old value.
        return DeviceState()

    if kind in (DeviceKind.LIGHT, DeviceKind.SWITCH):
        brightness = entity.number("brightness")
        return DeviceState(
            power=entity.state == "on",
            brightness=(
                round(brightness * 100 / _HA_BRIGHTNESS_MAX) if brightness is not None else None
            ),
        )

    if kind is DeviceKind.MEDIA_PLAYER:
        volume = entity.number("volume_level")
        return DeviceState(
            playback=_PLAYBACK_STATES.get(entity.state, PlaybackState.UNKNOWN),
            volume=round(volume * 100) if volume is not None else None,
            program_name=entity.text("media_title"),
        )

    if kind is DeviceKind.LOCK:
        return DeviceState(lock_state=_LOCK_STATES.get(entity.state, LockState.UNKNOWN))

    if kind is DeviceKind.THERMOSTAT:
        return DeviceState(
            current_temperature_c=entity.number("current_temperature"),
            target_temperature_c=entity.number("temperature"),
            # An "off" thermostat is not heating; anything else may be.
            heater=None if entity.state == "off" else entity.state in ("heat", "heat_cool"),
        )

    if kind is DeviceKind.SENSOR:
        try:
            return DeviceState(current_temperature_c=float(entity.state))
        except ValueError:
            return DeviceState()

    return DeviceState()  # pragma: no cover - every mapped kind is handled


def display_name(entity: HaEntity) -> str:
    """What to call it on screen.

    The friendly name is household data -- it may well be a person's name -- so
    it is shown to the household and never logged or committed.
    """
    return entity.text("friendly_name", limit=80) or entity.entity_id.split(".", 1)[1]
