"""A stand-in Home Assistant, good enough to be wrong in the same ways.

CI must never reach the household (see docs/security/privacy-model.md), so the
adapter is exercised against this instead. It speaks both interfaces the real
thing does: REST through an httpx transport, and a genuine WebSocket server on
loopback -- genuine because the authentication handshake is the part most worth
testing, and a mocked socket would test the mock.

Every entity here is invented. Fictional rooms, fictional names.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator, Iterable
from typing import Any

import httpx
from websockets.asyncio.server import ServerConnection, serve

TOKEN = "test-home-assistant-token"

#: A small household: two lights, a speaker, a door, a thermostat, and the noise
#: a real instance is full of.
ENTITIES: list[dict[str, Any]] = [
    {
        "entity_id": "light.living_room_ceiling",
        "state": "on",
        "attributes": {
            "friendly_name": "Ceiling Light",
            "brightness": 128,
            "supported_color_modes": ["color_temp", "hs"],
        },
    },
    {
        "entity_id": "light.hallway_spot",
        "state": "off",
        "attributes": {
            "friendly_name": "Hallway Spot",
            "supported_color_modes": ["onoff"],
        },
    },
    {
        "entity_id": "switch.terrace_socket",
        "state": "off",
        "attributes": {"friendly_name": "Terrace Socket"},
    },
    {
        "entity_id": "media_player.living_room_speaker",
        "state": "playing",
        "attributes": {
            "friendly_name": "Speaker",
            "volume_level": 0.35,
            "media_title": "Some Track",
            # PLAY | PAUSE | VOLUME_SET | NEXT_TRACK | PREVIOUS_TRACK
            "supported_features": 16384 | 1 | 4 | 32 | 16,
        },
    },
    {
        "entity_id": "lock.front_door",
        "state": "locked",
        "attributes": {"friendly_name": "Front Door"},
    },
    {
        "entity_id": "climate.living_room",
        "state": "heat",
        "attributes": {
            "friendly_name": "Living Room Radiator",
            "current_temperature": 20.5,
            "temperature": 21.5,
            "min_temp": 5.0,
            "max_temp": 30.0,
            "target_temp_step": 0.5,
        },
    },
    {
        "entity_id": "sensor.terrace_temperature",
        "state": "13.4",
        "attributes": {
            "friendly_name": "Terrace Temperature",
            "device_class": "temperature",
            "unit_of_measurement": "°C",
        },
    },
    # The noise: a household instance is mostly this, and none of it belongs on
    # a home screen.
    {
        "entity_id": "sensor.speaker_battery",
        "state": "88",
        "attributes": {"friendly_name": "Speaker Battery", "device_class": "battery"},
    },
    {
        "entity_id": "update.home_assistant_core",
        "state": "off",
        "attributes": {"friendly_name": "Core Update"},
    },
    {
        "entity_id": "automation.wake_up",
        "state": "on",
        "attributes": {"friendly_name": "Wake Up"},
    },
]

AREAS = [
    {"area_id": "living_room", "name": "Living Room"},
    {"area_id": "hallway", "name": "Hallway"},
    {"area_id": "terrace", "name": "Terrace"},
]

DEVICES = [{"id": "dev-speaker", "area_id": "living_room"}]

ENTITY_REGISTRY = [
    {"entity_id": "light.living_room_ceiling", "area_id": "living_room"},
    {"entity_id": "light.hallway_spot", "area_id": "hallway"},
    {"entity_id": "switch.terrace_socket", "area_id": "terrace"},
    # This one inherits its room from the physical device it belongs to.
    {"entity_id": "media_player.living_room_speaker", "device_id": "dev-speaker"},
    {"entity_id": "climate.living_room", "area_id": "living_room"},
]


class FakeHomeAssistant:
    """Holds entity state and answers both interfaces."""

    def __init__(
        self,
        entities: Iterable[dict[str, Any]] | None = None,
        *,
        token: str = TOKEN,
        registries: bool = True,
    ) -> None:
        self.token = token
        self.registries = registries
        self.entities = {
            item["entity_id"]: json.loads(json.dumps(item))
            for item in (entities if entities is not None else ENTITIES)
        }
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        #: Set to stop a service call from changing anything, which is how a
        #: real integration that silently fails behaves.
        self.apply_calls = True
        self.reject_service = False
        self._events: list[dict[str, Any]] = []

    # -- REST ---------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"message": "Unauthorized"})

        path = request.url.path
        if path == "/api/states":
            return httpx.Response(200, json=list(self.entities.values()))
        if path.startswith("/api/states/"):
            entity = self.entities.get(path.removeprefix("/api/states/"))
            if entity is None:
                return httpx.Response(404, json={"message": "Entity not found."})
            return httpx.Response(200, json=entity)
        if path.startswith("/api/services/"):
            return self._service(request, path.removeprefix("/api/services/"))
        return httpx.Response(404, json={"message": "Not found"})

    def _service(self, request: httpx.Request, target: str) -> httpx.Response:
        if self.reject_service:
            return httpx.Response(400, json={"message": "Bad request"})
        domain, _, service = target.partition("/")
        body = json.loads(request.content or b"{}")
        entity_id = body.get("entity_id", "")
        self.calls.append((domain, service, body))

        entity = self.entities.get(entity_id)
        if entity is None:
            return httpx.Response(200, json=[])
        if self.apply_calls:
            self._apply(entity, service, body)
        return httpx.Response(200, json=[entity])

    def _apply(self, entity: dict[str, Any], service: str, body: dict[str, Any]) -> None:
        attributes = entity["attributes"]
        if service == "turn_on":
            entity["state"] = "on"
            if "brightness_pct" in body:
                attributes["brightness"] = round(body["brightness_pct"] * 255 / 100)
        elif service == "turn_off":
            entity["state"] = "off"
        elif service == "volume_set":
            attributes["volume_level"] = body["volume_level"]
        elif service == "media_play":
            entity["state"] = "playing"
        elif service == "media_pause":
            entity["state"] = "paused"
        elif service == "set_temperature":
            attributes["temperature"] = body["temperature"]

    # -- WebSocket ----------------------------------------------------------

    @contextlib.asynccontextmanager
    async def running(self, events: list[dict[str, Any]] | None = None) -> AsyncIterator[str]:
        """Serve the WebSocket API on loopback; yields the base address."""
        self._events = events or []
        async with serve(self._socket, "127.0.0.1", 0) as server:
            port = next(iter(server.sockets)).getsockname()[1]
            yield f"http://127.0.0.1:{port}"

    async def _socket(self, connection: ServerConnection) -> None:
        await connection.send(json.dumps({"type": "auth_required", "ha_version": "2026.9.0"}))
        auth = json.loads(await connection.recv())
        if auth.get("access_token") != self.token:
            await connection.send(json.dumps({"type": "auth_invalid"}))
            return
        await connection.send(json.dumps({"type": "auth_ok", "ha_version": "2026.9.0"}))

        while True:
            try:
                message = json.loads(await connection.recv())
            except Exception:
                return
            await self._command(connection, message)

    async def _command(self, connection: ServerConnection, message: dict[str, Any]) -> None:
        command_id = message.get("id")
        kind = message.get("type")

        registry = {
            "config/area_registry/list": AREAS,
            "config/device_registry/list": DEVICES,
            "config/entity_registry/list": ENTITY_REGISTRY,
        }
        if kind in registry:
            if not self.registries:
                await connection.send(
                    json.dumps({"id": command_id, "type": "result", "success": False})
                )
                return
            await connection.send(
                json.dumps(
                    {"id": command_id, "type": "result", "success": True, "result": registry[kind]}
                )
            )
            return

        if kind == "subscribe_events":
            await connection.send(
                json.dumps({"id": command_id, "type": "result", "success": True, "result": None})
            )
            for state in self._events:
                await connection.send(
                    json.dumps(
                        {
                            "id": command_id,
                            "type": "event",
                            "event": {
                                "event_type": "state_changed",
                                "data": {
                                    "entity_id": state["entity_id"],
                                    "new_state": state,
                                    "old_state": None,
                                },
                            },
                        }
                    )
                )
            return

        await connection.send(json.dumps({"id": command_id, "type": "result", "success": False}))
