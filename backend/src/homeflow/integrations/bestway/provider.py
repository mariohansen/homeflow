"""Provider adapter for a local Bestway AirJet controller.

The adapter is read-only until an operator has proven the datapoint layout, and
each write capability is released individually after it has been observed on the
physical panel. Two properties make that more than a promise:

* an unproven layout means ``discover_devices`` returns nothing, so a wrong
  temperature can never reach a screen;
* a capability that has not been released is never advertised, so the client
  cannot render a control for it and the command pipeline refuses the action.

A write is followed by a read-back. If the controller does not confirm the new
value the command settles as UNKNOWN rather than success, because a hot tub that
did not do what was asked must not look like one that did.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass, field

from homeflow.capabilities import Capability, DeviceKind
from homeflow.clock import Clock, SystemClock
from homeflow.commands.models import (
    Action,
    OnOffParams,
    TargetTemperatureParams,
)
from homeflow.devices.models import Availability, DeviceConstraints, DeviceState
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
from homeflow.integrations.bestway.client import BestwayClient, ControllerMisbehaved
from homeflow.integrations.bestway.datapoints import (
    Datapoint,
    DatapointProfile,
    ProfileError,
    from_celsius,
    to_celsius,
)
from homeflow.log import get_logger

_logger = get_logger(__name__)

PROVIDER_NAME = "bestway"
#: A controller hosts exactly one tub. The identifier is a role, not a serial
#: number, so nothing household-identifying enters the provider reference.
DEVICE_ID = "airjet"

DEFAULT_POLL_SECONDS = 15.0

#: What a released datapoint lets the client actually do.
_WRITE_CAPABILITIES: Mapping[Datapoint, Capability] = {
    Datapoint.TARGET_TEMPERATURE: Capability.TARGET_TEMPERATURE,
    Datapoint.HEATER: Capability.HEATING,
    Datapoint.FILTER_PUMP: Capability.FILTER,
    Datapoint.BUBBLES: Capability.BUBBLES,
    Datapoint.CONTROL_PANEL_LOCK: Capability.CONTROL_PANEL_LOCK,
}

_ACTION_DATAPOINTS: Mapping[Action, Datapoint] = {
    Action.SET_TARGET_TEMPERATURE: Datapoint.TARGET_TEMPERATURE,
    Action.SET_HEATER: Datapoint.HEATER,
    Action.SET_FILTER: Datapoint.FILTER_PUMP,
    Action.SET_BUBBLES: Datapoint.BUBBLES,
    Action.SET_CONTROL_PANEL_LOCK: Datapoint.CONTROL_PANEL_LOCK,
}


def airjet_ref() -> ProviderDeviceRef:
    return ProviderDeviceRef(provider=PROVIDER_NAME, provider_device_id=DEVICE_ID)


@dataclass(slots=True)
class BestwayProvider:
    """Talks to one controller and normalises it into the canonical model."""

    client: BestwayClient
    profile: DatapointProfile
    clock: Clock = field(default_factory=SystemClock)
    display_name: str = "Pool"
    room_hint: str | None = "Terrace"
    poll_seconds: float = DEFAULT_POLL_SECONDS

    _last_payload: bytes | None = field(default=None, init=False)
    _availability: Availability = field(default=Availability.UNKNOWN, init=False)

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    async def aclose(self) -> None:
        """Release the connection. Called when the gateway shuts down."""
        await self.client.close()

    # -- discovery ---------------------------------------------------------

    def capabilities(self) -> tuple[Capability, ...]:
        """Read capabilities always; write capabilities only once released."""
        found: list[Capability] = []
        if Datapoint.CURRENT_TEMPERATURE in self.profile.locations:
            found.append(Capability.CURRENT_TEMPERATURE)
        for datapoint, capability in _WRITE_CAPABILITIES.items():
            if self.profile.may_write(datapoint):
                found.append(capability)
        return tuple(found)

    async def discover_devices(self) -> Sequence[ProviderDevice]:
        if not self.profile.trusted:
            _logger.warning(
                "bestway.layout_unverified",
                provider=PROVIDER_NAME,
                profile=self.profile.name,
                note=(
                    "the controller is not exposed until its datapoint layout has been "
                    "compared with the physical panel; run scripts/bestway_probe.py"
                ),
            )
            return []

        return [
            ProviderDevice(
                ref=airjet_ref(),
                suggested_name=self.display_name,
                kind=DeviceKind.POOL,
                capabilities=self.capabilities(),
                room_hint=self.room_hint,
                constraints=DeviceConstraints(
                    target_temperature_min_c=self.profile.target_temperature_min_c,
                    target_temperature_max_c=self.profile.target_temperature_max_c,
                    target_temperature_step_c=self.profile.target_temperature_step_c,
                ),
            )
        ]

    # -- reading -----------------------------------------------------------

    async def get_state(self, device_ref: ProviderDeviceRef) -> ProviderState:
        self._require_known_device(device_ref)
        payload = await self._read_payload()
        return self._snapshot(payload)

    async def subscribe(self) -> AsyncGenerator[ProviderEvent]:
        """Poll the controller and yield a snapshot whenever something changes.

        The protocol pushes reports of its own, but polling on a fixed interval
        is the conservative choice for a device with no delivery guarantees, and
        it doubles as the liveness check.
        """
        previous: bytes | None = None
        while True:
            await asyncio.sleep(self.poll_seconds)
            try:
                payload = await self._read_payload()
            except ProviderUnavailableError:
                if self._availability is not Availability.OFFLINE:
                    self._availability = Availability.OFFLINE
                    yield ProviderEvent(ref=airjet_ref(), state=self._offline_snapshot())
                continue

            if payload != previous:
                previous = payload
                yield ProviderEvent(ref=airjet_ref(), state=self._snapshot(payload))

    # -- writing -----------------------------------------------------------

    async def execute(
        self,
        device_ref: ProviderDeviceRef,
        command: ProviderCommand,
    ) -> ProviderCommandResult:
        self._require_known_device(device_ref)

        datapoint = _ACTION_DATAPOINTS.get(command.action)
        if datapoint is None:
            raise ProviderRejectedError("the controller has no datapoint for this action")

        # Never write blind: the write is expressed as a modification of a block
        # the controller reported moments ago.
        base_payload = await self._read_payload()
        value = self._value_for(command, datapoint, base_payload)

        try:
            attr_flags, attr_vals = self.profile.encode_control(
                datapoint, value, base_payload=base_payload
            )
        except ProfileError as exc:
            raise ProviderRejectedError(str(exc)) from exc

        await self.client.send_control(attr_flags, attr_vals)

        # Read-after-write. A controller that did not take the change must not
        # be reported as if it had.
        try:
            observed = await self._read_payload()
        except ProviderUnavailableError:
            return ProviderCommandResult(
                outcome=CommandOutcome.UNKNOWN,
                failure_code="read_back_failed",
            )

        state = self._snapshot(observed)
        applied = self.profile.decode(observed).get(datapoint)
        if applied == value:
            return ProviderCommandResult(outcome=CommandOutcome.APPLIED, state=state)
        return ProviderCommandResult(
            outcome=CommandOutcome.UNKNOWN,
            state=state,
            failure_code="not_confirmed_by_device",
        )

    def _value_for(
        self,
        command: ProviderCommand,
        datapoint: Datapoint,
        base_payload: bytes,
    ) -> int | bool:
        params = command.params
        if isinstance(params, OnOffParams):
            return params.on
        if isinstance(params, TargetTemperatureParams):
            minimum = self.profile.target_temperature_min_c
            maximum = self.profile.target_temperature_max_c
            if not minimum <= params.celsius <= maximum:
                # The command service checks this too; a device-facing adapter
                # does not rely on someone else having done so.
                raise ProviderRejectedError("setpoint is outside the verified device range")
            decoded = self.profile.decode(base_payload)
            fahrenheit = bool(decoded.get(Datapoint.UNIT_IS_FAHRENHEIT, False))
            return from_celsius(params.celsius, fahrenheit=fahrenheit)
        raise ProviderRejectedError("unsupported parameters for this controller")

    # -- helpers -----------------------------------------------------------

    def _require_known_device(self, ref: ProviderDeviceRef) -> None:
        if ref.provider != PROVIDER_NAME or ref.provider_device_id != DEVICE_ID:
            raise ProviderUnavailableError("unknown controller reference")

    async def _read_payload(self) -> bytes:
        payload = await self._read_with_retry()
        try:
            self.profile.decode(payload)
        except ProfileError as exc:
            # The layout does not fit what the controller sent. Reporting a
            # decoded value now would be a guess.
            _logger.warning(
                "bestway.payload_mismatch",
                provider=PROVIDER_NAME,
                profile=self.profile.name,
                payload_length=len(payload),
            )
            raise ProviderUnavailableError(
                "the controller sent an unexpected status block"
            ) from exc

        self._last_payload = payload
        self._availability = Availability.ONLINE
        return payload

    async def _read_with_retry(self) -> bytes:
        """Read the status block, reconnecting once if the peer hung up.

        Controllers in the field close the connection after an exchange, so a
        failed read is expected rather than exceptional. Reading is idempotent,
        which is what makes retrying it safe; no write is ever retried.
        """
        attempts = 2
        for attempt in range(1, attempts + 1):
            try:
                if not self.client.is_connected:
                    await self.client.connect()
                return await self.client.read_status()
            except ControllerMisbehaved:
                # Not a transient hiccup: stop talking to this peer.
                await self.client.close()
                raise
            except ProviderUnavailableError:
                await self.client.close()
                if attempt == attempts:
                    raise
        raise ProviderUnavailableError("the controller could not be read")  # pragma: no cover

    def _snapshot(self, payload: bytes) -> ProviderState:
        return ProviderState(
            state=self._to_state(payload),
            availability=Availability.ONLINE,
            observed_at=self.clock.now(),
        )

    def _offline_snapshot(self) -> ProviderState:
        return ProviderState(
            state=DeviceState(),
            availability=Availability.OFFLINE,
            observed_at=self.clock.now(),
        )

    def _to_state(self, payload: bytes) -> DeviceState:
        decoded = self.profile.decode(payload)
        fahrenheit = bool(decoded.get(Datapoint.UNIT_IS_FAHRENHEIT, False))

        def temperature(datapoint: Datapoint) -> float | None:
            raw = decoded.get(datapoint)
            if raw is None:
                return None
            return to_celsius(float(raw), fahrenheit=fahrenheit)

        def flag(datapoint: Datapoint) -> bool | None:
            raw = decoded.get(datapoint)
            return None if raw is None else bool(raw)

        return DeviceState(
            current_temperature_c=temperature(Datapoint.CURRENT_TEMPERATURE),
            target_temperature_c=temperature(Datapoint.TARGET_TEMPERATURE),
            heater=flag(Datapoint.HEATER),
            filter_pump=flag(Datapoint.FILTER_PUMP),
            bubbles=flag(Datapoint.BUBBLES),
            control_panel_lock=flag(Datapoint.CONTROL_PANEL_LOCK),
        )
