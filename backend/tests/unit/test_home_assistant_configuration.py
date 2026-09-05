"""Configuration for the Home Assistant adapter fails closed.

A misconfiguration must stop the gateway at startup, where an operator sees it,
rather than at the moment somebody taps a control.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from conftest import make_settings
from homeflow.config.settings import Environment
from homeflow.integrations.home_assistant.client import (
    ConfigurationError,
    HomeAssistantClient,
    normalise_base_url,
)

URL = "http://home-assistant.example.internal:8123"
TOKEN = SecretStr("example-token")


def settings(**overrides: object):
    base: dict[str, object] = {
        "env": Environment.TEST,
        "demo_mode": False,
        # Required outside demo mode; not what these tests are about.
        "id_salt": SecretStr("0" * 64),
        "home_assistant_enabled": True,
        "home_assistant_base_url": URL,
        "home_assistant_token": TOKEN,
    }
    return make_settings(**(base | overrides))


# -- fail closed -----------------------------------------------------------


def test_enabling_the_adapter_without_an_address_is_refused() -> None:
    with pytest.raises(ValueError, match="BASE_URL"):
        settings(home_assistant_base_url=None)


def test_enabling_the_adapter_without_a_token_is_refused() -> None:
    with pytest.raises(ValueError, match="TOKEN"):
        settings(home_assistant_token=None)


def test_the_door_cannot_be_released_by_configuration() -> None:
    """Writing it down is refused, not quietly ignored: the operator believed it."""
    with pytest.raises(ValueError, match="lock"):
        settings(home_assistant_write_enabled="light,lock")


def test_an_unknown_domain_is_refused() -> None:
    with pytest.raises(ValueError, match="vacuum"):
        settings(home_assistant_write_enabled="vacuum")


def test_the_adapter_cannot_run_alongside_demo_mode() -> None:
    """A demo build must be structurally unable to reach real devices."""
    with pytest.raises(ValueError, match="Demo mode"):
        settings(demo_mode=True)


def test_released_domains_are_read_as_a_comma_separated_list() -> None:
    parsed = settings(home_assistant_write_enabled="light, switch ,climate")
    assert parsed.home_assistant_write_enabled == ("light", "switch", "climate")


def test_read_only_is_the_default() -> None:
    assert settings().home_assistant_write_enabled == ()
    assert make_settings().home_assistant_enabled is False


# -- the address -----------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "ftp://home-assistant.example.internal",
        "file:///etc/passwd",
        "not a url",
        "//home-assistant.example.internal",
    ],
)
def test_only_plain_http_addresses_are_accepted(address: str) -> None:
    with pytest.raises(ConfigurationError):
        normalise_base_url(address)


def test_credentials_do_not_belong_in_the_address() -> None:
    """A token in a URL ends up in logs and process listings."""
    with pytest.raises(ConfigurationError, match="credentials"):
        normalise_base_url("http://user:secret@home-assistant.example.internal:8123")


def test_a_query_string_is_refused() -> None:
    with pytest.raises(ConfigurationError):
        normalise_base_url("http://home-assistant.example.internal:8123/?token=abc")


def test_a_trailing_slash_is_harmless() -> None:
    assert normalise_base_url(f"  {URL}/  ") == URL


def test_the_socket_address_follows_the_base_address() -> None:
    plain = HomeAssistantClient(base_url=URL, token=TOKEN)
    assert plain.websocket_url == "ws://home-assistant.example.internal:8123/api/websocket"

    secure = HomeAssistantClient(base_url="https://ha.example.internal", token=TOKEN)
    assert secure.websocket_url == "wss://ha.example.internal/api/websocket"


def test_a_sub_path_installation_is_kept() -> None:
    client = HomeAssistantClient(base_url="http://proxy.example.internal/ha/", token=TOKEN)
    assert client.base_url == "http://proxy.example.internal/ha"
    assert client.websocket_url == "ws://proxy.example.internal/ha/api/websocket"
