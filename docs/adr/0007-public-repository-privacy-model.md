# 0007 — Public repository privacy model

## Status

Accepted.

## Context

The project is developed in a public repository while controlling a real home.
Committed data is effectively permanent and is scanned by bots.

## Decision

Adopt the boundary documented in `docs/security/privacy-model.md`: code is
public, everything about the household is private. Enforce it structurally
rather than by convention — keyed identifier derivation, explicit response
models, central log redaction, a demo mode with no I/O, gitleaks in CI and
pre-commit, and tests that assert no provider identifier reaches a client.

## Alternatives considered

**Private repository.** Rejected: openness is wanted, and a private repository
would have encouraged sloppier habits that break the day it is opened.

**Convention and review only.** Rejected: one distracted commit is enough, and
history cannot be undone.

## Consequences

Fixtures, screenshots and diagrams must be synthetic, which requires demo mode
to exist as a real feature. Debugging with production payloads happens locally
and stays local.

## Security impact

An attacker who reads the repository learns the design but no secret and no
identifier. This is treated as the normal case, not the worst case.

## Privacy impact

This is the privacy control for the whole project.
