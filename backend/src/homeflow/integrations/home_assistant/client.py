"""Transport for a Home Assistant instance.

Two official interfaces, used for what each is good at: REST for snapshots and
service calls, the WebSocket API for live state and for the registries that say
which room a thing is in.

Home Assistant sits on the household network and is configured by the operator,
not by a request, so its address is trusted static configuration rather than
user input (see docs/security/ssrf.md in spirit: nothing here accepts a URL from
a client). Everything it *sends back* is still treated as untrusted: responses
are size-capped, frames are size-capped, and payloads are validated before any
of it becomes a device.

The token is a household credential. It travels in a header, never in a URL,
and nothing in this file logs a payload.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import SecretStr, ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from homeflow.integrations.base.errors import ProviderRejectedError, ProviderUnavailableError
from homeflow.integrations.home_assistant.entities import HaEntity
from homeflow.log import get_logger

_logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 10.0

#: A household instance answers /api/states with a few hundred kilobytes. Ten
#: megabytes is far past anything legitimate and stops a broken or hostile peer
#: from feeding the gateway until it runs out of memory.
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_MAX_FRAME_BYTES = 4 * 1024 * 1024

_WS_OPEN_TIMEOUT = 10.0
_WS_PING_INTERVAL = 20.0


class ConfigurationError(ValueError):
    """The configured address cannot be used."""


def normalise_base_url(raw: str) -> str:
    """Check the configured address and strip it to scheme, host and path.

    Refuses anything that is not plain HTTP(S), and refuses credentials in the
    URL: a token belongs in a header, where it does not end up in a log line or
    a process listing.
    """
    parts = urlsplit(raw.strip())
    if parts.scheme not in ("http", "https"):
        raise ConfigurationError("the Home Assistant address must be http:// or https://")
    if not parts.hostname:
        raise ConfigurationError("the Home Assistant address has no host")
    if parts.username or parts.password:
        raise ConfigurationError("credentials do not belong in the Home Assistant address")
    if parts.query or parts.fragment:
        raise ConfigurationError("the Home Assistant address must be a plain base address")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


@dataclass(slots=True)
class HomeAssistantClient:
    base_url: str
    token: SecretStr
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    http: httpx.AsyncClient | None = None

    _owned: httpx.AsyncClient | None = field(default=None, init=False)
    _command_id: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.base_url = normalise_base_url(self.base_url)

    @property
    def websocket_url(self) -> str:
        parts = urlsplit(self.base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        return urlunsplit((scheme, parts.netloc, f"{parts.path}/api/websocket", "", ""))

    async def aclose(self) -> None:
        if self._owned is not None:
            await self._owned.aclose()
            self._owned = None

    # -- REST --------------------------------------------------------------

    async def states(self) -> list[HaEntity]:
        """Every entity Home Assistant currently holds."""
        payload = await self._get("/api/states")
        if not isinstance(payload, list):
            raise ProviderUnavailableError("Home Assistant sent an unexpected state list")
        return _parse_entities(payload)

    async def state(self, entity_id: str) -> HaEntity:
        payload = await self._get(f"/api/states/{entity_id}")
        entity = _parse_entity(payload)
        if entity is None:
            raise ProviderUnavailableError("Home Assistant sent an unexpected state")
        return entity

    async def call_service(
        self,
        domain: str,
        service: str,
        data: Mapping[str, Any],
    ) -> list[HaEntity]:
        """Invoke one service and return the entities it changed.

        Home Assistant answers with the states it touched, which is what lets a
        command be judged on what actually happened rather than on a 200.
        """
        payload = await self._post(f"/api/services/{domain}/{service}", data)
        if not isinstance(payload, list):
            return []
        return _parse_entities(payload)

    def _client(self) -> httpx.AsyncClient:
        if self.http is not None:
            return self.http
        if self._owned is None:
            self._owned = httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self.token.get_secret_value()}"},
                timeout=self.timeout_seconds,
                # A redirect would be a chance to send the token somewhere else.
                follow_redirects=False,
            )
        return self._owned

    async def _get(self, path: str) -> Any:
        return await self._request("GET", path, None)

    async def _post(self, path: str, body: Mapping[str, Any]) -> Any:
        return await self._request("POST", path, dict(body))

    async def _request(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        client = self._client()
        headers = {"Authorization": f"Bearer {self.token.get_secret_value()}"}
        try:
            response = await client.request(
                method,
                f"{self.base_url}{path}",
                json=body,
                headers=headers,
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            _logger.warning("home_assistant.unreachable", provider="home_assistant")
            raise ProviderUnavailableError("Home Assistant did not answer") from exc

        if response.status_code in (401, 403):
            # Worth naming precisely: it is the one failure an operator fixes by
            # issuing a new token rather than by looking at the network.
            _logger.error("home_assistant.credential_refused", provider="home_assistant")
            raise ProviderUnavailableError("Home Assistant refused the credential")
        if response.status_code == 400:
            raise ProviderRejectedError("Home Assistant refused the request")
        if response.status_code >= 400:
            _logger.warning(
                "home_assistant.error_status",
                provider="home_assistant",
                result_code=response.status_code,
            )
            raise ProviderUnavailableError("Home Assistant reported an error")

        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise ProviderUnavailableError("Home Assistant sent an implausibly large response")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderUnavailableError("Home Assistant sent malformed JSON") from exc

    # -- WebSocket ---------------------------------------------------------

    @contextlib.asynccontextmanager
    async def _socket(self) -> AsyncIterator[ClientConnection]:
        """An authenticated socket, closed on the way out however that happens."""
        try:
            async with connect(
                self.websocket_url,
                max_size=_MAX_FRAME_BYTES,
                open_timeout=_WS_OPEN_TIMEOUT,
                ping_interval=_WS_PING_INTERVAL,
            ) as socket:
                greeting = await _receive(socket)
                if greeting.get("type") != "auth_required":
                    raise ProviderUnavailableError("Home Assistant did not ask for authentication")

                await socket.send(
                    json.dumps({"type": "auth", "access_token": self.token.get_secret_value()})
                )
                reply = await _receive(socket)
                if reply.get("type") != "auth_ok":
                    _logger.error("home_assistant.credential_refused", provider="home_assistant")
                    raise ProviderUnavailableError("Home Assistant refused the credential")

                yield socket
        except (WebSocketException, OSError) as exc:
            raise ProviderUnavailableError("the Home Assistant socket failed") from exc

    def _next_id(self) -> int:
        self._command_id += 1
        return self._command_id

    async def _command(self, socket: ClientConnection, message: dict[str, Any]) -> Any:
        """Send one command and wait for the result that carries its id."""
        command_id = self._next_id()
        await socket.send(json.dumps(message | {"id": command_id}))
        while True:
            frame = await _receive(socket)
            if frame.get("id") != command_id or frame.get("type") != "result":
                continue
            if not frame.get("success"):
                raise ProviderUnavailableError("Home Assistant refused a request")
            return frame.get("result")

    async def rooms(self) -> dict[str, str]:
        """Map each entity to the name of the room it is in.

        Best effort. An operator who has not assigned areas, or a token that
        cannot read the registries, simply means devices arrive without a room
        rather than not arriving at all.
        """
        try:
            async with self._socket() as socket:
                areas = await self._command(socket, {"type": "config/area_registry/list"})
                devices = await self._command(socket, {"type": "config/device_registry/list"})
                entities = await self._command(socket, {"type": "config/entity_registry/list"})
        except ProviderUnavailableError:
            _logger.info("home_assistant.rooms_unavailable", provider="home_assistant")
            return {}

        names = {
            item["area_id"]: str(item["name"])[:80]
            for item in _rows(areas)
            if isinstance(item.get("area_id"), str) and item.get("name")
        }
        device_areas = {
            item["id"]: item["area_id"]
            for item in _rows(devices)
            if isinstance(item.get("id"), str) and isinstance(item.get("area_id"), str)
        }

        rooms: dict[str, str] = {}
        for item in _rows(entities):
            entity_id = item.get("entity_id")
            if not isinstance(entity_id, str):
                continue
            # An entity may sit in a room directly, or inherit the room of the
            # physical device it belongs to.
            area = item.get("area_id")
            if not isinstance(area, str):
                area = device_areas.get(item.get("device_id"))  # type: ignore[arg-type]
            name = names.get(area) if isinstance(area, str) else None
            if name:
                rooms[entity_id] = name
        return rooms

    async def events(self) -> AsyncIterator[HaEntity]:
        """Yield every entity whose state changed, until the caller stops."""
        async with self._socket() as socket:
            await self._command(socket, {"type": "subscribe_events", "event_type": "state_changed"})
            while True:
                frame = await _receive(socket)
                if frame.get("type") != "event":
                    continue
                event = frame.get("event")
                if not isinstance(event, dict):
                    continue
                data = event.get("data")
                if not isinstance(data, dict):
                    continue
                entity = _parse_entity(data.get("new_state"))
                if entity is not None:
                    yield entity


async def _receive(socket: ClientConnection) -> dict[str, Any]:
    raw = await socket.recv()
    if isinstance(raw, bytes):
        raise ProviderUnavailableError("Home Assistant sent a binary frame")
    try:
        frame = json.loads(raw)
    except ValueError as exc:
        raise ProviderUnavailableError("Home Assistant sent a frame that is not JSON") from exc
    if not isinstance(frame, dict):
        raise ProviderUnavailableError("Home Assistant sent an unexpected frame")
    return frame


def _rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _parse_entity(payload: Any) -> HaEntity | None:
    if not isinstance(payload, dict):
        return None
    try:
        return HaEntity.model_validate(payload)
    except ValidationError:
        # An entity we cannot make sense of is skipped, not fatal: one odd
        # integration must not cost the household every other device.
        return None


def _parse_entities(payload: list[Any]) -> list[HaEntity]:
    parsed = [entity for entity in (_parse_entity(item) for item in payload) if entity is not None]
    if payload and not parsed:
        raise ProviderUnavailableError("Home Assistant sent no usable states")
    return parsed
