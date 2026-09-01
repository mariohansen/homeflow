# 0006 — Direct local Bestway adapter

## Status

Accepted.

## Context

The Bestway AirJet is the strongest everyday driver of this project: routine use
should not require the vendor app. Some AirJet controllers expose the Gizwits
GAgent LAN protocol on TCP port 12416. The datapoint layout is product and
firmware specific and is not documented by the vendor.

## Decision

Implement a dedicated local adapter that speaks the Gizwits LAN protocol
directly, independent of Home Assistant, so pool control has its own failure
domain. Implement it strictly read-only first and enable writes one capability
at a time, only after decoded state has been compared against the physical
controller.

## Alternatives considered

**Bestway cloud API.** Rejected: unnecessary for local control and adds a cloud
dependency and another account to protect.

**Via Home Assistant.** Rejected as the primary path: it would couple the most
used feature to a component whose restarts would interrupt it.

**Reusing an existing implementation verbatim.** Rejected: licence and
provenance must be checked, and a clean implementation against documented
protocol behaviour is preferred. `tubctl` is used as a protocol reference only.

## Consequences

More protocol work and a mandatory hardware verification step before each write
capability. In exchange, pool control is local, fast and independent.

## Security impact

The protocol is plaintext and unauthenticated on the LAN. Only the gateway may
speak to the device; port 12416 is never exposed to clients or the overlay.
Frames are size- and type-validated with bounded reads and timeouts, and the API
exposes semantic commands only — there is no raw command passthrough.

## Privacy impact

Device identifiers and product keys are household-identifying and are treated as
secrets: never committed, never logged.
