# 0012 — One-shot timers, and no automation engine

## Status

Accepted — 2026-09-02

## Context

Heating a hot tub takes hours. The vendor application offers "start in N hours"
and "heat for N hours", and without an equivalent, HomeFlow cannot meet its own
stated goal that routine use should not require opening the vendor app.

That feature is also a category change. Everything HomeFlow had done until now
happened because somebody was holding a phone and pressing a control. A timer is
the first write that reaches a physical device with nobody watching, and the
first time the gateway acts on an intention formed hours earlier — one that may
no longer hold when it fires.

The obvious generalisation is an automation engine: triggers, conditions,
actions, schedules, chains. That is a much larger thing to make safe. Loop
prevention, cooldowns, rate limits, dry runs and a global kill switch are all
required before a rule engine can be trusted with a lock or a heater, and none
of them are required for "turn the heater off in three hours".

## Decision

Implement **one-shot timers only**, as a small module, and do not build an
automation engine.

A timer is one function, one moment, one action:

- **Delayed start** — off now, on when it runs out.
- **Run for** — on immediately, off when it runs out.

The constraints that make this safe:

1. **An allowlist, not a risk class.** Only `SET_HEATER` and `SET_FILTER` may be
   timed. The check is a fixed set in `schedules/service.py`, so a future MEDIUM
   action does not silently become schedulable, and no client payload can put a
   door on a timer.
2. **Released capabilities only.** A timer is refused unless the device already
   supports the capability, which for Bestway means the operator released it
   after verifying it against the physical panel.
3. **The same pipeline.** Firing calls `CommandService.submit`, so a timer gets
   the same capability check, device-declared bounds, risk classification,
   timeout, reconciliation and audit record as a tap. There is no second path.
4. **Once.** A timer that fails is recorded and settles. Nothing is retried:
   repeating an unattended physical write is exactly what the command policy
   forbids, because the device may have acted after the gateway gave up.
5. **The unattended half reduces activity.** "Run for" starts the function
   immediately, while the user is present, and arms only the stop. If the start
   does not take, nothing is armed at all — an "off" for a function the user
   never managed to start would be a surprise rather than a convenience.
6. **One timer per function.** A second one supersedes the first, so two
   unattended writes can never race for the same switch.
7. **Bounded.** Between half an hour and `HOMEFLOW_SCHEDULE_MAX_HOURS` (24 by
   default), expressed as a delay rather than an absolute time, so a client with
   a wrong clock cannot schedule a write into next week.
8. **Cancellable, and visible.** Every armed timer appears on the device card
   with a countdown and a cancel button, and every transition is audited.

Timers live in memory. A gateway restart forgets them, which is the honest
behaviour while there is no database (see
[0009](0009-deferred-persistence.md)) — the alternative, a timer that survives
a restart but whose owner has forgotten it exists, is worse.

## Alternatives considered

**A general automation engine.** Rejected for now. It is the right long-term
shape for scenes and cross-vendor rules, but it needs loop prevention, cooldown,
execution-rate limits, a dry-run mode and a global disable before it can be
trusted with physical devices. None of that is needed for a hot tub timer, and
building it now would mean shipping an unsafe subset of it.

**Absolute times ("start at 18:00").** Rejected for the first version. A delay
cannot be misread across time zones, cannot be affected by a wrong clock on the
phone, and is what the vendor app trained the household to expect. Absolute
times can be added later on top of the same one-shot record.

**Repeating schedules ("filter daily at 09:00").** Rejected. A repeating rule
that nobody reviews is the failure mode this ADR exists to avoid. A daily filter
cycle is a genuine future feature, but it needs the durable storage and the
review surface that do not exist yet.

**Client-side timers.** Rejected outright. A phone that is asleep, offline or
uninstalled would silently drop the second half of "heat for three hours",
leaving a heater running. The gateway is the only component that is supposed to
be always on.

## Consequences

- The tub can be heated on a schedule without the vendor app, which was the
  point.
- Timers are lost on restart, and the interface says so by simply not showing
  them. An operator restarting the gateway mid-timer must re-arm it.
- The worst-case lateness of a timer is one tick of the scheduler loop
  (`HOMEFLOW_SCHEDULE_TICK_SECONDS`, 20 seconds by default).
- Adding a schedulable function later is a deliberate edit to `TIMED_ACTIONS`
  with a superseding ADR, not a configuration change.

## Security impact

This introduces the first unattended physical write, which is a real increase in
what a stolen credential can do — but a bounded one: the same credential could
already turn the heater on directly, and a timer cannot reach any action that a
tap could not. The allowlist means the highest-risk actions in the system
(unlock, unlatch) remain structurally unreachable from a timer even if the
policy table were later changed to consider them MEDIUM.

Every arming, cancellation, supersession and firing produces an audit entry
attributed to the user and client that created it, so an unexpected physical
change can be traced to the request that scheduled it.

## Privacy impact

None beyond what commands already record. A timer stores HomeFlow identifiers,
the action, and the moment it is due. Timers are in-memory and never leave the
gateway.
