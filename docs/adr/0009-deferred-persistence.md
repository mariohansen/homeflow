# 0009 — Deferred persistence for the first vertical slice

## Status

Accepted.

## Context

Phase 1 proves the architecture end to end with a synthetic household. The
target stack names PostgreSQL as the durable store, but the development machine
for this phase has no container runtime, so a database layer written now could
not be executed or tested here.

## Decision

Keep the device registry, command history and audit trail in bounded in-memory
structures for the first slice. Define the audit trail behind an `AuditSink`
protocol so a PostgreSQL implementation drops in without touching call sites,
and ship the PostgreSQL service in `compose.yaml` ready for phase 2.

Public device identifiers are derived from a keyed HMAC over the provider
identifier rather than stored, so they stay stable across restarts without a
database.

## Alternatives considered

**Write the SQLAlchemy and Alembic layer now.** Rejected: it could not be run or
tested in this environment, and untested persistence code that touches audit
records is worse than none.

**SQLite as an interim store.** Rejected: it would add a schema and migrations
that would be discarded when PostgreSQL arrives.

## Consequences

Command history and audit entries do not survive a restart. Device state is
rebuilt from adapters at startup, which is correct behaviour anyway because
physical state is authoritative. Persistence is the first task of phase 2.

## Security impact

The audit trail is not durable yet, so it cannot support an investigation across
a restart. This is listed as a known gap in `SECURITY.md` rather than implied to
be solved. No authorisation decision depends on persistence.

## Privacy impact

Less data is retained, and nothing is written to disk that could later be
committed by accident.
