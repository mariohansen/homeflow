"""In-memory mapping between HomeFlow devices and provider handles.

The registry is the only place that knows a provider identifier for a device.
Nothing it returns to callers carries that identifier, which is what keeps
the privacy model enforceable rather than aspirational.

Single event loop, no threads: no locking is required.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from homeflow.devices.identity import device_uuid, room_uuid
from homeflow.devices.models import Availability, Device, DeviceState, Room, StateSource
from homeflow.errors import DeviceNotFoundError
from homeflow.integrations.base.models import ProviderDevice, ProviderDeviceRef, ProviderState


class DeviceRegistry:
    __slots__ = ("_devices", "_id_by_ref", "_id_salt", "_refs")

    def __init__(self, id_salt: str) -> None:
        self._id_salt = id_salt
        self._devices: dict[UUID, Device] = {}
        self._refs: dict[UUID, ProviderDeviceRef] = {}
        self._id_by_ref: dict[tuple[str, str], UUID] = {}

    def register(self, provider_device: ProviderDevice, state: ProviderState) -> Device:
        ref = provider_device.ref
        identifier = device_uuid(self._id_salt, ref.provider, ref.provider_device_id)
        room_name = provider_device.room_hint
        device = Device(
            id=identifier,
            display_name=provider_device.suggested_name,
            kind=provider_device.kind,
            room_id=room_uuid(self._id_salt, room_name) if room_name else None,
            room_name=room_name,
            capabilities=provider_device.capabilities,
            availability=state.availability,
            state=state.state,
            constraints=provider_device.constraints,
            state_observed_at=state.observed_at,
            last_seen_at=state.observed_at,
            state_source=StateSource.PROVIDER_SNAPSHOT,
        )
        self._devices[identifier] = device
        self._refs[identifier] = ref
        self._id_by_ref[ref.key] = identifier
        return device

    def list_devices(self) -> list[Device]:
        return sorted(self._devices.values(), key=lambda device: device.display_name)

    def get(self, device_id: UUID) -> Device | None:
        return self._devices.get(device_id)

    def require(self, device_id: UUID) -> Device:
        device = self._devices.get(device_id)
        if device is None:
            raise DeviceNotFoundError
        return device

    def ref_for(self, device_id: UUID) -> ProviderDeviceRef:
        ref = self._refs.get(device_id)
        if ref is None:
            raise DeviceNotFoundError
        return ref

    def id_for_ref(self, ref: ProviderDeviceRef) -> UUID | None:
        return self._id_by_ref.get(ref.key)

    def rooms(self) -> list[Room]:
        seen: dict[UUID, Room] = {}
        for device in self._devices.values():
            if device.room_id is not None and device.room_name is not None:
                seen.setdefault(device.room_id, Room(id=device.room_id, name=device.room_name))
        return sorted(seen.values(), key=lambda room: room.name)

    def apply(
        self,
        device_id: UUID,
        *,
        state: DeviceState,
        availability: Availability,
        observed_at: datetime,
        source: StateSource,
    ) -> Device:
        """Merge reported state into the held device and return the new value.

        Freshness timestamps only advance while the device is reachable, so an
        offline device keeps showing when its state was last genuinely observed
        instead of pretending to be current (see docs/architecture/overview.md).
        """
        current = self.require(device_id)
        reachable = availability is Availability.ONLINE
        updated = current.model_copy(
            update={
                "state": current.state.merged_with(state),
                "availability": availability,
                "state_observed_at": observed_at if reachable else current.state_observed_at,
                "last_seen_at": observed_at if reachable else current.last_seen_at,
                "state_source": source,
            }
        )
        self._devices[device_id] = updated
        return updated
