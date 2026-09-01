"""The single mutation pipeline (see docs/architecture/overview.md).

Every state change in HomeFlow goes through :meth:`CommandService.submit`:
capability check, parameter validation, device-declared range check, risk
classification, audit, bounded execution and reconciliation. There is no second
path to a device and no raw passthrough.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from homeflow.audit.log import AuditEntry, AuditSink
from homeflow.auth.models import Principal
from homeflow.clock import Clock
from homeflow.commands.models import (
    Action,
    Command,
    CommandParams,
    CommandStatus,
    RiskClass,
    TargetTemperatureParams,
)
from homeflow.commands.policy import ACTION_SPECS, classify, required_capability_for
from homeflow.commands.reconcile import desired_matches
from homeflow.config.settings import Settings
from homeflow.devices.models import Availability, Device, StateSource
from homeflow.devices.service import DeviceService
from homeflow.errors import (
    ActionAuthorizationRequiredError,
    CapabilityNotSupportedError,
    CommandNotFoundError,
    DeviceUnavailableError,
    InvalidParametersError,
    ParameterOutOfRangeError,
)
from homeflow.events.bus import EventBus
from homeflow.events.models import DomainEvent, EventType
from homeflow.integrations.base.errors import ProviderError
from homeflow.integrations.base.models import CommandOutcome, ProviderCommand, ProviderDeviceRef
from homeflow.integrations.base.provider import DeviceProvider
from homeflow.log import get_logger

_logger = get_logger(__name__)

#: A command stops being executable this long after submission.
_COMMAND_TTL = timedelta(minutes=2)
_MAX_TRACKED_COMMANDS = 500
_MAX_DEVICE_LOCKS = 256

_COMPLETION_EVENTS: Mapping[CommandStatus, EventType] = {
    CommandStatus.SUCCEEDED: EventType.COMMAND_SUCCEEDED,
    CommandStatus.FAILED: EventType.COMMAND_FAILED,
    # An unknown outcome is not a failure: the device may still have acted.
    CommandStatus.UNKNOWN: EventType.COMMAND_TIMED_OUT,
}


class CommandService:
    __slots__ = (
        "_audit",
        "_bus",
        "_clock",
        "_commands",
        "_devices",
        "_locks",
        "_providers",
        "_settings",
    )

    def __init__(
        self,
        *,
        devices: DeviceService,
        providers: Mapping[str, DeviceProvider],
        bus: EventBus,
        audit: AuditSink,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self._devices = devices
        self._providers = dict(providers)
        self._bus = bus
        self._audit = audit
        self._clock = clock
        self._settings = settings
        self._commands: OrderedDict[UUID, Command] = OrderedDict()
        self._locks: OrderedDict[UUID, asyncio.Lock] = OrderedDict()

    def get(self, command_id: UUID) -> Command:
        command = self._commands.get(command_id)
        if command is None:
            raise CommandNotFoundError
        return command

    async def submit(
        self,
        principal: Principal,
        device_id: UUID,
        action: Action,
        raw_parameters: Mapping[str, Any],
        *,
        correlation_id: str,
    ) -> Command:
        device = self._devices.get(device_id)
        params = self._validate(action, raw_parameters)

        capability = required_capability_for(action, params)
        if not device.supports(capability):
            self._audit_denied(
                principal, device, action, correlation_id, "capability_not_supported"
            )
            raise CapabilityNotSupportedError

        self._check_constraints(device, params)

        risk = classify(action, params)
        if risk is RiskClass.HIGH:
            # No HIGH-risk action executes until the fresh device-owner
            # authorisation flow described in SECURITY.md exists.
            self._audit_denied(
                principal, device, action, correlation_id, "action_authorization_required"
            )
            raise ActionAuthorizationRequiredError

        if device.availability is Availability.OFFLINE:
            self._audit_denied(principal, device, action, correlation_id, "device_unavailable")
            raise DeviceUnavailableError

        command = self._create(principal, device, action, params, risk, correlation_id)
        return await self._execute(command, device, params)

    def _validate(self, action: Action, raw_parameters: Mapping[str, Any]) -> CommandParams:
        spec = ACTION_SPECS.get(action)
        if spec is None:  # pragma: no cover - Action is a closed enum
            raise CapabilityNotSupportedError
        try:
            return spec.params_model.model_validate(dict(raw_parameters))
        except ValidationError as exc:
            raise InvalidParametersError from exc

    def _check_constraints(self, device: Device, params: CommandParams) -> None:
        """Enforce the device's own verified limits, never a hard-coded guess."""
        if not isinstance(params, TargetTemperatureParams):
            return
        minimum = device.constraints.target_temperature_min_c
        maximum = device.constraints.target_temperature_max_c
        if minimum is None or maximum is None:
            raise ParameterOutOfRangeError(
                "The device has no verified temperature range, so setpoints are refused."
            )
        if not minimum <= params.celsius <= maximum:
            raise ParameterOutOfRangeError(
                f"Target temperature must be between {minimum} and {maximum} degrees Celsius."
            )

    def _create(
        self,
        principal: Principal,
        device: Device,
        action: Action,
        params: CommandParams,
        risk: RiskClass,
        correlation_id: str,
    ) -> Command:
        now = self._clock.now()
        command = Command(
            id=uuid4(),
            device_id=device.id,
            requested_by_user_id=principal.user_id,
            requested_by_client_id=principal.client_id,
            action=action,
            parameters=params.model_dump(mode="json"),
            risk_class=risk,
            correlation_id=correlation_id,
            created_at=now,
            expires_at=now + _COMMAND_TTL,
            status=CommandStatus.PENDING,
        )
        self._remember(command)
        self._audit.record(
            AuditEntry(
                occurred_at=now,
                event="command.requested",
                actor_user_id=principal.user_id,
                actor_client_id=principal.client_id,
                device_id=device.id,
                command_id=command.id,
                action=action.value,
                risk_class=risk,
                correlation_id=correlation_id,
                context=command.parameters,
            )
        )
        self._publish(EventType.COMMAND_REQUESTED, command)
        return command

    async def _execute(self, command: Command, device: Device, params: CommandParams) -> Command:
        ref = self._devices.registry.ref_for(device.id)
        provider = self._providers.get(ref.provider)
        if provider is None:  # pragma: no cover - the container wires every provider
            return self._finish(command, CommandStatus.FAILED, "provider_unavailable")

        running = command.with_status(CommandStatus.RUNNING, at=self._clock.now())
        self._remember(running)
        self._publish(EventType.COMMAND_STARTED, running)

        provider_command = ProviderCommand(action=command.action, params=params)

        # Serialise per device so a physical device never sees overlapping writes.
        async with self._device_lock(device.id):
            try:
                async with asyncio.timeout(self._settings.command_timeout_seconds):
                    result = await provider.execute(ref, provider_command)
            except TimeoutError:
                _logger.warning(
                    "command.timeout",
                    provider=provider.name,
                    homeflow_device_id=str(device.id),
                    command_id=str(running.id),
                    correlation_id=running.correlation_id,
                )
                return await self._reconcile(running, provider, ref, params)
            except ProviderError as exc:
                _logger.warning(
                    "command.provider_error",
                    provider=provider.name,
                    homeflow_device_id=str(device.id),
                    command_id=str(running.id),
                    result_code=exc.failure_code,
                )
                return self._finish(running, CommandStatus.FAILED, exc.failure_code)
            except Exception:
                _logger.exception(
                    "command.unexpected_error",
                    provider=provider.name,
                    homeflow_device_id=str(device.id),
                    command_id=str(running.id),
                )
                return self._finish(running, CommandStatus.FAILED, "internal_error")

        if result.state is not None:
            self._devices.ingest(
                ref,
                result.state,
                source=StateSource.COMMAND_RESULT,
                correlation_id=running.correlation_id,
            )

        if result.outcome is CommandOutcome.APPLIED:
            return self._finish(running, CommandStatus.SUCCEEDED, None)
        if result.outcome is CommandOutcome.REJECTED:
            return self._finish(running, CommandStatus.FAILED, result.failure_code or "rejected")
        return self._finish(running, CommandStatus.UNKNOWN, result.failure_code)

    async def _reconcile(
        self,
        command: Command,
        provider: DeviceProvider,
        ref: ProviderDeviceRef,
        params: CommandParams,
    ) -> Command:
        """Read state back once after a timeout to decide the real outcome.

        There is no retry of the write itself: repeating a physical mutation
        after an unknown outcome is exactly what the command policy forbids.
        """
        self._publish(EventType.COMMAND_TIMED_OUT, command)
        try:
            async with asyncio.timeout(self._settings.reconcile_timeout_seconds):
                observed = await provider.get_state(ref)
        except (TimeoutError, ProviderError):
            return self._finish(command, CommandStatus.UNKNOWN, "reconciliation_failed")
        except Exception:
            _logger.exception("command.reconcile_error", command_id=str(command.id))
            return self._finish(command, CommandStatus.UNKNOWN, "reconciliation_failed")

        self._devices.ingest(
            ref,
            observed,
            source=StateSource.PROVIDER_SNAPSHOT,
            correlation_id=command.correlation_id,
        )
        if desired_matches(command.action, params, observed.state) is True:
            return self._finish(command, CommandStatus.SUCCEEDED, None)
        return self._finish(command, CommandStatus.UNKNOWN, "device_response_timeout")

    def _finish(
        self,
        command: Command,
        status: CommandStatus,
        failure_code: str | None,
    ) -> Command:
        now = self._clock.now()
        finished = command.with_status(status, at=now, failure_code=failure_code)
        self._remember(finished)
        self._audit.record(
            AuditEntry(
                occurred_at=now,
                event="command.completed",
                actor_user_id=finished.requested_by_user_id,
                actor_client_id=finished.requested_by_client_id,
                device_id=finished.device_id,
                command_id=finished.id,
                action=finished.action.value,
                risk_class=finished.risk_class,
                outcome=status.value,
                correlation_id=finished.correlation_id,
                context={"failure_code": failure_code} if failure_code else {},
            )
        )
        event_type = _COMPLETION_EVENTS.get(status)
        if event_type is not None:
            self._publish(event_type, finished)
        return finished

    def _audit_denied(
        self,
        principal: Principal,
        device: Device,
        action: Action,
        correlation_id: str,
        reason: str,
    ) -> None:
        self._audit.record(
            AuditEntry(
                occurred_at=self._clock.now(),
                event="command.denied",
                actor_user_id=principal.user_id,
                actor_client_id=principal.client_id,
                device_id=device.id,
                action=action.value,
                outcome=reason,
                correlation_id=correlation_id,
            )
        )

    def _publish(self, event_type: EventType, command: Command) -> None:
        self._bus.publish(
            DomainEvent(
                type=event_type,
                occurred_at=self._clock.now(),
                device_id=command.device_id,
                command_id=command.id,
                correlation_id=command.correlation_id,
                payload={"status": command.status.value, "action": command.action.value},
            )
        )

    def _remember(self, command: Command) -> None:
        self._commands[command.id] = command
        self._commands.move_to_end(command.id)
        while len(self._commands) > _MAX_TRACKED_COMMANDS:
            self._commands.popitem(last=False)

    def _device_lock(self, device_id: UUID) -> asyncio.Lock:
        lock = self._locks.get(device_id)
        if lock is None:
            if len(self._locks) >= _MAX_DEVICE_LOCKS:
                self._locks.popitem(last=False)
            lock = asyncio.Lock()
            self._locks[device_id] = lock
        self._locks.move_to_end(device_id)
        return lock
