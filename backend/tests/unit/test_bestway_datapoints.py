"""The datapoint layout and the gate that keeps an unproven one read-only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from homeflow.integrations.bestway.datapoints import (
    AIRJET_19BYTE_PROFILE,
    CANDIDATE_PROFILE,
    BitLocation,
    ByteLocation,
    Datapoint,
    DatapointProfile,
    ProfileError,
    builtin_profile,
    from_celsius,
    load_profile,
    to_celsius,
)
from simulators.bestway_simulator import airjet19_payload

# flags at 4, target at 5, current at 6
PAYLOAD = bytes([0x01, 0x00, 0x00, 0x00, 0b00000110, 38, 27, 0, 0, 0, 0, 0])


def trusted(*writable: Datapoint) -> DatapointProfile:
    return DatapointProfile.model_validate(
        CANDIDATE_PROFILE.model_dump() | {"trusted": True, "writable": frozenset(writable)}
    )


def test_the_shipped_layout_refuses_everything_until_it_is_proven() -> None:
    assert CANDIDATE_PROFILE.trusted is False
    assert CANDIDATE_PROFILE.writable == frozenset()
    for datapoint in Datapoint:
        assert CANDIDATE_PROFILE.may_write(datapoint) is False


def test_decoding_reads_bytes_and_bits() -> None:
    decoded = CANDIDATE_PROFILE.decode(PAYLOAD)
    assert decoded[Datapoint.TARGET_TEMPERATURE] == 38
    assert decoded[Datapoint.CURRENT_TEMPERATURE] == 27
    assert decoded[Datapoint.HEATER] is True
    assert decoded[Datapoint.FILTER_PUMP] is True
    assert decoded[Datapoint.BUBBLES] is False
    assert decoded[Datapoint.CONTROL_PANEL_LOCK] is False


def test_a_short_payload_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ProfileError, match="at least"):
        CANDIDATE_PROFILE.decode(PAYLOAD[:4])


def test_writing_is_refused_while_the_layout_is_unproven() -> None:
    with pytest.raises(ProfileError, match="not verified"):
        CANDIDATE_PROFILE.encode_control(Datapoint.HEATER, False, base_payload=PAYLOAD)


def test_writing_is_refused_for_a_capability_that_was_not_released() -> None:
    profile = trusted(Datapoint.HEATER)
    profile.encode_control(Datapoint.HEATER, False, base_payload=PAYLOAD)
    with pytest.raises(ProfileError, match="not been released"):
        profile.encode_control(Datapoint.BUBBLES, True, base_payload=PAYLOAD)


def test_a_control_request_flags_and_changes_one_field() -> None:
    profile = trusted(Datapoint.BUBBLES)
    flags, values = profile.encode_control(Datapoint.BUBBLES, True, base_payload=PAYLOAD)

    assert flags == 1 << 0, "only the bubbles attribute may be flagged"
    base = PAYLOAD[4:12]
    differing = [i for i, (a, b) in enumerate(zip(base, values, strict=True)) if a != b]
    assert differing == [0]
    # Neighbouring bits in the flag byte are carried over untouched.
    assert values[0] & 0b0000_0001
    assert values[0] & 0b0000_0010
    assert values[0] & 0b0000_0100


def test_clearing_a_bit_leaves_the_others_alone() -> None:
    profile = trusted(Datapoint.HEATER)
    flags, values = profile.encode_control(Datapoint.HEATER, False, base_payload=PAYLOAD)
    assert flags == 1 << 2
    assert not values[0] & 0b0000_0100
    assert values[0] & 0b0000_0010


def test_a_setpoint_that_does_not_fit_a_byte_is_refused() -> None:
    profile = trusted(Datapoint.TARGET_TEMPERATURE)
    with pytest.raises(ProfileError, match="one byte"):
        profile.encode_control(Datapoint.TARGET_TEMPERATURE, 300, base_payload=PAYLOAD)


def test_writing_without_an_observed_block_is_refused() -> None:
    profile = trusted(Datapoint.HEATER)
    with pytest.raises(ProfileError, match="no verified status block"):
        profile.encode_control(Datapoint.HEATER, True, base_payload=b"\x00\x01")


def test_releasing_an_unmapped_datapoint_fails_at_configuration_time() -> None:
    with pytest.raises(ValidationError, match="no location"):
        DatapointProfile.model_validate(
            {
                "name": "partial",
                "minimum_payload_length": 8,
                "locations": {Datapoint.CURRENT_TEMPERATURE: {"kind": "byte", "offset": 6}},
                "target_temperature_min_c": 20.0,
                "target_temperature_max_c": 40.0,
                "target_temperature_step_c": 1.0,
                "trusted": True,
                "writable": [Datapoint.HEATER],
            }
        )


def test_the_temperature_unit_is_not_writable() -> None:
    with pytest.raises(ValidationError, match="not a HomeFlow-writable"):
        DatapointProfile.model_validate(
            CANDIDATE_PROFILE.model_dump()
            | {"trusted": True, "writable": [Datapoint.UNIT_IS_FAHRENHEIT]}
        )


def test_an_inverted_temperature_range_is_refused() -> None:
    with pytest.raises(ValidationError, match="must exceed"):
        DatapointProfile.model_validate(
            CANDIDATE_PROFILE.model_dump()
            | {"target_temperature_min_c": 40.0, "target_temperature_max_c": 20.0}
        )


def test_locations_must_stay_inside_a_plausible_payload() -> None:
    with pytest.raises(ValidationError):
        ByteLocation(offset=-1)
    with pytest.raises(ValidationError):
        BitLocation(offset=0, bit=8)


def test_unit_conversion_round_trips() -> None:
    assert to_celsius(38, fahrenheit=False) == 38.0
    assert to_celsius(100, fahrenheit=True) == pytest.approx(37.8, abs=0.05)
    assert from_celsius(38.0, fahrenheit=False) == 38
    assert from_celsius(37.8, fahrenheit=True) == 100


def test_a_layout_can_be_supplied_as_json(tmp_path: Path) -> None:
    path = tmp_path / "layout.json"
    path.write_text(json.dumps(CANDIDATE_PROFILE.model_dump(mode="json")), encoding="utf-8")
    loaded = load_profile(path)
    assert loaded.name == CANDIDATE_PROFILE.name
    assert loaded.trusted is False


def test_a_broken_layout_file_is_reported_clearly(tmp_path: Path) -> None:
    path = tmp_path / "layout.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileError, match="valid JSON"):
        load_profile(path)

    path.write_text(json.dumps({"name": "x"}), encoding="utf-8")
    with pytest.raises(ProfileError, match="invalid"):
        load_profile(path)

    with pytest.raises(ProfileError, match="cannot read"):
        load_profile(tmp_path / "missing.json")


def test_an_unknown_builtin_layout_is_reported() -> None:
    assert builtin_profile("airjet-candidate") is CANDIDATE_PROFILE
    with pytest.raises(ProfileError, match="no built-in"):
        builtin_profile("nope")


# --- the layout observed on a physical controller -------------------------


def test_the_observed_layout_reads_a_nineteen_byte_block() -> None:
    payload = airjet19_payload(current_c=27, target_c=36, filter_pump=True)
    decoded = AIRJET_19BYTE_PROFILE.decode(payload)

    assert decoded[Datapoint.CURRENT_TEMPERATURE] == 27
    assert decoded[Datapoint.TARGET_TEMPERATURE] == 36
    assert decoded[Datapoint.FILTER_PUMP] is True
    assert decoded[Datapoint.HEATER] is False
    assert decoded[Datapoint.BUBBLES] is False
    assert decoded[Datapoint.CONTROL_PANEL_LOCK] is False


def test_the_heater_and_the_pump_are_separate_bits() -> None:
    """The observation that settled the layout: the heater drives the pump.

    Turning the heater on sets two bits; turning it off clears only one. A
    layout that conflated them would report the pump as off while it runs.
    """
    both = AIRJET_19BYTE_PROFILE.decode(airjet19_payload(heater=True, filter_pump=True))
    assert both[Datapoint.HEATER] is True
    assert both[Datapoint.FILTER_PUMP] is True

    pump_only = AIRJET_19BYTE_PROFILE.decode(airjet19_payload(heater=False, filter_pump=True))
    assert pump_only[Datapoint.HEATER] is False
    assert pump_only[Datapoint.FILTER_PUMP] is True


def test_the_observed_layout_still_refuses_to_write() -> None:
    with pytest.raises(ProfileError, match="not verified"):
        AIRJET_19BYTE_PROFILE.encode_control(
            Datapoint.HEATER, True, base_payload=airjet19_payload()
        )


def test_a_control_request_in_the_observed_layout_flags_one_attribute() -> None:
    released = DatapointProfile.model_validate(
        AIRJET_19BYTE_PROFILE.model_dump()
        | {"trusted": True, "writable": frozenset({Datapoint.BUBBLES})}
    )
    base = airjet19_payload(current_c=27, target_c=36, filter_pump=True)
    flags, values = released.encode_control(Datapoint.BUBBLES, True, base_payload=base)

    # wave_power is bit 3 of the control request's attribute flags.
    assert flags == 1 << 3
    assert len(values) == 14
    differing = [i for i, (a, b) in enumerate(zip(base[1:15], values, strict=True)) if a != b]
    assert differing == [0], "only the flag byte may differ"
    assert values[0] & 1 << 3, "bubbles must be set"
    assert values[0] & 1 << 2, "the running pump must be carried over"
    assert values[1] == 36, "the setpoint travels along unchanged"


def test_a_datapoint_without_a_control_flag_bit_cannot_be_released() -> None:
    """A layout that can read something cannot automatically write it."""
    without_flags = AIRJET_19BYTE_PROFILE.model_dump()
    without_flags["control_flag_bits"] = {Datapoint.HEATER: 1}
    with pytest.raises(ValidationError, match="no control flag bit"):
        DatapointProfile.model_validate(
            without_flags | {"trusted": True, "writable": frozenset({Datapoint.BUBBLES})}
        )


def test_the_setpoint_is_flagged_as_the_attribute_after_the_booleans() -> None:
    """Bit 7 follows the attribute order; the read-back is what confirms it."""
    assert AIRJET_19BYTE_PROFILE.control_flag_bits[Datapoint.TARGET_TEMPERATURE] == 7

    released = DatapointProfile.model_validate(
        AIRJET_19BYTE_PROFILE.model_dump()
        | {"trusted": True, "writable": frozenset({Datapoint.TARGET_TEMPERATURE})}
    )
    base = airjet19_payload(current_c=27, target_c=36, filter_pump=True)
    flags, values = released.encode_control(Datapoint.TARGET_TEMPERATURE, 38, base_payload=base)

    assert flags == 1 << 7
    assert values[1] == 38, "the setpoint sits right after the flag byte"
    assert values[0] == base[1], "the flag byte is carried over untouched"


def test_the_message_type_byte_is_not_treated_as_state() -> None:
    """Byte 0 flips between request and report; it must not be a datapoint."""
    offsets = {location.offset for location in AIRJET_19BYTE_PROFILE.locations.values()}
    assert 0 not in offsets
