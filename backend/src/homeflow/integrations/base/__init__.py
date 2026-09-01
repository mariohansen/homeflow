"""The contract every provider adapter implements."""

from homeflow.integrations.base.errors import (
    ProviderError,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from homeflow.integrations.base.models import (
    CommandOutcome,
    ProviderCommand,
    ProviderCommandResult,
    ProviderDevice,
    ProviderDeviceRef,
    ProviderEvent,
    ProviderState,
)
from homeflow.integrations.base.provider import DeviceProvider

__all__ = [
    "CommandOutcome",
    "DeviceProvider",
    "ProviderCommand",
    "ProviderCommandResult",
    "ProviderDevice",
    "ProviderDeviceRef",
    "ProviderError",
    "ProviderEvent",
    "ProviderRejectedError",
    "ProviderState",
    "ProviderUnavailableError",
]
