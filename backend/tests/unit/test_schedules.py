"""One-shot timers: the only unattended physical write in HomeFlow.

What these guard is the safety argument, not the convenience. A timer cannot
name an action the operator has not released, cannot name a door whatever the
client sends, fires exactly once, and is never retried.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

import pytest

from conftest import make_settings, run
from homeflow.audit.log import InMemoryAuditLog
from homeflow.auth.models import Principal
from homeflow.capabilities import Capability
from homeflow.clock import ManualClock
from homeflow.commands.models import Action, CommandStatus
from homeflow.commands.service import CommandService
from homeflow.config.settings import Environment
from homeflow.devices.models import Availability, Device, StateSource
from homeflow.devices.registry import DeviceRegistry
from homeflow.devices.service import DeviceService
from homeflow.errors import (
    CapabilityNotSupportedError,
    DeviceUnavailableError,
    ParameterOutOfRangeError,
    ScheduleNotFoundError,
)
from homeflow.events.bus import EventBus
from homeflow.integrations.base.errors import ProviderUnavailableError
from homeflow.integrations.base.models import CommandOutcome
from homeflow.schedules.models import Schedule, ScheduleKind, ScheduleStatus
from homeflow.schedules.service import ScheduleService
from simulators.fake_provider import FakeProvider, fake_ref

PRINCIPAL = Principal(user_id=uuid4(), client_id=uuid4(), display_name="Alice")

#: The tub functions the operator has released in these tests.
POOL_CAPABILITIES = (Capability.FILTER, Capability.BUBBLES)


@dataclass(slots=True)
class Harness:
    provider: FakeProvider
    clock: ManualClock
    audit: InMemoryAuditLog
    commands: CommandService
    devices: DeviceService
    schedules: ScheduleService
    device: Device

    async def arm(
        self,
        action: Action = Action.SET_HEATER,
        kind: ScheduleKind = ScheduleKind.DELAYED_START,
        hours: float = 1.0,
    ) -> Schedule:
        return await self.schedules.create(
            PRINCIPAL,
            self.device.id,
            action,
            kind,
            hours,
            correlation_id="corr-1",
        )

    async def wake(self) -> None:
        """Do what the loop does on waking, without the sleeping."""
        for schedule in self.schedules.armed():
            if schedule.due_at <= self.clock.now():
                await self.schedules.fire(schedule)

    def status_of(self, schedule: Schedule) -> ScheduleStatus:
        return self.schedules.get(schedule.id).status

    def go_offline(self) -> None:
        self.provider.availability = Availability.OFFLINE
        self.devices.ingest(
            fake_ref(),
            self.provider._snapshot(),
            source=StateSource.PROVIDER_EVENT,
        )


async def _harness(provider: FakeProvider | None = None) -> Harness:
    resolved = provider or FakeProvider(extra_capabilities=POOL_CAPABILITIES)
    settings = make_settings(
        env=Environment.TEST,
        demo_mode=True,
        command_timeout_seconds=0.5,
        reconcile_timeout_seconds=0.2,
        schedule_tick_seconds=1.0,
    )
    clock = ManualClock()
    bus = EventBus()
    audit = InMemoryAuditLog()
    devices = DeviceService(registry=DeviceRegistry(id_salt="test-salt"), bus=bus, clock=clock)
    await devices.bootstrap([resolved])
    commands = CommandService(
        devices=devices,
        providers={resolved.name: resolved},
        bus=bus,
        audit=audit,
        clock=clock,
        settings=settings,
    )
    return Harness(
        provider=resolved,
        clock=clock,
        audit=audit,
        commands=commands,
        devices=devices,
        schedules=ScheduleService(
            commands=commands,
            devices=devices,
            bus=bus,
            audit=audit,
            clock=clock,
            settings=settings,
        ),
        device=devices.list_devices()[0],
    )


def test_arming_a_timer_does_not_touch_the_device() -> None:
    async def scenario() -> tuple[bool | None, int]:
        harness = await _harness()
        await harness.arm(hours=2.0)
        return harness.provider.state.heater, harness.provider.execute_calls

    heater, calls = run(scenario())
    assert heater is False
    assert calls == 0


def test_a_timer_does_not_fire_early() -> None:
    async def scenario() -> bool | None:
        harness = await _harness()
        await harness.arm(hours=2.0)
        harness.clock.advance(3600)
        await harness.wake()
        return harness.provider.state.heater

    assert run(scenario()) is False


def test_a_delayed_start_turns_the_function_on_when_due() -> None:
    async def scenario() -> tuple[bool | None, ScheduleStatus]:
        harness = await _harness()
        schedule = await harness.arm()
        harness.clock.advance(3601)
        await harness.wake()
        return harness.provider.state.heater, harness.status_of(schedule)

    heater, status = run(scenario())
    assert heater is True
    assert status is ScheduleStatus.COMPLETED


def test_run_for_starts_now_and_arms_only_the_stop() -> None:
    """The unattended half of a timer reduces what the device is doing."""

    async def scenario() -> tuple[bool | None, bool | None, bool]:
        harness = await _harness()
        schedule = await harness.arm(kind=ScheduleKind.RUN_FOR)
        started = harness.provider.state.heater
        harness.clock.advance(3601)
        await harness.wake()
        return started, harness.provider.state.heater, schedule.desired

    started, ended, desired = run(scenario())
    assert started is True, "run-for starts the function immediately"
    assert ended is False, "and stops it when the timer runs out"
    assert desired is False, "the unattended action is the off"


def test_a_door_can_never_be_put_on_a_timer() -> None:
    """The allowlist, not the risk class, is what keeps this true."""

    async def scenario() -> None:
        harness = await _harness()
        with pytest.raises(CapabilityNotSupportedError):
            await harness.arm(action=Action.SET_LOCK_STATE)

    run(scenario())


def test_an_unreleased_function_cannot_be_timed() -> None:
    async def scenario() -> None:
        # This provider offers no FILTER capability.
        harness = await _harness(FakeProvider())
        with pytest.raises(CapabilityNotSupportedError):
            await harness.arm(action=Action.SET_FILTER)

    run(scenario())


@pytest.mark.parametrize("hours", [0.0, 0.25, 0.75, 25.0, -3.0])
def test_the_delay_is_bounded(hours: float) -> None:
    async def scenario() -> None:
        harness = await _harness()
        with pytest.raises(ParameterOutOfRangeError):
            await harness.arm(hours=hours)

    run(scenario())


def test_the_stored_moment_matches_what_was_asked_for() -> None:
    async def scenario() -> timedelta:
        harness = await _harness()
        created = harness.clock.now()
        schedule = await harness.arm(hours=2.5)
        return schedule.due_at - created

    assert run(scenario()) == timedelta(hours=2.5)


def test_run_for_arms_nothing_when_the_start_fails() -> None:
    """An "off" for a function that never started would be a surprise."""

    async def scenario() -> tuple[int, bool | None]:
        harness = await _harness(
            FakeProvider(
                raise_error=ProviderUnavailableError("unreachable"),
                extra_capabilities=POOL_CAPABILITIES,
            )
        )
        with pytest.raises(DeviceUnavailableError):
            await harness.arm(kind=ScheduleKind.RUN_FOR)
        return len(harness.schedules.armed()), harness.provider.state.heater

    armed, heater = run(scenario())
    assert armed == 0
    assert heater is False


def test_a_second_timer_replaces_the_first() -> None:
    """Two unattended writes must never race for the same switch."""

    async def scenario() -> tuple[int, ScheduleStatus]:
        harness = await _harness()
        first = await harness.arm(hours=1.0)
        await harness.arm(hours=3.0)
        return len(harness.schedules.for_device(harness.device.id)), harness.status_of(first)

    armed, replaced = run(scenario())
    assert armed == 1
    assert replaced is ScheduleStatus.SUPERSEDED


def test_two_functions_can_be_timed_independently() -> None:
    async def scenario() -> int:
        harness = await _harness()
        await harness.arm(action=Action.SET_HEATER)
        await harness.arm(action=Action.SET_FILTER)
        return len(harness.schedules.for_device(harness.device.id))

    assert run(scenario()) == 2


def test_cancelling_disarms_it() -> None:
    async def scenario() -> tuple[ScheduleStatus, bool | None, int]:
        harness = await _harness()
        schedule = await harness.arm()
        cancelled = harness.schedules.cancel(PRINCIPAL, schedule.id)
        harness.clock.advance(7200)
        await harness.wake()
        return cancelled.status, harness.provider.state.heater, len(harness.schedules.armed())

    status, heater, armed = run(scenario())
    assert status is ScheduleStatus.CANCELLED
    assert heater is False, "a cancelled timer must never reach the device"
    assert armed == 0


def test_an_unknown_timer_is_not_found() -> None:
    async def scenario() -> None:
        harness = await _harness()
        with pytest.raises(ScheduleNotFoundError):
            harness.schedules.cancel(PRINCIPAL, uuid4())

    run(scenario())


def test_a_failure_settles_the_timer_and_is_never_retried() -> None:
    async def scenario() -> tuple[ScheduleStatus, int]:
        harness = await _harness()
        schedule = await harness.arm()
        # The controller goes away before the timer is due.
        harness.provider.raise_error = ProviderUnavailableError("gone")
        before = harness.provider.execute_calls

        harness.clock.advance(3601)
        await harness.wake()
        status = harness.status_of(schedule)

        # A second pass of the loop must not try again.
        await harness.wake()
        return status, harness.provider.execute_calls - before

    status, attempts = run(scenario())
    assert status is ScheduleStatus.FAILED
    assert attempts == 1, "an unattended physical write gets exactly one attempt"


def test_an_offline_device_settles_the_timer_without_reaching_it() -> None:
    async def scenario() -> tuple[ScheduleStatus, int]:
        harness = await _harness()
        schedule = await harness.arm()
        harness.go_offline()
        before = harness.provider.execute_calls

        harness.clock.advance(3601)
        await harness.wake()
        return harness.status_of(schedule), harness.provider.execute_calls - before

    status, attempts = run(scenario())
    assert status is ScheduleStatus.FAILED
    assert attempts == 0, "an offline device is refused before anything is sent"


def test_a_silent_device_leaves_the_timer_unknown_not_failed() -> None:
    """A physical device can act after the gateway gives up on hearing back."""

    async def scenario() -> ScheduleStatus:
        harness = await _harness(
            FakeProvider(
                outcome=CommandOutcome.UNKNOWN,
                apply_writes=False,
                extra_capabilities=POOL_CAPABILITIES,
            )
        )
        schedule = await harness.arm()
        harness.clock.advance(3601)
        await harness.wake()
        return harness.status_of(schedule)

    assert run(scenario()) is ScheduleStatus.UNKNOWN


def test_a_timer_does_not_bypass_the_command_pipeline() -> None:
    """Firing must produce an ordinary, audited command record."""

    async def scenario() -> tuple[str, CommandStatus]:
        harness = await _harness()
        schedule = await harness.arm()
        harness.clock.advance(3601)
        await harness.wake()

        settled = harness.schedules.get(schedule.id)
        assert settled.command_id is not None
        command = harness.commands.get(settled.command_id)
        return command.action.value, command.status

    action, status = run(scenario())
    assert action == "SET_HEATER"
    assert status is CommandStatus.SUCCEEDED


def test_every_timer_is_audited() -> None:
    async def scenario() -> list[str]:
        harness = await _harness()
        schedule = await harness.arm()
        harness.schedules.cancel(PRINCIPAL, schedule.id)
        return [entry.event for entry in harness.audit.recent(20)]

    events = run(scenario())
    assert "schedule.created" in events
    assert "schedule.cancelled" in events


def test_a_refused_timer_is_audited_too() -> None:
    async def scenario() -> list[str]:
        harness = await _harness()
        with pytest.raises(CapabilityNotSupportedError):
            await harness.arm(action=Action.SET_LOCK_STATE)
        return [entry.event for entry in harness.audit.recent(20)]

    assert "schedule.denied" in run(scenario())
