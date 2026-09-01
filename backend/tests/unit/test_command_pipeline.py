"""The single mutation pipeline: authorisation, bounds, timeouts, reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from conftest import make_settings, run
from homeflow.audit.log import InMemoryAuditLog
from homeflow.auth.models import Principal
from homeflow.capabilities import Capability
from homeflow.clock import SystemClock
from homeflow.commands.models import Action, CommandStatus, RiskClass
from homeflow.commands.service import CommandService
from homeflow.config.settings import Environment
from homeflow.devices.models import Availability, Device
from homeflow.devices.registry import DeviceRegistry
from homeflow.devices.service import DeviceService
from homeflow.errors import (
    ActionAuthorizationRequiredError,
    CapabilityNotSupportedError,
    DeviceUnavailableError,
    InvalidParametersError,
    ParameterOutOfRangeError,
)
from homeflow.events.bus import EventBus
from homeflow.integrations.base.errors import ProviderUnavailableError
from homeflow.integrations.base.models import CommandOutcome
from simulators.fake_provider import FakeProvider

PRINCIPAL = Principal(user_id=uuid4(), client_id=uuid4(), display_name="Alice")


@dataclass(slots=True)
class Harness:
    commands: CommandService
    devices: DeviceService
    audit: InMemoryAuditLog
    device: Device
    provider: FakeProvider


async def _harness(provider: FakeProvider, **overrides: object) -> Harness:
    settings = make_settings(
        env=Environment.TEST,
        demo_mode=True,
        command_timeout_seconds=0.2,
        reconcile_timeout_seconds=0.2,
        **overrides,  # type: ignore[arg-type]
    )
    clock = SystemClock()
    bus = EventBus()
    audit = InMemoryAuditLog()
    registry = DeviceRegistry(id_salt=settings.effective_id_salt)
    devices = DeviceService(registry=registry, bus=bus, clock=clock)
    await devices.bootstrap([provider])
    commands = CommandService(
        devices=devices,
        providers={provider.name: provider},
        bus=bus,
        audit=audit,
        clock=clock,
        settings=settings,
    )
    return Harness(commands, devices, audit, devices.list_devices()[0], provider)


def test_successful_command_updates_canonical_state() -> None:
    async def scenario() -> tuple[CommandStatus, float | None]:
        harness = await _harness(FakeProvider())
        command = await harness.commands.submit(
            PRINCIPAL,
            harness.device.id,
            Action.SET_TARGET_TEMPERATURE,
            {"celsius": 34.0},
            correlation_id="corr-1",
        )
        refreshed = harness.devices.get(harness.device.id)
        return command.status, refreshed.state.target_temperature_c

    status, target = run(scenario())
    assert status is CommandStatus.SUCCEEDED
    assert target == 34.0


def test_setpoint_outside_the_device_range_is_refused_before_any_write() -> None:
    async def scenario() -> int:
        harness = await _harness(FakeProvider())
        with pytest.raises(ParameterOutOfRangeError):
            await harness.commands.submit(
                PRINCIPAL,
                harness.device.id,
                Action.SET_TARGET_TEMPERATURE,
                {"celsius": 45.0},
                correlation_id="corr-2",
            )
        return harness.provider.execute_calls

    assert run(scenario()) == 0


def test_invalid_parameters_are_rejected() -> None:
    async def scenario() -> None:
        harness = await _harness(FakeProvider())
        with pytest.raises(InvalidParametersError):
            await harness.commands.submit(
                PRINCIPAL,
                harness.device.id,
                Action.SET_TARGET_TEMPERATURE,
                {"celsius": "warm"},
                correlation_id="corr-3",
            )

    run(scenario())


def test_unsupported_capability_is_rejected() -> None:
    async def scenario() -> None:
        harness = await _harness(FakeProvider())
        with pytest.raises(CapabilityNotSupportedError):
            await harness.commands.submit(
                PRINCIPAL,
                harness.device.id,
                Action.SET_BRIGHTNESS,
                {"brightness": 50},
                correlation_id="corr-4",
            )

    run(scenario())


def test_high_risk_action_is_refused_until_the_authorisation_flow_exists() -> None:
    async def scenario() -> str | None:
        provider = FakeProvider(extra_capabilities=(Capability.LOCK, Capability.UNLOCK))
        harness = await _harness(provider)
        with pytest.raises(ActionAuthorizationRequiredError):
            await harness.commands.submit(
                PRINCIPAL,
                harness.device.id,
                Action.SET_LOCK_STATE,
                {"desired": "UNLOCKED"},
                correlation_id="corr-5",
            )
        assert provider.execute_calls == 0
        denied = [entry for entry in harness.audit.recent() if entry.event == "command.denied"]
        return denied[0].outcome if denied else None

    # The refusal is audited, not silently dropped.
    assert run(scenario()) == "action_authorization_required"


def test_offline_device_refuses_writes() -> None:
    async def scenario() -> int:
        provider = FakeProvider(availability=Availability.OFFLINE)
        harness = await _harness(provider)
        with pytest.raises(DeviceUnavailableError):
            await harness.commands.submit(
                PRINCIPAL,
                harness.device.id,
                Action.SET_HEATER,
                {"on": True},
                correlation_id="corr-6",
            )
        return provider.execute_calls

    assert run(scenario()) == 0


def test_provider_failure_is_reported_as_failed() -> None:
    async def scenario() -> tuple[CommandStatus, str | None]:
        provider = FakeProvider(raise_error=ProviderUnavailableError("boom"))
        harness = await _harness(provider)
        command = await harness.commands.submit(
            PRINCIPAL,
            harness.device.id,
            Action.SET_HEATER,
            {"on": True},
            correlation_id="corr-7",
        )
        return command.status, command.failure_code

    status, failure = run(scenario())
    assert status is CommandStatus.FAILED
    assert failure == "device_unavailable"


def test_timeout_followed_by_matching_state_counts_as_success() -> None:
    """A slow device that did apply the command is not reported as a failure."""

    async def scenario() -> tuple[CommandStatus, int]:
        provider = FakeProvider(execute_delay=5.0)
        harness = await _harness(provider)
        # The write is applied out of band, as a real device would.
        provider.state = provider.state.model_copy(update={"heater": True})
        command = await harness.commands.submit(
            PRINCIPAL,
            harness.device.id,
            Action.SET_HEATER,
            {"on": True},
            correlation_id="corr-8",
        )
        return command.status, provider.execute_calls

    status, calls = run(scenario())
    assert status is CommandStatus.SUCCEEDED
    # The write is never repeated after a timeout.
    assert calls == 1


def test_timeout_with_unchanged_state_is_unknown_not_failed() -> None:
    async def scenario() -> tuple[CommandStatus, str | None]:
        provider = FakeProvider(execute_delay=5.0)
        harness = await _harness(provider)
        command = await harness.commands.submit(
            PRINCIPAL,
            harness.device.id,
            Action.SET_HEATER,
            {"on": True},
            correlation_id="corr-9",
        )
        return command.status, command.failure_code

    status, failure = run(scenario())
    assert status is CommandStatus.UNKNOWN
    assert failure == "device_response_timeout"


def test_provider_reported_unknown_outcome_is_preserved() -> None:
    async def scenario() -> CommandStatus:
        provider = FakeProvider(outcome=CommandOutcome.UNKNOWN, apply_writes=False)
        harness = await _harness(provider)
        command = await harness.commands.submit(
            PRINCIPAL,
            harness.device.id,
            Action.SET_HEATER,
            {"on": True},
            correlation_id="corr-10",
        )
        return command.status

    assert run(scenario()) is CommandStatus.UNKNOWN


def test_every_command_is_audited_with_its_risk_class() -> None:
    async def scenario() -> list[tuple[str, RiskClass | None]]:
        harness = await _harness(FakeProvider())
        await harness.commands.submit(
            PRINCIPAL,
            harness.device.id,
            Action.SET_HEATER,
            {"on": True},
            correlation_id="corr-11",
        )
        return [(entry.event, entry.risk_class) for entry in harness.audit.recent()]

    entries = run(scenario())
    assert ("command.requested", RiskClass.MEDIUM) in entries
    assert ("command.completed", RiskClass.MEDIUM) in entries
