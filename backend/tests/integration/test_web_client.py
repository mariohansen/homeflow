"""The gateway also serves the web client, on the same origin (ADR 0011)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _ticket(client: TestClient, auth: dict[str, str]) -> str:
    response = client.post("/v1/auth/ws-ticket", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["expiresInSeconds"] <= 60
    return body["ticket"]


def test_ticket_endpoint_requires_a_credential(client: TestClient) -> None:
    response = client.post("/v1/auth/ws-ticket")
    assert response.status_code == 401
    assert response.json()["type"] == "unauthenticated"


def test_ticket_opens_a_socket_and_cannot_be_reused(
    client: TestClient, auth: dict[str, str]
) -> None:
    ticket = _ticket(client, auth)
    protocols = ["homeflow.v1", f"homeflow.ticket.{ticket}"]

    with client.websocket_connect("/v1/ws", subprotocols=protocols) as socket:
        hello = socket.receive_json()
        assert hello["type"] == "Hello"

    accepted_again = False
    try:
        with client.websocket_connect("/v1/ws", subprotocols=protocols):
            accepted_again = True
    except Exception:
        accepted_again = False
    assert accepted_again is False


def test_an_invented_ticket_is_rejected(client: TestClient) -> None:
    accepted = False
    try:
        with client.websocket_connect(
            "/v1/ws", subprotocols=["homeflow.v1", "homeflow.ticket.made-up"]
        ):
            accepted = True
    except Exception:
        accepted = False
    assert accepted is False


def test_state_reaches_a_ticket_authenticated_socket(
    client: TestClient, app: FastAPI, auth: dict[str, str]
) -> None:
    devices = client.get("/v1/devices", headers=auth).json()
    light = next(device for device in devices if device["kind"] == "LIGHT")

    # A request issued from inside the socket context would share the test
    # client's portal; a second client over the same app keeps them separate.
    commander = TestClient(app)

    ticket = _ticket(client, auth)
    with client.websocket_connect(
        "/v1/ws", subprotocols=["homeflow.v1", f"homeflow.ticket.{ticket}"]
    ) as socket:
        assert socket.receive_json()["type"] == "Hello"

        commander.post(
            f"/v1/devices/{light['id']}/commands",
            headers=auth,
            json={"action": "SET_BRIGHTNESS", "parameters": {"brightness": 15}},
        )

        seen: list[dict[str, Any]] = []
        for _ in range(6):
            frame = socket.receive_json()
            seen.append(frame)
            if frame["type"] == "DeviceStateChanged":
                break

        states = [frame for frame in seen if frame["type"] == "DeviceStateChanged"]
        assert states, f"no state event, saw {[frame['type'] for frame in seen]}"
        assert states[-1]["device"]["state"]["brightness"] == 15


def test_client_shell_is_served_from_the_same_origin(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "HomeFlow" in response.text


def test_responses_carry_a_strict_content_security_policy(client: TestClient) -> None:
    policy = client.get("/").headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert "'unsafe-inline'" not in policy
    assert "'unsafe-eval'" not in policy
    assert "frame-ancestors 'none'" in policy
    # Only same-origin resources are permitted.
    for directive in ("script-src 'self'", "style-src 'self'", "connect-src 'self'"):
        assert directive in policy


def test_household_state_is_never_cached(client: TestClient, auth: dict[str, str]) -> None:
    api = client.get("/v1/devices", headers=auth)
    assert api.headers["cache-control"] == "no-store"
    # The shell may be cached but must be revalidated.
    assert client.get("/").headers["cache-control"] == "no-cache"


def test_the_client_bundle_contains_no_household_data(client: TestClient) -> None:
    """The shell is public material: it must be as synthetic as the repository."""
    shell = client.get("/").text
    for asset in ("/app.css", "/js/app.js", "/js/render.js", "/js/api.js", "/js/live.js"):
        shell += client.get(asset).text
    lowered = shell.lower()
    for forbidden in ("192.168.", "10.0.", "tskey-", "eyj", "@gmail", "@haw-"):
        assert forbidden not in lowered
    assert "terrace-pool" not in lowered
