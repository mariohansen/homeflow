"""The outdoor temperature adapter treats a public service as an untrusted peer."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from conftest import run
from homeflow.capabilities import Capability, DeviceKind
from homeflow.clock import ManualClock
from homeflow.commands.models import Action, OnOffParams
from homeflow.integrations.base.errors import ProviderRejectedError, ProviderUnavailableError
from homeflow.integrations.base.models import ProviderCommand, ProviderDeviceRef
from homeflow.integrations.weather.provider import OpenMeteoProvider, outdoor_ref

Handler = Callable[[httpx.Request], httpx.Response]

#: Coordinates for a well-known public square, not a household.
BERLIN = (52.5200, 13.4050)


def provider(handler: Handler) -> OpenMeteoProvider:
    return OpenMeteoProvider(
        latitude=BERLIN[0],
        longitude=BERLIN[1],
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        clock=ManualClock(),
    )


class Service:
    """A stand-in forecast service that remembers what it was asked."""

    def __init__(self, payload: object, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.request: httpx.Request | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.request = request
        return httpx.Response(self.status, json=self.payload)


def test_it_reports_one_sensor() -> None:
    async def scenario() -> tuple[DeviceKind, tuple[Capability, ...]]:
        pool = provider(Service({"current": {"temperature_2m": 12.3}}))
        device = (await pool.discover_devices())[0]
        return device.kind, device.capabilities

    kind, capabilities = run(scenario())
    assert kind is DeviceKind.SENSOR
    assert capabilities == (Capability.CURRENT_TEMPERATURE,)


def test_a_reading_becomes_canonical_state() -> None:
    async def scenario() -> float | None:
        pool = provider(Service({"current": {"temperature_2m": 12.34}}))
        return (await pool.get_state(outdoor_ref())).state.current_temperature_c

    assert run(scenario()) == 12.3


def test_only_coordinates_are_sent() -> None:
    """The request must carry no household data beyond where to look."""
    service = Service({"current": {"temperature_2m": 4.0}})

    async def scenario() -> httpx.Request:
        await provider(service).get_state(outdoor_ref())
        assert service.request is not None
        return service.request

    request = run(scenario())
    assert set(request.url.params.keys()) == {"latitude", "longitude", "current"}
    assert request.url.host == "api.open-meteo.com"
    assert not request.content


def test_a_failing_service_is_unavailable_not_fatal() -> None:
    async def scenario() -> None:
        pool = provider(Service({"error": True}, status=503))
        with pytest.raises(ProviderUnavailableError):
            await pool.get_state(outdoor_ref())

    run(scenario())


def test_an_unexpected_payload_is_refused() -> None:
    async def scenario() -> None:
        pool = provider(Service({"current": {}}))
        with pytest.raises(ProviderUnavailableError, match="unexpected"):
            await pool.get_state(outdoor_ref())

    run(scenario())


def test_an_implausible_temperature_is_refused() -> None:
    """A number outside anything habitable is a broken response, not weather."""

    async def scenario() -> None:
        pool = provider(Service({"current": {"temperature_2m": 900.0}}))
        with pytest.raises(ProviderUnavailableError, match="unexpected"):
            await pool.get_state(outdoor_ref())

    run(scenario())


def test_a_thermometer_cannot_be_commanded() -> None:
    async def scenario() -> None:
        pool = provider(Service({"current": {"temperature_2m": 4.0}}))
        command = ProviderCommand(action=Action.SET_POWER, params=OnOffParams(on=True))
        with pytest.raises(ProviderRejectedError):
            await pool.execute(outdoor_ref(), command)

    run(scenario())


def test_an_unknown_reference_is_refused() -> None:
    async def scenario() -> None:
        pool = provider(Service({"current": {"temperature_2m": 4.0}}))
        elsewhere = ProviderDeviceRef(provider="weather", provider_device_id="mars")
        with pytest.raises(ProviderUnavailableError):
            await pool.get_state(elsewhere)

    run(scenario())
