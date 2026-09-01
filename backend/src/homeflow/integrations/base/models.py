"""Data exchanged between the gateway core and a provider adapter."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from homeflow.capabilities import Capability, DeviceKind
from homeflow.commands.models import Action, CommandParams
from homeflow.devices.models import Availability, DeviceConstraints, DeviceState


class ProviderDeviceRef(BaseModel):
    """Internal handle for a device at a provider.

    Never serialised to a client: the API layer maps HomeFlow UUIDs to refs and
    back (see docs/security/privacy-model.md).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    provider_device_id: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.provider, self.provider_device_id)


class ProviderDevice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: ProviderDeviceRef
    suggested_name: str
    kind: DeviceKind
    capabilities: tuple[Capability, ...]
    room_hint: str | None = None
    constraints: DeviceConstraints = DeviceConstraints()


class ProviderState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: DeviceState
    availability: Availability
    observed_at: datetime


class ProviderCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    action: Action
    params: CommandParams


class CommandOutcome(StrEnum):
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    #: The adapter cannot tell whether the device applied the command.
    UNKNOWN = "UNKNOWN"


class ProviderCommandResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: CommandOutcome
    state: ProviderState | None = None
    failure_code: str | None = None


class ProviderEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: ProviderDeviceRef
    state: ProviderState
