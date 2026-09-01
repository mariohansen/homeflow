"""Redaction is a privacy guard, so it is tested like one."""

from __future__ import annotations

import json
import logging

from homeflow.log import REDACTED, JsonFormatter, get_logger, pseudonymize, redact


def test_sensitive_keys_are_removed() -> None:
    payload = {
        "authorization": "Bearer abc.def.ghi",
        "home_assistant_token": "secret-value",
        "provider_device_id": "light.hallway_ceiling",
        "email": "someone@example.org",
        "command_id": "1234",
    }
    result = redact(payload)
    assert result["authorization"] == REDACTED
    assert result["home_assistant_token"] == REDACTED
    assert result["provider_device_id"] == REDACTED
    assert result["email"] == REDACTED
    # Non-sensitive operational fields survive.
    assert result["command_id"] == "1234"


def test_patterns_are_scrubbed_from_free_text() -> None:
    text = (
        "connect 192.0.2.10 mac 02:00:00:00:00:01 token "
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abcdefghijk key tskey-auth-abcdef123456"
    )
    scrubbed = redact({"message": text})["message"]
    assert "192.0.2.10" not in scrubbed
    assert "02:00:00:00:00:01" not in scrubbed
    assert "eyJhbGciOiJIUzI1NiIs" not in scrubbed
    assert "tskey-auth-abcdef123456" not in scrubbed


def test_nested_structures_are_redacted() -> None:
    payload = {"outer": {"token": "abc", "items": [{"password": "p"}, "203.0.113.30"]}}
    result = redact(payload)
    assert result["outer"]["token"] == REDACTED
    assert result["outer"]["items"][0]["password"] == REDACTED
    assert "203.0.113.30" not in result["outer"]["items"][1]


def test_structured_logger_never_emits_raw_secrets(caplog) -> None:
    formatter = JsonFormatter()
    logger = get_logger("test.redaction")
    with caplog.at_level(logging.INFO):
        logger.info("provider.call", token="super-secret", provider="demo")
    record = caplog.records[-1]
    emitted = json.loads(formatter.format(record))
    assert emitted["event"] == "provider.call"
    assert emitted["provider"] == "demo"
    assert emitted["token"] == REDACTED
    assert "super-secret" not in json.dumps(emitted)


def test_pseudonym_is_stable_and_one_way() -> None:
    first = pseudonymize("light.hallway_ceiling")
    second = pseudonymize("light.hallway_ceiling")
    assert first == second
    assert first != pseudonymize("light.other")
    assert "hallway" not in first
