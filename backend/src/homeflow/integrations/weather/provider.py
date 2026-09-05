"""Outdoor temperature from a public forecast service.

The pool card is more useful next to what it is competing with: heating to 33
degrees means something different at 5 outside than at 24. This adapter fetches
one number and normalises it into an ordinary sensor device, so nothing above it
knows a weather service exists.

Two things this deliberately does not do. It sends no household data: a
latitude and longitude the operator chose, and nothing else — no device state,
no identifiers. And it treats the service as an untrusted, rate-limited peer:
one request every quarter of an hour by default, a hard timeout, a validated
response, and stale readings marked stale rather than hidden.

The coordinates describe where someone lives. They belong in the untracked
configuration, never in this repository.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from homeflow.capabilities import Capability, DeviceKind
from homeflow.clock import Clock, SystemClock
from homeflow.devices.models import Availability, DeviceState
from homeflow.integrations.base.errors import ProviderRejectedError, ProviderUnavailableError
from homeflow.integrations.base.models import (
    ProviderCommand,
    ProviderCommandResult,
    ProviderDevice,
    ProviderDeviceRef,
    ProviderEvent,
    ProviderState,
)
from homeflow.log import get_logger

_logger = get_logger(__name__)

PROVIDER_NAME = "weather"
DEVICE_ID = "outdoor"

#: A forecast service has nothing to gain from being asked more often, and a
#: quota is easier to keep than to recover from.
DEFAULT_POLL_SECONDS = 900.0
DEFAULT_TIMEOUT_SECONDS = 10.0
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

#: Anything outside this is not a temperature in a place people live.
_PLAUSIBLE_RANGE_C = (-90.0, 60.0)

_INITIAL_BACKOFF_SECONDS = 60.0
_MAX_BACKOFF_SECONDS = 3600.0


class _CurrentReading(BaseModel):
    model_config = ConfigDict(extra="ignore")

    temperature_2m: float = Field(ge=_PLAUSIBLE_RANGE_C[0], le=_PLAUSIBLE_RANGE_C[1])


class _Forecast(BaseModel):
    model_config = ConfigDict(extra="ignore")

    current: _CurrentReading


def outdoor_ref() -> ProviderDeviceRef:
    return ProviderDeviceRef(provider=PROVIDER_NAME, provider_device_id=DEVICE_ID)


@dataclass(slots=True)
class OpenMeteoProvider:
    """One outdoor temperature, refreshed on a slow schedule."""

    latitude: float
    longitude: float
    client: httpx.AsyncClient | None = None
    clock: Clock = field(default_factory=SystemClock)
    display_name: str = "Outdoor"
    room_hint: str | None = None
    poll_seconds: float = DEFAULT_POLL_SECONDS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    _owned_client: httpx.AsyncClient | None = field(default=None, init=False)

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    async def aclose(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None

    async def discover_devices(self) -> Sequence[ProviderDevice]:
        return [
            ProviderDevice(
                ref=outdoor_ref(),
                suggested_name=self.display_name,
                kind=DeviceKind.SENSOR,
                capabilities=(Capability.CURRENT_TEMPERATURE,),
                room_hint=self.room_hint,
            )
        ]

    async def get_state(self, device_ref: ProviderDeviceRef) -> ProviderState:
        if device_ref != outdoor_ref():
            raise ProviderUnavailableError("unknown weather reference")
        celsius = await self._fetch()
        return ProviderState(
            state=DeviceState(current_temperature_c=celsius),
            availability=Availability.ONLINE,
            observed_at=self.clock.now(),
        )

    async def execute(
        self,
        device_ref: ProviderDeviceRef,
        command: ProviderCommand,
    ) -> ProviderCommandResult:
        # A thermometer is not a control surface.
        raise ProviderRejectedError("the outdoor temperature cannot be commanded")

    async def subscribe(self) -> AsyncGenerator[ProviderEvent]:
        """Refresh slowly, and back off further when the service is unhappy."""
        backoff = _INITIAL_BACKOFF_SECONDS
        while True:
            await asyncio.sleep(self.poll_seconds)
            try:
                celsius = await self._fetch()
            except ProviderUnavailableError:
                # A forecast is not worth hammering for; wait longer each time.
                jitter = random.random() * 30.0  # noqa: S311 - spread, not security
                await asyncio.sleep(min(backoff + jitter, _MAX_BACKOFF_SECONDS))
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
                continue

            backoff = _INITIAL_BACKOFF_SECONDS
            yield ProviderEvent(
                ref=outdoor_ref(),
                state=ProviderState(
                    state=DeviceState(current_temperature_c=celsius),
                    availability=Availability.ONLINE,
                    observed_at=self.clock.now(),
                ),
            )

    async def _fetch(self) -> float:
        client = self.client or self._ensure_client()
        params = {
            "latitude": f"{self.latitude:.4f}",
            "longitude": f"{self.longitude:.4f}",
            "current": "temperature_2m",
        }
        try:
            response = await client.get(OPEN_METEO_URL, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # The service's own words are not repeated: they would end up in a
            # problem document, and they are not ours to vouch for.
            _logger.warning("weather.unavailable", provider=PROVIDER_NAME)
            raise ProviderUnavailableError("the weather service did not answer") from exc

        try:
            forecast = _Forecast.model_validate(payload)
        except ValidationError as exc:
            _logger.warning("weather.unexpected_payload", provider=PROVIDER_NAME)
            raise ProviderUnavailableError("the weather service sent something unexpected") from exc

        return round(forecast.current.temperature_2m, 1)

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(follow_redirects=False)
        return self._owned_client
