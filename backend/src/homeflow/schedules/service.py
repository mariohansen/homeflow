"""Arming, cancelling and firing one-shot timers.

The safety argument for this module is short on purpose:

* A timer can only ever request an action the operator has already released for
  that device, and only from a fixed list that excludes every HIGH-risk action.
  A door can never be put on a timer, whatever a future client sends.
* Firing goes through :class:`~homeflow.commands.service.CommandService`, so the
  capability check, the device's own bounds, risk classification, the timeout
  and the audit record are the same ones a tap gets.
* A timer fires **once**. A failure is recorded and the timer settles; nothing
  is retried, because retrying a physical write nobody is watching is exactly
  what the command policy forbids.
* "Run for" turns the function on immediately and arms only the off. That is the
  safe direction: the unattended half of the timer reduces what the device is
  doing rather than increasing it.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from homeflow.audit.log import AuditEntry, AuditSink
from homeflow.auth.models import Principal
from homeflow.clock import Clock
from homeflow.commands.models import Action, Command, CommandStatus, OnOffParams, RiskClass
from homeflow.commands.policy import classify, required_capability_for
from homeflow.config.settings import Settings
from homeflow.devices.service import DeviceService
from homeflow.errors import (
    ActionAuthorizationRequiredError,
    CapabilityNotSupportedError,
    DeviceUnavailableError,
    HomeFlowError,
    ParameterOutOfRangeError,
    ScheduleNotFoundError,
)
from homeflow.events.bus import EventBus
from homeflow.events.models import DomainEvent, EventType
from homeflow.log import get_logger
from homeflow.schedules.models import SETTLED, Schedule, ScheduleKind, ScheduleStatus

_logger = get_logger(__name__)

#: Only these functions may be put on a timer. The list is a allowlist rather
#: than a risk-class check so that a future MEDIUM action does not silently
#: become schedulable; adding one is a deliberate edit with an ADR behind it.
TIMED_ACTIONS = frozenset({Action.SET_HEATER, Action.SET_FILTER})

#: Timers are rounded to this, so a stored moment always matches what was asked
#: for and the countdown a client shows cannot drift away from it.
_STEP_HOURS = 0.5
_MIN_HOURS = 0.5

_MAX_TRACKED = 200


class CommandSubmitter(Protocol):
    """The one way a timer is allowed to reach a device."""

    async def submit(
        self,
        principal: Principal,
        device_id: UUID,
        action: Action,
        raw_parameters: Mapping[str, Any],
        *,
        correlation_id: str,
    ) -> Command: ...


class ScheduleService:
    """Owns every armed timer and the loop that fires them."""

    __slots__ = ("_audit", "_bus", "_clock", "_commands", "_devices", "_schedules", "_settings")

    def __init__(
        self,
        *,
        commands: CommandSubmitter,
        devices: DeviceService,
        bus: EventBus,
        audit: AuditSink,
        clock: Clock,
        settings: Settings,
    ) -> None:
        # A protocol, not the class: the command pipeline knows nothing about
        # timers, and a timer can reach a device only through submit().
        self._commands = commands
        self._devices = devices
        self._bus = bus
        self._audit = audit
        self._clock = clock
        self._settings = settings
        self._schedules: OrderedDict[UUID, Schedule] = OrderedDict()

    # -- reading -----------------------------------------------------------

    def get(self, schedule_id: UUID) -> Schedule:
        schedule = self._schedules.get(schedule_id)
        if schedule is None:
            raise ScheduleNotFoundError
        return schedule

    def armed(self) -> list[Schedule]:
        return [item for item in self._schedules.values() if item.status is ScheduleStatus.ARMED]

    def for_device(self, device_id: UUID) -> list[Schedule]:
        """Every armed timer on one device, soonest first."""
        return sorted(
            (item for item in self.armed() if item.device_id == device_id),
            key=lambda item: item.due_at,
        )

    # -- arming ------------------------------------------------------------

    async def create(
        self,
        principal: Principal,
        device_id: UUID,
        action: Action,
        kind: ScheduleKind,
        hours: float,
        *,
        correlation_id: str,
    ) -> Schedule:
        device = self._devices.get(device_id)

        if action not in TIMED_ACTIONS:
            self._audit_denied(principal, device_id, action, correlation_id, "not_schedulable")
            raise CapabilityNotSupportedError(
                "Only the pool heater and filter pump can be put on a timer."
            )

        delay = self._validate_hours(hours)

        # "Run for" ends with the function off; "start in" ends with it on.
        desired = kind is ScheduleKind.DELAYED_START
        params = OnOffParams(on=desired)

        if not device.supports(required_capability_for(action, params)):
            self._audit_denied(
                principal, device_id, action, correlation_id, "capability_not_supported"
            )
            raise CapabilityNotSupportedError
        if classify(action, params) is RiskClass.HIGH:  # pragma: no cover - allowlist prevents it
            raise ActionAuthorizationRequiredError

        command_id: UUID | None = None
        if kind is ScheduleKind.RUN_FOR:
            # Turning it on is an ordinary, attended command. If it does not
            # take, nothing is armed: an "off" for a function the user never
            # managed to start would be a surprise, not a convenience.
            command = await self._commands.submit(
                principal,
                device_id,
                action,
                {"on": True},
                correlation_id=correlation_id,
            )
            command_id = command.id
            if command.status not in (CommandStatus.SUCCEEDED, CommandStatus.UNKNOWN):
                raise DeviceUnavailableError(
                    "The function could not be started, so no timer was set."
                )

        now = self._clock.now()
        schedule = Schedule(
            id=uuid4(),
            device_id=device_id,
            kind=kind,
            action=action,
            desired=desired,
            requested_by_user_id=principal.user_id,
            requested_by_client_id=principal.client_id,
            requested_by_display_name=principal.display_name,
            created_at=now,
            due_at=now + delay,
            correlation_id=correlation_id,
            command_id=command_id,
        )

        # One timer per function: a second one replaces the first rather than
        # leaving two unattended writes racing for the same switch.
        for existing in self.for_device(device_id):
            if existing.action is action:
                self._settle(existing, ScheduleStatus.SUPERSEDED)

        self._remember(schedule)
        self._record(schedule, "schedule.created")
        self._publish(schedule, EventType.SCHEDULE_ARMED)
        _logger.info(
            "schedule.created",
            homeflow_device_id=str(device_id),
            action=action.value,
            kind=kind.value,
            hours=hours,
        )
        return schedule

    def cancel(self, principal: Principal, schedule_id: UUID) -> Schedule:
        schedule = self.get(schedule_id)
        if schedule.status is not ScheduleStatus.ARMED:
            return schedule
        settled = self._settle(schedule, ScheduleStatus.CANCELLED, actor=principal)
        _logger.info("schedule.cancelled", homeflow_device_id=str(schedule.device_id))
        return settled

    def _validate_hours(self, hours: float) -> timedelta:
        maximum = self._settings.schedule_max_hours
        rounded = round(hours / _STEP_HOURS) * _STEP_HOURS
        if rounded < _MIN_HOURS or rounded > maximum or abs(rounded - hours) > 1e-9:
            raise ParameterOutOfRangeError(
                f"A timer must be between {_MIN_HOURS} and {maximum} hours, "
                f"in steps of {_STEP_HOURS}."
            )
        return timedelta(hours=rounded)

    # -- firing ------------------------------------------------------------

    async def run(self) -> None:
        """Fire timers as they come due. Cancelled with the application."""
        while True:
            await asyncio.sleep(self._settings.schedule_tick_seconds)
            for schedule in self._due():
                try:
                    await self.fire(schedule)
                except asyncio.CancelledError:
                    raise
                except Exception:  # pragma: no cover - defensive
                    # One broken timer must not stop the rest from settling.
                    _logger.exception("schedule.fire_failed")
                    self._settle(schedule, ScheduleStatus.FAILED, failure_code="internal_error")

    def _due(self) -> list[Schedule]:
        now = self._clock.now()
        return [item for item in self.armed() if item.due_at <= now]

    async def fire(self, schedule: Schedule) -> None:
        """Run one timer now and settle it, whatever the outcome.

        Public so the loop body can be exercised without waiting on real time.
        """
        principal = Principal(
            user_id=schedule.requested_by_user_id,
            client_id=schedule.requested_by_client_id,
            display_name=schedule.requested_by_display_name,
        )
        try:
            command = await self._commands.submit(
                principal,
                schedule.device_id,
                schedule.action,
                {"on": schedule.desired},
                correlation_id=schedule.correlation_id,
            )
        except HomeFlowError as exc:
            # No retry. An unattended physical write gets exactly one attempt,
            # and the household is told what happened instead.
            self._settle(schedule, ScheduleStatus.FAILED, failure_code=exc.problem_type)
            return

        if command.status is CommandStatus.SUCCEEDED:
            status = ScheduleStatus.COMPLETED
        elif command.status is CommandStatus.UNKNOWN:
            status = ScheduleStatus.UNKNOWN
        else:
            status = ScheduleStatus.FAILED
        self._settle(
            schedule,
            status,
            failure_code=command.failure_code,
            command_id=command.id,
        )

    # -- bookkeeping -------------------------------------------------------

    def _settle(
        self,
        schedule: Schedule,
        status: ScheduleStatus,
        *,
        failure_code: str | None = None,
        command_id: UUID | None = None,
        actor: Principal | None = None,
    ) -> Schedule:
        assert status in SETTLED  # noqa: S101 - narrowing, enum-enforced
        settled = schedule.settled(status, failure_code=failure_code, command_id=command_id)
        self._schedules[settled.id] = settled
        self._record(settled, f"schedule.{status.value.lower()}", actor=actor)
        self._publish(settled, EventType.SCHEDULE_SETTLED)
        return settled

    def _remember(self, schedule: Schedule) -> None:
        self._schedules[schedule.id] = schedule
        while len(self._schedules) > _MAX_TRACKED:
            # Only settled timers are ever evicted, so an armed one cannot be
            # forgotten while it still owes the household an action.
            for candidate_id, candidate in self._schedules.items():
                if candidate.status is not ScheduleStatus.ARMED:
                    del self._schedules[candidate_id]
                    break
            else:  # pragma: no cover - would need 200 armed timers
                return

    def _publish(self, schedule: Schedule, event_type: EventType) -> None:
        self._bus.publish(
            DomainEvent(
                type=event_type,
                occurred_at=self._clock.now(),
                device_id=schedule.device_id,
                correlation_id=schedule.correlation_id,
                payload={"scheduleId": str(schedule.id), "status": schedule.status.value},
            )
        )

    def _record(self, schedule: Schedule, event: str, *, actor: Principal | None = None) -> None:
        self._audit.record(
            AuditEntry(
                occurred_at=self._clock.now(),
                event=event,
                actor_user_id=actor.user_id if actor else schedule.requested_by_user_id,
                actor_client_id=actor.client_id if actor else schedule.requested_by_client_id,
                device_id=schedule.device_id,
                command_id=schedule.command_id,
                action=schedule.action.value,
                risk_class=RiskClass.MEDIUM,
                outcome=schedule.status.value,
                correlation_id=schedule.correlation_id,
                context={
                    "kind": schedule.kind.value,
                    "desired": schedule.desired,
                    "dueAt": schedule.due_at.isoformat(),
                },
            )
        )

    def _audit_denied(
        self,
        principal: Principal,
        device_id: UUID,
        action: Action,
        correlation_id: str,
        reason: str,
    ) -> None:
        self._audit.record(
            AuditEntry(
                occurred_at=self._clock.now(),
                event="schedule.denied",
                actor_user_id=principal.user_id,
                actor_client_id=principal.client_id,
                device_id=device_id,
                action=action.value,
                outcome=reason,
                correlation_id=correlation_id,
            )
        )
