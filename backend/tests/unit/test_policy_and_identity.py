"""Risk classification, capability mapping and identifier derivation."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from conftest import make_settings
from homeflow.capabilities import Capability
from homeflow.commands.models import (
    Action,
    LockStateParams,
    OnOffParams,
    TargetTemperatureParams,
)
from homeflow.commands.policy import ACTION_SPECS, classify, required_capability_for
from homeflow.config.settings import Environment
from homeflow.devices.identity import device_uuid, room_uuid
from homeflow.devices.models import LockState


def test_every_action_has_a_spec() -> None:
    assert set(ACTION_SPECS) == set(Action)


def test_low_risk_actions_stay_low() -> None:
    assert classify(Action.SET_POWER, OnOffParams(on=True)) is not None
    assert classify(Action.SET_POWER, OnOffParams(on=True)).value == "LOW"


def test_pool_actions_are_medium_risk() -> None:
    params = TargetTemperatureParams(celsius=37.0)
    assert classify(Action.SET_TARGET_TEMPERATURE, params).value == "MEDIUM"
    assert classify(Action.SET_HEATER, OnOffParams(on=True)).value == "MEDIUM"


def test_locking_is_medium_but_unlocking_is_high() -> None:
    lock = LockStateParams(desired=LockState.LOCKED)
    unlock = LockStateParams(desired=LockState.UNLOCKED)
    assert classify(Action.SET_LOCK_STATE, lock).value == "MEDIUM"
    assert classify(Action.SET_LOCK_STATE, unlock).value == "HIGH"
    assert classify(Action.UNLATCH, ACTION_SPECS[Action.UNLATCH].params_model()).value == "HIGH"


def test_unlocking_requires_the_unlock_capability() -> None:
    lock = LockStateParams(desired=LockState.LOCKED)
    unlock = LockStateParams(desired=LockState.UNLOCKED)
    assert required_capability_for(Action.SET_LOCK_STATE, lock) is Capability.LOCK
    assert required_capability_for(Action.SET_LOCK_STATE, unlock) is Capability.UNLOCK


def test_unknown_lock_state_is_rejected() -> None:
    with pytest.raises(ValidationError):
        LockStateParams(desired=LockState.UNKNOWN)


def test_parameters_are_bounded_and_closed() -> None:
    with pytest.raises(ValidationError):
        TargetTemperatureParams(celsius=999.0)
    with pytest.raises(ValidationError):
        OnOffParams(on=True, extra_field=1)  # type: ignore[call-arg]


def test_device_ids_are_stable_but_do_not_leak_provider_ids() -> None:
    salt = "salt-a"
    first = device_uuid(salt, "home_assistant", "light.hallway_ceiling")
    again = device_uuid(salt, "home_assistant", "light.hallway_ceiling")
    other_salt = device_uuid("salt-b", "home_assistant", "light.hallway_ceiling")

    assert first == again
    assert first != other_salt
    assert "hallway" not in str(first)
    assert first != device_uuid(salt, "home_assistant", "light.other")


def test_room_ids_are_case_insensitive() -> None:
    assert room_uuid("salt", "Living Room") == room_uuid("salt", "living room ")


def test_production_refuses_demo_mode() -> None:
    with pytest.raises(ValueError, match="DEMO_MODE"):
        make_settings(
            env=Environment.PRODUCTION,
            demo_mode=True,
            id_salt=SecretStr("s"),
            allowed_hosts=("gw",),
        )


def test_production_refuses_a_development_credential() -> None:
    with pytest.raises(ValueError, match="DEV_CLIENT_TOKEN"):
        make_settings(
            env=Environment.PRODUCTION,
            demo_mode=False,
            id_salt=SecretStr("s"),
            dev_client_token=SecretStr("x"),
            allowed_hosts=("gw",),
        )


def test_production_refuses_a_wildcard_host() -> None:
    with pytest.raises(ValueError, match="ALLOWED_HOSTS"):
        make_settings(
            env=Environment.PRODUCTION,
            demo_mode=False,
            id_salt=SecretStr("s"),
            allowed_hosts=("*",),
        )


def test_non_demo_requires_an_id_salt() -> None:
    with pytest.raises(ValueError, match="ID_SALT"):
        make_settings(env=Environment.DEVELOPMENT, demo_mode=False)


def test_list_settings_survive_the_trip_through_an_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A list in the environment is comma separated, not JSON.

    Production is told to set HOMEFLOW_ALLOWED_HOSTS, so this path has to work
    from a real environment variable and not only from a keyword argument.
    """
    monkeypatch.setenv("HOMEFLOW_ALLOWED_HOSTS", "gateway.example.internal, localhost")
    monkeypatch.setenv("HOMEFLOW_BESTWAY_WRITE_ENABLED", "BUBBLES,HEATER")
    # Releasing a capability still requires a verified layout, even here.
    monkeypatch.setenv("HOMEFLOW_BESTWAY_TRUST_PROFILE", "true")

    settings = make_settings(env=Environment.TEST, demo_mode=True)

    assert settings.allowed_hosts == ("gateway.example.internal", "localhost")
    assert settings.bestway_write_enabled == ("BUBBLES", "HEATER")
