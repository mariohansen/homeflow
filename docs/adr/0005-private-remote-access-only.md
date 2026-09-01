# 0005 — Private remote access only

## Status

Accepted.

## Context

Remote control is a core requirement. The system controls a door lock, so any
publicly reachable control endpoint is an unacceptable risk: it would be found
by scanners within hours.

## Decision

Remote access uses a private encrypted overlay such as Tailscale or WireGuard
between trusted devices and the gateway. No router port-forward, no UPnP-exposed
service, no Tailscale Funnel. Tailscale Serve may provide HTTPS inside the
tailnet. Exposing anything publicly requires explicit human approval.

## Alternatives considered

**Reverse proxy with TLS and a strong password.** Rejected: it puts door control
one authentication bug away from the public internet.

**Vendor cloud relay.** Rejected: it makes local control depend on a third party
and widens the trust boundary.

## Consequences

Household devices must join the overlay, which is a one-time setup step per
device. There is no browser-based access from an arbitrary network, which is
accepted.

## Security impact

Removes the entire class of opportunistic internet attacks against the gateway.
Overlay membership is still not treated as authorisation.

## Privacy impact

Enabling overlay HTTPS certificates publishes hostnames to certificate
transparency logs, so hostnames must not encode resident names or the address,
and real hostnames are never documented publicly.
