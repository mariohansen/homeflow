"""Provider adapter for Home Assistant.

Home Assistant is an integration gateway here, not the product: it speaks to
Hue, Sonos, tado and the rest, and this adapter turns what it knows into
canonical devices. Nothing above it learns that Home Assistant exists, and no
entity id reaches a client (see docs/adr/0004-home-assistant-as-integration-gateway.md).

Writing is released one domain at a time, the same discipline the Bestway
adapter uses. Until an operator releases a domain, its entities are advertised
without their writable capabilities, so the command pipeline refuses the action
and the client cannot render a control for it. The state is still shown --
knowing the living room light is on is useful before being able to switch it.

``lock`` can never be released. Door control needs the fresh device-owner
authorisation described in SECURITY.md, and until that exists the adapter shows
whether the door is locked and offers nothing else.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass, field
from typing import Any

from homeflow.capabilities import DeviceKind
from homeflow.clock import Clock, SystemClock
from homeflow.commands.models import (
    Action,
    BrightnessParams,
    OnOffParams,
    PlaybackCommand,
    PlaybackParams,
    TargetTemperatureParams,
    VolumeParams,
)
from homeflow.devices.models import DeviceState
from homeflow.integrations.base.errors import ProviderRejectedError, ProviderUnavailableError
from homeflow.integrations.base.models import (
    CommandOutcome,
    ProviderCommand,
    ProviderCommandResult,
    ProviderDevice,
    ProviderDeviceRef,
    ProviderEvent,
    ProviderState,
)
from homeflow.integrations.home_assistant.client import HomeAssistantClient
from homeflow.integrations.home_assistant.entities import (
    RELEASABLE_DOMAINS,
    HaEntity,
    availability,
    describe,
    display_name,
    normalise,
)
from homeflow.log import get_logger

_logger = get_logger(__name__)

PROVIDER_NAME = "home_assistant"

#: Home Assistant reports a service call's effect immediately, but an
#: integration behind it may take a moment to catch up. Reading is idempotent
#: and safe to repeat; the service call itself never is.
DEFAULT_CONFIRM_ATTEMPTS = 4
DEFAULT_CONFIRM_DELAY_SECONDS = 0.4


def ref_for(entity_id: str) -> ProviderDeviceRef:
    return ProviderDeviceRef(provider=PROVIDER_NAME, provider_device_id=entity_id)


@dataclass(frozen=True, slots=True)
class _Call:
    """One Home Assistant service call, derived from a canonical command."""

    service: str
    data: dict[str, Any]
    #: What the entity should read as afterwards, for the read-back.
    expected: DeviceState


@dataclass(slots=True)
class HomeAssistantProvider:
    client: HomeAssistantClient
    #: Domains an operator has released for writing, after checking them.
    released_domains: frozenset[str] = frozenset()
    clock: Clock = field(default_factory=SystemClock)
    confirm_attempts: int = DEFAULT_CONFIRM_ATTEMPTS
    confirm_delay_seconds: float = DEFAULT_CONFIRM_DELAY_SECONDS

    #: Entity id to kind, filled by discovery. An entity that is not in here
    #: was never advertised and cannot be commanded.
    _kinds: dict[str, DeviceKind] = field(default_factory=dict, init=False)

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    async def aclose(self) -> None:
        await self.client.aclose()

    async def discover_devices(self) -> Sequence[ProviderDevice]:
        entities = await self.client.states()
        rooms = await self.client.rooms()

        devices: list[ProviderDevice] = []
        self._kinds.clear()
        for entity in entities:
            mapping = describe(entity)
            if mapping is None:
                continue

            # RELEASABLE_DOMAINS is the outer bound; configuration cannot widen
            # it, so a door stays read-only whatever an operator writes down.
            released = (
                entity.domain in self.released_domains and entity.domain in RELEASABLE_DOMAINS
            )
            capabilities = (
                mapping.capabilities
                if released
                else tuple(item for item in mapping.capabilities if item not in mapping.writable)
            )
            self._kinds[entity.entity_id] = mapping.kind
            devices.append(
                ProviderDevice(
                    ref=ref_for(entity.entity_id),
                    suggested_name=display_name(entity),
                    kind=mapping.kind,
                    capabilities=capabilities,
                    room_hint=rooms.get(entity.entity_id),
                    constraints=mapping.constraints,
                )
            )

        _logger.info(
            "home_assistant.discovered",
            provider=PROVIDER_NAME,
            entity_count=len(entities),
            device_count=len(devices),
            released_for_writing=sorted(self.released_domains),
        )
        return devices

    async def get_state(self, device_ref: ProviderDeviceRef) -> ProviderState:
        entity = await self.client.state(self._entity_id(device_ref))
        return self._snapshot(entity)

    async def execute(
        self,
        device_ref: ProviderDeviceRef,
        command: ProviderCommand,
    ) -> ProviderCommandResult:
        entity_id = self._entity_id(device_ref)
        domain = entity_id.split(".", 1)[0]
        kind = self._kinds[entity_id]

        if domain not in self.released_domains or domain not in RELEASABLE_DOMAINS:
            # Defence in depth. The capability was never advertised, so the
            # command service already refused this; if that check is ever
            # weakened, the adapter still will not write.
            raise ProviderRejectedError("this domain has not been released for writing")

        call = self._call_for(command)
        changed = await self.client.call_service(
            domain, call.service, {"entity_id": entity_id} | call.data
        )

        # Home Assistant answers with what it touched. If our entity is in there
        # and already reads correctly, there is nothing to wait for.
        for entity in changed:
            if entity.entity_id == entity_id and self._matches(entity, kind, call.expected):
                return ProviderCommandResult(
                    outcome=CommandOutcome.APPLIED, state=self._snapshot(entity)
                )

        observed = await self._confirm(entity_id, kind, call.expected)
        if observed is None:
            return ProviderCommandResult(
                outcome=CommandOutcome.UNKNOWN, failure_code="read_back_failed"
            )
        state = self._snapshot(observed)
        if self._matches(observed, kind, call.expected):
            return ProviderCommandResult(outcome=CommandOutcome.APPLIED, state=state)
        return ProviderCommandResult(
            outcome=CommandOutcome.UNKNOWN,
            state=state,
            failure_code="not_confirmed_by_device",
        )

    async def subscribe(self) -> AsyncGenerator[ProviderEvent]:
        """Live state, re-synchronised every time the socket comes back.

        A reconnect means events were missed while it was down, so the stream
        opens with a full snapshot rather than letting held state quietly drift.
        """
        async for entity in self._stream():
            kind = self._kinds.get(entity.entity_id)
            if kind is None:
                # An entity that appeared after discovery, or one HomeFlow has
                # no use for. Either way it is not a device here.
                continue
            yield ProviderEvent(ref=ref_for(entity.entity_id), state=self._snapshot(entity))

    async def _stream(self) -> AsyncGenerator[HaEntity]:
        for entity in await self.client.states():
            yield entity
        async for entity in self.client.events():
            yield entity

    # -- command translation ----------------------------------------------

    def _call_for(self, command: ProviderCommand) -> _Call:
        params = command.params

        if command.action is Action.SET_POWER and isinstance(params, OnOffParams):
            return _Call(
                service="turn_on" if params.on else "turn_off",
                data={},
                expected=DeviceState(power=params.on),
            )

        if command.action is Action.SET_BRIGHTNESS and isinstance(params, BrightnessParams):
            # Turning a light to zero brightness is turning it off; Home
            # Assistant treats brightness_pct 0 as an error on some platforms.
            if params.brightness == 0:
                return _Call(service="turn_off", data={}, expected=DeviceState(power=False))
            return _Call(
                service="turn_on",
                data={"brightness_pct": params.brightness},
                expected=DeviceState(power=True, brightness=params.brightness),
            )

        if command.action is Action.SET_VOLUME and isinstance(params, VolumeParams):
            return _Call(
                service="volume_set",
                data={"volume_level": round(params.volume / 100, 2)},
                expected=DeviceState(volume=params.volume),
            )

        if command.action is Action.SET_PLAYBACK and isinstance(params, PlaybackParams):
            playing = params.playback is PlaybackCommand.PLAY
            return _Call(
                service="media_play" if playing else "media_pause",
                data={},
                expected=DeviceState(),
            )

        if command.action is Action.SET_TARGET_TEMPERATURE and isinstance(
            params, TargetTemperatureParams
        ):
            return _Call(
                service="set_temperature",
                data={"temperature": params.celsius},
                expected=DeviceState(target_temperature_c=params.celsius),
            )

        raise ProviderRejectedError("Home Assistant has no service for this action")

    async def _confirm(
        self,
        entity_id: str,
        kind: DeviceKind,
        expected: DeviceState,
    ) -> HaEntity | None:
        latest: HaEntity | None = None
        for attempt in range(self.confirm_attempts):
            if attempt:
                await asyncio.sleep(self.confirm_delay_seconds)
            try:
                latest = await self.client.state(entity_id)
            except ProviderUnavailableError:
                continue
            if self._matches(latest, kind, expected):
                return latest
        return latest

    def _matches(self, entity: HaEntity, kind: DeviceKind, expected: DeviceState) -> bool:
        """Does the entity now read the way the command asked for?

        Only the fields the command was about are compared. A light that came on
        at a slightly different brightness than requested still counts as on;
        the state that is stored is the measured one either way.
        """
        wanted = expected.model_dump(exclude_none=True)
        if not wanted:
            # Nothing to check against, such as a playback command whose result
            # depends on the source. The read-back state is reported as is.
            return True
        observed = normalise(entity, kind)
        for key, value in wanted.items():
            seen = getattr(observed, key)
            if isinstance(value, float) and isinstance(seen, float):
                if abs(seen - value) > 0.51:
                    return False
            elif key == "brightness":
                # Percent is a lossy view of 0-255; neighbouring values are the
                # same instruction, not a failure to follow it.
                if seen is None or abs(int(seen) - int(value)) > 1:
                    return False
            elif seen != value:
                return False
        return True

    # -- helpers -----------------------------------------------------------

    def _entity_id(self, device_ref: ProviderDeviceRef) -> str:
        if device_ref.provider != PROVIDER_NAME:
            raise ProviderUnavailableError("unknown device reference")
        entity_id = device_ref.provider_device_id
        if entity_id not in self._kinds:
            raise ProviderUnavailableError("this device is not known to the adapter")
        return entity_id

    def _snapshot(self, entity: HaEntity) -> ProviderState:
        kind = self._kinds.get(entity.entity_id, DeviceKind.SENSOR)
        return ProviderState(
            state=normalise(entity, kind),
            availability=availability(entity),
            observed_at=self.clock.now(),
        )


__all__ = ["PROVIDER_NAME", "HomeAssistantProvider", "ref_for"]
