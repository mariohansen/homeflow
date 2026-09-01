"""Datapoint layout for an AirJet controller, and the gate that guards it.

The byte layout of a status frame is product and firmware specific and is not
documented by the vendor. Writing to an offset that means something else on your
controller is how a hot tub gets told to do the wrong thing, so this module
treats the layout as a claim that has to be proven:

* a profile carries ``trusted = False`` until an operator has compared every
  decoded value against the physical control panel;
* an untrusted profile decodes for diagnostics only — the adapter refuses to
  register the device, so no wrong temperature ever reaches a screen;
* writing additionally requires the individual datapoint to be listed in
  ``writable``, which starts empty and is filled one capability at a time after
  each one has been observed on the device.

The shipped layout comes from community documentation and is **unverified**.
``scripts/bestway_probe.py`` is the tool that verifies or corrects it, and a
corrected layout can be supplied as JSON without changing this file.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from homeflow.integrations.bestway.protocol import MAX_PAYLOAD_BYTES


class Datapoint(StrEnum):
    CURRENT_TEMPERATURE = "CURRENT_TEMPERATURE"
    TARGET_TEMPERATURE = "TARGET_TEMPERATURE"
    HEATER = "HEATER"
    FILTER_PUMP = "FILTER_PUMP"
    BUBBLES = "BUBBLES"
    CONTROL_PANEL_LOCK = "CONTROL_PANEL_LOCK"
    #: True when the controller reports temperatures in Fahrenheit.
    UNIT_IS_FAHRENHEIT = "UNIT_IS_FAHRENHEIT"


#: Datapoints that carry a temperature and therefore need unit conversion.
TEMPERATURE_DATAPOINTS = frozenset({Datapoint.CURRENT_TEMPERATURE, Datapoint.TARGET_TEMPERATURE})

BOOLEAN_DATAPOINTS = frozenset(
    {
        Datapoint.HEATER,
        Datapoint.FILTER_PUMP,
        Datapoint.BUBBLES,
        Datapoint.CONTROL_PANEL_LOCK,
        Datapoint.UNIT_IS_FAHRENHEIT,
    }
)


class ProfileError(Exception):
    """The datapoint layout cannot be used as given."""


class ByteLocation(BaseModel):
    """A whole unsigned byte at ``offset``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = "byte"
    offset: int = Field(ge=0, lt=MAX_PAYLOAD_BYTES)


class BitLocation(BaseModel):
    """One bit of the byte at ``offset``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = "bit"
    offset: int = Field(ge=0, lt=MAX_PAYLOAD_BYTES)
    bit: int = Field(ge=0, le=7)


Location = ByteLocation | BitLocation


class DatapointProfile(BaseModel):
    """A claimed mapping from status bytes to canonical values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    #: Free-text note on where the layout came from, shown in the probe output.
    provenance: str = "unverified"
    #: Smallest status payload this layout can be applied to.
    minimum_payload_length: int = Field(ge=1, le=MAX_PAYLOAD_BYTES)
    locations: dict[Datapoint, Location]

    #: Verified device limits. Refusing a setpoint is safe; guessing is not.
    target_temperature_min_c: float = Field(ge=0.0, le=60.0)
    target_temperature_max_c: float = Field(ge=0.0, le=60.0)
    target_temperature_step_c: float = Field(gt=0.0, le=5.0)

    #: Where the controller's attribute value block starts inside the status
    #: payload, and how long it is. Writing echoes that block back with one
    #: field changed, so it has to be locatable.
    control_values_offset: int | None = None
    control_values_length: int | None = None
    #: Bit each datapoint occupies in the control request's attribute flags.
    #: A separate namespace from the status layout, so it is stated rather than
    #: derived.
    control_flag_bits: dict[Datapoint, int] = Field(default_factory=dict)

    #: Set only by an operator who compared decoded values with the panel.
    trusted: bool = False
    #: Datapoints an operator has additionally verified as safe to write.
    writable: frozenset[Datapoint] = frozenset()

    @field_validator("target_temperature_max_c")
    @classmethod
    def _range_is_ordered(cls, value: float, info: Any) -> float:
        minimum = info.data.get("target_temperature_min_c")
        if minimum is not None and value <= minimum:
            raise ValueError("target_temperature_max_c must exceed target_temperature_min_c")
        return value

    @field_validator("writable")
    @classmethod
    def _writable_is_known(cls, value: frozenset[Datapoint], info: Any) -> frozenset[Datapoint]:
        if Datapoint.UNIT_IS_FAHRENHEIT in value:
            raise ValueError("the temperature unit is not a HomeFlow-writable datapoint")
        locations = info.data.get("locations") or {}
        unknown = {item for item in value if item not in locations}
        if unknown:
            raise ValueError(f"writable datapoints have no location: {sorted(unknown)}")
        flag_bits = info.data.get("control_flag_bits") or {}
        unflagged = {item for item in value if item not in flag_bits}
        if unflagged:
            raise ValueError(
                "these datapoints have no control flag bit, so the controller "
                f"cannot be told to apply them: {sorted(unflagged)}"
            )
        return value

    def may_write(self, datapoint: Datapoint) -> bool:
        """Writing needs both a proven layout and a proven datapoint."""
        return self.trusted and datapoint in self.writable

    def decode(self, payload: bytes) -> dict[Datapoint, int | bool]:
        """Decode a status payload. Raises :class:`ProfileError` if it is too short."""
        if len(payload) < self.minimum_payload_length:
            raise ProfileError(
                f"status payload is {len(payload)} bytes, "
                f"the layout needs at least {self.minimum_payload_length}"
            )

        decoded: dict[Datapoint, int | bool] = {}
        for datapoint, location in self.locations.items():
            if location.offset >= len(payload):
                raise ProfileError(f"{datapoint.value} points past the end of the payload")
            raw = payload[location.offset]
            if isinstance(location, BitLocation):
                decoded[datapoint] = bool(raw >> location.bit & 1)
            else:
                decoded[datapoint] = raw
        return decoded

    def location_for(self, datapoint: Datapoint) -> Location:
        location = self.locations.get(datapoint)
        if location is None:
            raise ProfileError(f"{datapoint.value} is not part of this layout")
        return location

    def encode_control(
        self,
        datapoint: Datapoint,
        value: int | bool,
        *,
        base_payload: bytes,
    ) -> tuple[int, bytes]:
        """Return ``(attr_flags, attr_vals)`` for a control request.

        The value block is the one the controller reported moments earlier with
        a single field changed, so even a controller that applied more than it
        was asked to would land on the state this gateway intended. The
        permission check lives here, where the bytes are produced, so no path
        upstream can bypass it.
        """
        if not self.may_write(datapoint):
            raise ProfileError(
                f"writing {datapoint.value} is not permitted: the datapoint layout is "
                "not verified against the physical controller, or this capability has "
                "not been released yet"
            )
        if self.control_values_offset is None or self.control_values_length is None:
            raise ProfileError("this layout does not say where the control values sit")

        start = self.control_values_offset
        end = start + self.control_values_length
        if len(base_payload) < end:
            raise ProfileError("no verified status block to build the control request from")

        flag_bit = self.control_flag_bits.get(datapoint)
        if flag_bit is None:
            raise ProfileError(f"no control flag bit is known for {datapoint.value}")

        location = self.location_for(datapoint)
        values = bytearray(base_payload[start:end])
        index = location.offset - start
        if not 0 <= index < len(values):
            raise ProfileError(f"{datapoint.value} sits outside the control value block")

        if isinstance(location, BitLocation):
            mask = 1 << location.bit
            if value:
                values[index] |= mask
            else:
                values[index] &= 0xFF ^ mask
        else:
            number = int(value)
            if not 0 <= number <= 0xFF:
                raise ProfileError(f"{datapoint.value} value does not fit in one byte")
            values[index] = number

        return 1 << flag_bit, bytes(values)


#: Layout taken from community documentation of AirJet controllers. It is a
#: starting point for verification, never a fact: the offsets below have not
#: been confirmed against any physical device in this repository, and both
#: `trusted` and `writable` are deliberately left at their refusing defaults.
CANDIDATE_PROFILE = DatapointProfile(
    name="airjet-candidate",
    provenance="community documentation, unverified against hardware",
    minimum_payload_length=12,
    locations={
        Datapoint.CURRENT_TEMPERATURE: ByteLocation(offset=6),
        Datapoint.TARGET_TEMPERATURE: ByteLocation(offset=5),
        Datapoint.HEATER: BitLocation(offset=4, bit=2),
        Datapoint.FILTER_PUMP: BitLocation(offset=4, bit=1),
        Datapoint.BUBBLES: BitLocation(offset=4, bit=0),
        Datapoint.CONTROL_PANEL_LOCK: BitLocation(offset=4, bit=3),
        Datapoint.UNIT_IS_FAHRENHEIT: BitLocation(offset=4, bit=4),
    },
    control_values_offset=4,
    control_values_length=8,
    control_flag_bits={
        Datapoint.BUBBLES: 0,
        Datapoint.FILTER_PUMP: 1,
        Datapoint.HEATER: 2,
        Datapoint.CONTROL_PANEL_LOCK: 3,
        Datapoint.TARGET_TEMPERATURE: 7,
    },
    target_temperature_min_c=20.0,
    target_temperature_max_c=40.0,
    target_temperature_step_c=1.0,
)

#: Layout of a controller that reports a nineteen byte status block, worked out
#: by pressing one button at a time on the physical panel and watching which bit
#: moved. Turning the heater on set two bits at once, and turning it off cleared
#: only one, which is what separates the heater from the filter pump it drives.
#:
#: Still ``trusted: false``: it was derived on one controller, and every
#: deployment confirms it against its own panel before anything is exposed.
#:
#: Two limitations are deliberate rather than forgotten:
#:  * the temperature unit flag was not identified, so values are read as
#:    Celsius. A controller switched to Fahrenheit would report wrongly.
#:  * bits 0 and 6 were set throughout and remain unexplained.
AIRJET_19BYTE_PROFILE = DatapointProfile(
    name="airjet-19byte",
    provenance=(
        "observed against a physical controller: bits by button press, "
        "temperature range read off the panel"
    ),
    minimum_payload_length=19,
    locations={
        # Byte 0 is the message type and flips between request and report, so
        # it carries no device state.
        Datapoint.TARGET_TEMPERATURE: ByteLocation(offset=2),
        Datapoint.CURRENT_TEMPERATURE: ByteLocation(offset=15),
        Datapoint.HEATER: BitLocation(offset=1, bit=1),
        Datapoint.FILTER_PUMP: BitLocation(offset=1, bit=2),
        Datapoint.BUBBLES: BitLocation(offset=1, bit=3),
        Datapoint.CONTROL_PANEL_LOCK: BitLocation(offset=1, bit=4),
    },
    # The attribute value block the controller echoes back: the flag byte, the
    # setpoint, then the timer fields.
    control_values_offset=1,
    control_values_length=14,
    # Which bit of the control request's attribute flags names each datapoint.
    # Flags follow the order the attributes are declared in, and the first seven
    # are the booleans packed into the flag byte, in the same bit order. Four of
    # those were confirmed by pressing buttons on a controller.
    #
    # The setpoint is the eighth attribute and the byte right after the flag
    # byte in the value block, so bit 7 follows from the ordering. Derived that
    # way rather than read from documentation, then confirmed on a controller:
    # the setpoint changed and the panel agreed.
    control_flag_bits={
        Datapoint.HEATER: 1,
        Datapoint.FILTER_PUMP: 2,
        Datapoint.BUBBLES: 3,
        Datapoint.CONTROL_PANEL_LOCK: 4,
        Datapoint.TARGET_TEMPERATURE: 7,
    },
    # Read off the physical panel by holding each button to its stop.
    target_temperature_min_c=20.0,
    target_temperature_max_c=40.0,
    target_temperature_step_c=1.0,
)

_BUILTIN: Mapping[str, DatapointProfile] = {
    CANDIDATE_PROFILE.name: CANDIDATE_PROFILE,
    AIRJET_19BYTE_PROFILE.name: AIRJET_19BYTE_PROFILE,
}


def builtin_profile(name: str) -> DatapointProfile:
    profile = _BUILTIN.get(name)
    if profile is None:
        raise ProfileError(f"no built-in datapoint layout named {name!r}")
    return profile


def load_profile(path: Path) -> DatapointProfile:
    """Load an operator-supplied layout.

    This is administrator configuration, not client input, but it still decides
    what bytes get written to a heater, so it is validated strictly.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfileError(f"cannot read the datapoint layout at {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProfileError("the datapoint layout is not valid JSON") from exc

    try:
        return DatapointProfile.model_validate(raw)
    except ValidationError as exc:
        raise ProfileError(
            f"the datapoint layout is invalid: {exc.error_count()} problem(s)"
        ) from exc


def to_celsius(value: float, *, fahrenheit: bool) -> float:
    return round((value - 32.0) * 5.0 / 9.0, 1) if fahrenheit else float(value)


def from_celsius(value: float, *, fahrenheit: bool) -> int:
    """Convert a Celsius setpoint into the unit the controller expects."""
    converted = value * 9.0 / 5.0 + 32.0 if fahrenheit else value
    return round(converted)
