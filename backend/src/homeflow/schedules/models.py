"""One-shot timers.

A timer is the only thing in HomeFlow that touches a device while nobody is
looking, so it is deliberately the smallest useful shape: one function, one
moment, one action, and then it is over. There is no repetition, no recurrence
rule and no chain of steps, because each of those turns an unattended physical
write into something that has to be reasoned about rather than read.

Everything a timer can do, a person could already do by tapping the same
control. What a timer changes is *when*, not *what* -- it fires through the
ordinary command pipeline with the same capability check, the same bounds and
the same audit trail (see docs/adr/0012-one-shot-timers.md).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from homeflow.commands.models import Action


class ScheduleKind(StrEnum):
    #: Off now, on when the timer runs out. "Start in three hours."
    DELAYED_START = "DELAYED_START"
    #: On now, off when the timer runs out. "Heat for three hours."
    RUN_FOR = "RUN_FOR"


class ScheduleStatus(StrEnum):
    ARMED = "ARMED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    #: Replaced by a newer timer for the same function.
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"
    #: The timer fired and the device never confirmed. As with commands, this is
    #: not reported as a failure: a physical device can act after we gave up.
    UNKNOWN = "UNKNOWN"


#: Terminal states. A timer in any of these never fires again.
SETTLED = frozenset(
    {
        ScheduleStatus.COMPLETED,
        ScheduleStatus.CANCELLED,
        ScheduleStatus.SUPERSEDED,
        ScheduleStatus.FAILED,
        ScheduleStatus.UNKNOWN,
    }
)


class Schedule(BaseModel):
    """Immutable timer record; transitions create a new instance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    device_id: UUID
    kind: ScheduleKind
    action: Action
    #: What will be applied when the timer runs out.
    desired: bool
    requested_by_user_id: UUID
    requested_by_client_id: UUID
    requested_by_display_name: str
    created_at: datetime
    due_at: datetime
    correlation_id: str
    status: ScheduleStatus = ScheduleStatus.ARMED
    failure_code: str | None = None
    command_id: UUID | None = None

    def settled(
        self,
        status: ScheduleStatus,
        *,
        failure_code: str | None = None,
        command_id: UUID | None = None,
    ) -> Schedule:
        return self.model_copy(
            update={"status": status, "failure_code": failure_code, "command_id": command_id}
        )
