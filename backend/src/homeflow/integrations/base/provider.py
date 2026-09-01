"""The provider protocol (see docs/architecture/overview.md)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable

from homeflow.integrations.base.models import (
    ProviderCommand,
    ProviderCommandResult,
    ProviderDevice,
    ProviderDeviceRef,
    ProviderEvent,
    ProviderState,
)


@runtime_checkable
class DeviceProvider(Protocol):
    """Adapters normalise one vendor; nothing above this line is vendor-aware.

    Adapters that cannot subscribe may poll internally and still yield events.
    Every method must apply its own I/O timeouts; the command service adds an
    outer bound but does not replace adapter-level timeouts.
    """

    @property
    def name(self) -> str:
        """Stable provider identifier, e.g. ``demo`` or ``home_assistant``."""
        ...

    async def discover_devices(self) -> Sequence[ProviderDevice]: ...

    async def get_state(self, device_ref: ProviderDeviceRef) -> ProviderState: ...

    async def execute(
        self,
        device_ref: ProviderDeviceRef,
        command: ProviderCommand,
    ) -> ProviderCommandResult: ...

    def subscribe(self) -> AsyncIterator[ProviderEvent]:
        """Yield normalised state changes until the consumer stops iterating."""
        ...
