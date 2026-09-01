# 0002 — Local home gateway

## Status

Accepted.

## Context

The household mixes local-only protocols with vendor clouds. A client that
talked to each vendor directly would carry every credential, would need to
implement every protocol, and would break whenever a vendor changed its API.

## Decision

Run a gateway on an always-on trusted host in the home. Clients speak only to
the gateway; the gateway speaks to devices and vendor clouds.

## Alternatives considered

**Client talks to vendors directly.** Rejected: credentials on phones, no
central authorisation or audit, no access to local-only protocols, and a
rewrite of the client for every integration change.

**Vendor cloud aggregator.** Rejected: it makes the home dependent on a third
party for local control.

## Consequences

One always-on host must be maintained and backed up, and it is a single point of
failure for remote control. In exchange, adapters can be replaced without
touching clients, and local control keeps working while a vendor cloud is down.

## Security impact

Credentials stay on one hardened host. Authorisation, audit, rate limiting and
input validation live in one place. Insecure IoT protocols never reach a phone.

## Privacy impact

Household data stays in the home; nothing needs to traverse a vendor cloud for
local control.
