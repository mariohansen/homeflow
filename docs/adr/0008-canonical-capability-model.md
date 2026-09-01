# 0008 — Canonical capability model

## Status

Accepted.

## Context

Devices from different vendors overlap only partially. Without a shared
vocabulary the client fills with vendor conditionals, and features get rendered
that a given device cannot actually perform.

## Decision

Model every device as a kind plus a set of capabilities and a normalised state.
Kinds guide presentation; **capabilities authorise**. A command is refused unless
the device declares the capability it requires, and per-device constraints such
as a temperature range are published by the adapter from verified device
behaviour.

## Alternatives considered

**Vendor-shaped models.** Rejected: it pushes provider complexity into the
client, which is exactly what the gateway exists to prevent.

**One flat state blob.** Rejected: untyped state cannot be validated, cannot be
reconciled after a timeout, and cannot authorise anything.

## Consequences

Adding a vendor means mapping to the existing vocabulary and extending it only
when a genuinely new capability appears. Unsupported controls are absent from
the client instead of failing when tapped.

## Security impact

Capabilities are the authorisation surface. Unlocking requires an explicit
`UNLOCK` capability rather than a generic lock capability, so a mislabelled
device cannot be talked into opening a door. Adapter-declared constraints keep
setpoint validation tied to verified hardware limits.

## Privacy impact

The canonical model carries no provider identifiers, which is what allows the
public API to be free of household-identifying data.
