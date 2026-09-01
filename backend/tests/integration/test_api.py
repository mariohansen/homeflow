"""End-to-end slice: REST snapshot, command pipeline and live WebSocket update."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

PROVIDER_DEVICE_IDS = (
    "terrace-pool",
    "living-room-ceiling-light",
    "living-room-speaker",
    "hallway-lock",
    "utility-washer",
    "utility-dishwasher",
)


def _devices(client: TestClient, auth: dict[str, str]) -> list[dict[str, Any]]:
    response = client.get("/v1/devices", headers=auth)
    assert response.status_code == 200
    return response.json()


def _pool(client: TestClient, auth: dict[str, str]) -> dict[str, Any]:
    return next(device for device in _devices(client, auth) if device["kind"] == "POOL")


def test_healthz_is_public(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_private_network_membership_is_not_authorisation(client: TestClient) -> None:
    for path in ("/v1/me", "/v1/devices", "/v1/rooms", "/v1/activity"):
        response = client.get(path)
        assert response.status_code == 401
        assert response.json()["type"] == "unauthenticated"


def test_invalid_credential_is_rejected(client: TestClient) -> None:
    response = client.get("/v1/devices", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_me_reports_demo_mode(client: TestClient, auth: dict[str, str]) -> None:
    body = client.get("/v1/me", headers=auth).json()
    assert body["demoMode"] is True
    assert body["displayName"] == "Development client"


def test_devices_expose_capabilities_and_freshness(
    client: TestClient, auth: dict[str, str]
) -> None:
    pool = _pool(client, auth)
    assert set(pool["capabilities"]) >= {"CURRENT_TEMPERATURE", "TARGET_TEMPERATURE", "HEATING"}
    assert pool["constraints"]["targetTemperatureMinC"] == 20.0
    assert pool["isStale"] is False
    assert pool["state"]["currentTemperatureC"] is not None


def test_offline_device_is_marked_stale(client: TestClient, auth: dict[str, str]) -> None:
    dishwasher = next(
        device for device in _devices(client, auth) if device["displayName"] == "Demo Dishwasher"
    )
    assert dishwasher["availability"] == "OFFLINE"
    assert dishwasher["isStale"] is True


def test_rooms_are_derived_from_devices(client: TestClient, auth: dict[str, str]) -> None:
    names = {room["name"] for room in client.get("/v1/rooms", headers=auth).json()}
    assert {"Living Room", "Hallway", "Terrace", "Utility Room"} <= names


def test_no_provider_identifier_reaches_a_client(client: TestClient, auth: dict[str, str]) -> None:
    """The public surface must never carry a provider entity id."""
    payload = json.dumps(_devices(client, auth))
    for provider_device_id in PROVIDER_DEVICE_IDS:
        assert provider_device_id not in payload
    assert "provider" not in payload


def test_command_round_trip_updates_state(client: TestClient, auth: dict[str, str]) -> None:
    pool = _pool(client, auth)
    response = client.post(
        f"/v1/devices/{pool['id']}/commands",
        headers=auth,
        json={"action": "SET_TARGET_TEMPERATURE", "parameters": {"celsius": 38.0}},
    )
    assert response.status_code == 200
    command = response.json()
    assert command["status"] == "SUCCEEDED"
    assert command["riskClass"] == "MEDIUM"

    assert _pool(client, auth)["state"]["targetTemperatureC"] == 38.0

    stored = client.get(f"/v1/commands/{command['id']}", headers=auth)
    assert stored.status_code == 200
    assert stored.json()["status"] == "SUCCEEDED"


def test_setpoint_outside_device_limits_returns_problem_details(
    client: TestClient, auth: dict[str, str]
) -> None:
    pool = _pool(client, auth)
    response = client.post(
        f"/v1/devices/{pool['id']}/commands",
        headers=auth,
        json={"action": "SET_TARGET_TEMPERATURE", "parameters": {"celsius": 55.0}},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["type"] == "parameter_out_of_range"
    assert body["correlationId"]
    assert "Traceback" not in json.dumps(body)


def test_unlocking_the_demo_lock_is_refused(client: TestClient, auth: dict[str, str]) -> None:
    lock = next(device for device in _devices(client, auth) if device["displayName"] == "Demo Lock")
    response = client.post(
        f"/v1/devices/{lock['id']}/commands",
        headers=auth,
        json={"action": "SET_LOCK_STATE", "parameters": {"desired": "UNLOCKED"}},
    )
    assert response.status_code == 403
    assert response.json()["type"] == "action_authorization_required"


def test_command_to_offline_device_is_refused(client: TestClient, auth: dict[str, str]) -> None:
    dishwasher = next(
        device for device in _devices(client, auth) if device["displayName"] == "Demo Dishwasher"
    )
    response = client.post(
        f"/v1/devices/{dishwasher['id']}/commands",
        headers=auth,
        json={"action": "SET_POWER", "parameters": {"on": True}},
    )
    # The dishwasher has no POWER capability either; both refusals are correct
    # and neither reaches the adapter.
    assert response.status_code in (409, 422)


def test_unknown_device_returns_a_problem_document(
    client: TestClient, auth: dict[str, str]
) -> None:
    response = client.get("/v1/devices/00000000-0000-4000-8000-000000000000", headers=auth)
    assert response.status_code == 404
    assert response.json()["type"] == "device_not_found"


def test_activity_records_the_command(client: TestClient, auth: dict[str, str]) -> None:
    pool = _pool(client, auth)
    client.post(
        f"/v1/devices/{pool['id']}/commands",
        headers=auth,
        json={"action": "SET_HEATER", "parameters": {"on": True}},
    )
    entries = client.get("/v1/activity", headers=auth).json()
    events = {entry["event"] for entry in entries}
    assert {"command.requested", "command.completed"} <= events
    assert all("token" not in json.dumps(entry) for entry in entries)


def test_websocket_requires_authentication(client: TestClient) -> None:
    accepted = False
    try:
        with client.websocket_connect("/v1/ws"):
            accepted = True
    except Exception:
        accepted = False
    assert accepted is False


def test_websocket_streams_state_changes(
    client: TestClient, app: FastAPI, auth: dict[str, str]
) -> None:
    pool = _pool(client, auth)
    # See test_web_client: the command needs a portal of its own.
    commander = TestClient(app)

    with client.websocket_connect("/v1/ws", headers=auth) as socket:
        hello = socket.receive_json()
        assert hello["type"] == "Hello"
        assert hello["demoMode"] is True

        commander.post(
            f"/v1/devices/{pool['id']}/commands",
            headers=auth,
            json={"action": "SET_BUBBLES", "parameters": {"on": True}},
        )

        seen: list[dict[str, Any]] = []
        for _ in range(6):
            frame = socket.receive_json()
            seen.append(frame)
            if frame["type"] == "DeviceStateChanged":
                break

        state_frames = [frame for frame in seen if frame["type"] == "DeviceStateChanged"]
        assert state_frames, f"no state event received, saw {[f['type'] for f in seen]}"
        device = state_frames[-1]["device"]
        assert device["id"] == pool["id"]
        assert device["state"]["bubbles"] is True
        assert "provider" not in json.dumps(state_frames[-1])


def test_a_client_that_reconnects_often_is_not_locked_out(
    client: TestClient, auth: dict[str, str]
) -> None:
    """The auth budget exists to blunt guessing, not to punish a reload.

    A page load spends several authenticated requests, so counting successes
    against the same budget locked a household out of its own home.
    """
    for _ in range(40):
        assert client.get("/v1/devices", headers=auth).status_code == 200
        assert client.post("/v1/auth/ws-ticket", headers=auth).status_code == 200


def test_repeated_failures_are_throttled(client: TestClient) -> None:
    wrong = {"Authorization": "Bearer not-the-token"}
    statuses = {client.get("/v1/devices", headers=wrong).status_code for _ in range(40)}
    assert 401 in statuses, "the first attempts are simply refused"
    assert 429 in statuses, "persistent guessing is throttled"
