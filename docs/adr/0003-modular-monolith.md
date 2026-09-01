# 0003 — Modular monolith

## Status

Accepted.

## Context

The system serves one household and a handful of users. It must be easy to
deploy, back up and debug on a small home server.

## Decision

One deployable service with firm internal module boundaries: API, auth, domain,
commands, events, adapters, persistence and audit. Extraction into a separate
service requires a documented requirement and its own ADR.

## Alternatives considered

**Microservices.** Rejected: distributed transactions, more network trust
boundaries, and far more operational burden for no benefit at this scale.

**Unstructured single module.** Rejected: boundaries are what make an adapter
replaceable and make the security-relevant code reviewable in isolation.

## Consequences

Simple deployment and debugging. Module discipline has to be maintained by
review rather than by process isolation.

## Security impact

Fewer network boundaries means less exposed surface. All authorisation happens
in one process, so it cannot be bypassed by reaching a different service.

## Privacy impact

None beyond keeping household data in one auditable place.
