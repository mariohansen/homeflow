"""Vendor-independent device taxonomy (see docs/adr/0008-canonical-capability-model.md).

`DeviceKind` guides presentation. `Capability` is the authorisation surface:
a command is only accepted when the device actually declares the capability it
requires, so an adapter can never be talked into an unsupported action.
"""

from __future__ import annotations

from enum import StrEnum


class DeviceKind(StrEnum):
    LIGHT = "LIGHT"
    MEDIA_PLAYER = "MEDIA_PLAYER"
    LOCK = "LOCK"
    DOORBELL = "DOORBELL"
    CAMERA = "CAMERA"
    INTERCOM = "INTERCOM"
    THERMOSTAT = "THERMOSTAT"
    POOL = "POOL"
    WASHING_MACHINE = "WASHING_MACHINE"
    DISHWASHER = "DISHWASHER"
    VOICE_ASSISTANT = "VOICE_ASSISTANT"
    SENSOR = "SENSOR"
    SWITCH = "SWITCH"


class Capability(StrEnum):
    POWER = "POWER"
    BRIGHTNESS = "BRIGHTNESS"
    COLOR = "COLOR"
    COLOR_TEMPERATURE = "COLOR_TEMPERATURE"

    MEDIA_PLAYBACK = "MEDIA_PLAYBACK"
    MEDIA_NEXT = "MEDIA_NEXT"
    MEDIA_PREVIOUS = "MEDIA_PREVIOUS"
    VOLUME = "VOLUME"
    GROUPING = "GROUPING"

    LOCK = "LOCK"
    UNLOCK = "UNLOCK"
    UNLATCH = "UNLATCH"

    LIVE_VIDEO = "LIVE_VIDEO"
    DOORBELL_EVENTS = "DOORBELL_EVENTS"
    MOTION_EVENTS = "MOTION_EVENTS"

    CURRENT_TEMPERATURE = "CURRENT_TEMPERATURE"
    TARGET_TEMPERATURE = "TARGET_TEMPERATURE"
    HEATING = "HEATING"
    FILTER = "FILTER"
    BUBBLES = "BUBBLES"
    CONTROL_PANEL_LOCK = "CONTROL_PANEL_LOCK"

    HVAC_MODE = "HVAC_MODE"
    HUMIDITY = "HUMIDITY"

    PROGRAM_STATUS = "PROGRAM_STATUS"
    PROGRAM_ACTIONS = "PROGRAM_ACTIONS"
    REMAINING_TIME = "REMAINING_TIME"

    ANNOUNCEMENT = "ANNOUNCEMENT"
    TEXT_COMMAND = "TEXT_COMMAND"
