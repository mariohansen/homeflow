"""Releasing a Bestway capability is a configuration decision, so it fails closed."""

from __future__ import annotations

import pytest

from conftest import make_settings
from homeflow.clock import SystemClock
from homeflow.config.settings import Environment
from homeflow.container import build_bestway_profile, build_providers
from homeflow.integrations.bestway.datapoints import Datapoint, ProfileError
from homeflow.integrations.bestway.provider import PROVIDER_NAME

HOST = "192.0.2.10"


def enabled(**overrides: object):
    return make_settings(
        env=Environment.TEST,
        demo_mode=False,
        id_salt="salt",
        bestway_enabled=True,
        bestway_host=HOST,
        **overrides,
    )


def test_enabling_the_adapter_without_an_address_is_refused() -> None:
    with pytest.raises(ValueError, match="BESTWAY_HOST"):
        make_settings(env=Environment.TEST, demo_mode=False, id_salt="salt", bestway_enabled=True)


def test_releasing_a_capability_requires_a_verified_layout() -> None:
    """The whole point of the gate: no write release without verification."""
    with pytest.raises(ValueError, match="TRUST_PROFILE"):
        enabled(bestway_write_enabled=("HEATER",), bestway_trust_profile=False)


def test_demo_mode_cannot_run_next_to_real_hardware() -> None:
    with pytest.raises(ValueError, match="Demo mode"):
        make_settings(
            env=Environment.TEST,
            demo_mode=True,
            bestway_enabled=True,
            bestway_host=HOST,
        )


def test_the_layout_defaults_to_refusing_everything() -> None:
    profile = build_bestway_profile(enabled())
    assert profile.trusted is False
    assert profile.writable == frozenset()


def test_verification_and_release_are_two_separate_decisions() -> None:
    verified_only = build_bestway_profile(enabled(bestway_trust_profile=True))
    assert verified_only.trusted is True
    assert verified_only.writable == frozenset()
    assert verified_only.may_write(Datapoint.HEATER) is False

    released = build_bestway_profile(
        enabled(bestway_trust_profile=True, bestway_write_enabled=("HEATER",))
    )
    assert released.may_write(Datapoint.HEATER) is True
    assert released.may_write(Datapoint.BUBBLES) is False


def test_a_write_list_is_accepted_as_a_comma_separated_string() -> None:
    profile = build_bestway_profile(
        enabled(bestway_trust_profile=True, bestway_write_enabled="HEATER, FILTER_PUMP")
    )
    assert profile.writable == frozenset({Datapoint.HEATER, Datapoint.FILTER_PUMP})


def test_an_unknown_datapoint_name_fails_at_startup() -> None:
    with pytest.raises(ProfileError, match="unknown datapoint"):
        build_bestway_profile(
            enabled(bestway_trust_profile=True, bestway_write_enabled=("NOT_A_DATAPOINT",))
        )


def test_an_unknown_builtin_layout_fails_at_startup() -> None:
    with pytest.raises(ProfileError, match="no built-in"):
        build_bestway_profile(enabled(bestway_profile="does-not-exist"))


def test_the_adapter_is_wired_only_when_it_is_enabled() -> None:
    clock = SystemClock()

    without = build_providers(
        make_settings(env=Environment.TEST, demo_mode=False, id_salt="salt"), clock
    )
    assert without == {}

    with_adapter = build_providers(enabled(), clock)
    assert set(with_adapter) == {PROVIDER_NAME}


def test_demo_mode_wires_the_synthetic_provider_only() -> None:
    providers = build_providers(
        make_settings(env=Environment.TEST, demo_mode=True), clock=SystemClock()
    )
    assert set(providers) == {"demo"}
