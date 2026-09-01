# 0004 — Home Assistant as an integration gateway

## Status

Accepted.

## Context

Home Assistant already maintains working integrations for Hue, Sonos, Nuki,
tado°, Ring, Miele and Alexa. Reimplementing each vendor protocol would consume
the entire project budget and would need continuous maintenance.

## Decision

Use Home Assistant as one provider adapter behind the HomeFlow gateway, over its
authenticated REST and WebSocket APIs. Home Assistant is not the API clients
see, not the data model, not the user interface, and not the security boundary
for HomeFlow users.

## Alternatives considered

**Direct vendor integrations for everything.** Rejected for now: high effort,
high maintenance, and it delays the practical goal. Direct adapters remain an
option where they materially improve a feature, such as Miele's official API.

**Home Assistant as the user interface.** Rejected: it does not deliver the
product goal of one coherent, native-feeling household interface.

## Consequences

Fast coverage of many vendors and a single integration to maintain, at the cost
of a dependency whose restarts must be tolerated. Adapters that matter most,
such as Bestway, stay independent of it so that a Home Assistant restart cannot
take pool control down.

## Security impact

The Home Assistant token stays on the gateway and never reaches a client. Home
Assistant is not exposed publicly. Where least privilege is not achievable, the
limitation is documented in the threat model rather than ignored.

## Privacy impact

Home Assistant entity ids often encode real room and person names. They are
treated as internal metadata and are never exposed as public identifiers or
written to logs.
