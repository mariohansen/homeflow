"""What a Home Assistant entity is allowed to become.

The mapping is where every decision that matters lives, so it is tested on its
own, with no Home Assistant anywhere near it.
"""

from __future__ import annotations

import pytest

from homeflow.capabilities import Capability, DeviceKind
from homeflow.devices.models import Availability, LockState, PlaybackState
from homeflow.integrations.home_assistant.entities import (
    RELEASABLE_DOMAINS,
    HaEntity,
    availability,
    describe,
    display_name,
    normalise,
)


def entity(entity_id: str, state: str, **attributes: object) -> HaEntity:
    return HaEntity(entity_id=entity_id, state=state, attributes=dict(attributes))


def mapped(item: HaEntity):
    mapping = describe(item)
    assert mapping is not None
    return mapping


# -- what becomes a device -------------------------------------------------


def test_a_dimmable_light_may_be_dimmed() -> None:
    mapping = mapped(entity("light.a", "on", supported_color_modes=["color_temp"]))
    assert mapping.kind is DeviceKind.LIGHT
    assert set(mapping.capabilities) == {Capability.POWER, Capability.BRIGHTNESS}


def test_a_light_that_cannot_dim_gets_no_brightness() -> None:
    """A control the device cannot perform must never be offered."""
    mapping = mapped(entity("light.a", "on", supported_color_modes=["onoff"]))
    assert set(mapping.capabilities) == {Capability.POWER}


def test_a_speaker_claims_only_the_features_it_advertises() -> None:
    quiet = mapped(entity("media_player.a", "playing", supported_features=16384 | 1))
    assert set(quiet.capabilities) == {Capability.MEDIA_PLAYBACK}

    full = mapped(entity("media_player.b", "playing", supported_features=16384 | 1 | 4 | 32 | 16))
    assert set(full.capabilities) == {
        Capability.MEDIA_PLAYBACK,
        Capability.VOLUME,
        Capability.MEDIA_NEXT,
        Capability.MEDIA_PREVIOUS,
    }


def test_a_speaker_with_no_declared_features_gets_no_controls() -> None:
    assert mapped(entity("media_player.a", "playing")).capabilities == ()


def test_a_thermostat_takes_its_limits_from_the_device() -> None:
    mapping = mapped(
        entity(
            "climate.a",
            "heat",
            current_temperature=20.0,
            temperature=21.0,
            min_temp=5.0,
            max_temp=30.0,
            target_temp_step=0.5,
        )
    )
    assert mapping.kind is DeviceKind.THERMOSTAT
    assert mapping.constraints.target_temperature_min_c == 5.0
    assert mapping.constraints.target_temperature_max_c == 30.0
    assert Capability.TARGET_TEMPERATURE in mapping.capabilities


def test_a_thermostat_that_reports_no_setpoint_offers_none() -> None:
    mapping = mapped(entity("climate.a", "heat", current_temperature=20.0))
    assert Capability.TARGET_TEMPERATURE not in mapping.capabilities
    assert mapping.writable == ()


def test_only_temperature_sensors_are_imported() -> None:
    """A household instance is mostly sensors; importing all would bury the rest."""
    assert describe(entity("sensor.a", "13.4", device_class="temperature")) is not None
    assert describe(entity("sensor.b", "88", device_class="battery")) is None
    assert describe(entity("update.core", "off")) is None
    assert describe(entity("automation.wake", "on")) is None
    assert describe(entity("scene.evening", "on")) is None


# -- the door --------------------------------------------------------------


def test_a_door_is_shown_and_never_made_writable() -> None:
    mapping = mapped(entity("lock.front", "locked"))
    assert mapping.kind is DeviceKind.LOCK
    assert mapping.writable == (), "a door must carry no writable capability"
    assert mapping.capabilities == ()


def test_no_configuration_can_release_the_lock_domain() -> None:
    assert "lock" not in RELEASABLE_DOMAINS
    assert frozenset({"light", "switch", "media_player", "climate"}) == RELEASABLE_DOMAINS


# -- reading state ---------------------------------------------------------


def test_brightness_is_rescaled_to_percent() -> None:
    state = normalise(entity("light.a", "on", brightness=255), DeviceKind.LIGHT)
    assert state.power is True
    assert state.brightness == 100

    half = normalise(entity("light.a", "on", brightness=128), DeviceKind.LIGHT)
    assert half.brightness == 50


def test_volume_is_rescaled_to_percent() -> None:
    state = normalise(
        entity("media_player.a", "playing", volume_level=0.35), DeviceKind.MEDIA_PLAYER
    )
    assert state.volume == 35
    assert state.playback is PlaybackState.PLAYING


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("locked", LockState.LOCKED),
        ("unlocked", LockState.UNLOCKED),
        ("locking", LockState.UNKNOWN),
        ("unlocking", LockState.UNKNOWN),
        ("jammed", LockState.UNKNOWN),
        ("something_new", LockState.UNKNOWN),
    ],
)
def test_a_door_mid_movement_is_unknown_not_guessed(reported: str, expected: LockState) -> None:
    state = normalise(entity("lock.front", reported), DeviceKind.LOCK)
    assert state.lock_state is expected


def test_an_unavailable_entity_reports_nothing_rather_than_its_old_value() -> None:
    state = normalise(entity("light.a", "unavailable", brightness=255), DeviceKind.LIGHT)
    assert state.power is None
    assert state.brightness is None
    assert availability(entity("light.a", "unavailable")) is Availability.OFFLINE
    assert availability(entity("light.a", "unknown")) is Availability.UNKNOWN
    assert availability(entity("light.a", "on")) is Availability.ONLINE


def test_an_attribute_that_is_not_a_number_is_not_read_as_one() -> None:
    """An integration can put anything in attributes."""
    state = normalise(entity("light.a", "on", brightness="very"), DeviceKind.LIGHT)
    assert state.brightness is None

    nonsense = normalise(entity("sensor.a", "not a number"), DeviceKind.SENSOR)
    assert nonsense.current_temperature_c is None


def test_a_boolean_is_not_mistaken_for_a_number() -> None:
    state = normalise(entity("climate.a", "heat", current_temperature=True), DeviceKind.THERMOSTAT)
    assert state.current_temperature_c is None


def test_an_entity_id_that_is_not_one_is_refused() -> None:
    for bad in ("no_domain", "Light.Upper", "light.", "../etc/passwd", "light.a b"):
        with pytest.raises(ValueError, match="validation error"):
            HaEntity(entity_id=bad, state="on")


# -- names -----------------------------------------------------------------


def test_the_friendly_name_is_used_and_bounded() -> None:
    assert display_name(entity("light.a", "on", friendly_name="Ceiling Light")) == "Ceiling Light"
    long = display_name(entity("light.a", "on", friendly_name="x" * 500))
    assert len(long) == 80


def test_an_entity_without_a_name_falls_back_to_its_own_object_id() -> None:
    assert display_name(entity("light.hallway_spot", "on")) == "hallway_spot"
