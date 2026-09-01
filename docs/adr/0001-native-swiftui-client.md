# 0001 — Native SwiftUI client

## Status

Superseded by [ADR 0011](0011-installable-web-client.md).

The reasoning below still holds for a native client; it is the macOS toolchain
requirement, not the design, that made it unavailable.

## Context

HomeFlow is meant for daily use on a phone. Controls must feel immediate, state
must be visible at a glance, and high-risk actions need platform-level
device-owner authentication. Later goals include widgets, Control Center
controls, App Intents, Live Activities and a watch app.

## Decision

Build the client natively in Swift and SwiftUI, using Swift Concurrency,
URLSession and `URLSessionWebSocketTask`, the platform keystore for credentials,
and LocalAuthentication for sensitive confirmations.

## Alternatives considered

**React Native or Flutter.** Rejected: they add a large dependency surface, give
weaker access to platform-integration features, and — decisively — still require
macOS to compile and sign an iOS build, so they solve no toolchain problem.

**Web application.** Rejected at the time for weaker platform integration.
Reconsidered in ADR 0010.

## Consequences

The client is limited to Apple platforms and requires a macOS toolchain to
build. Platform integrations become straightforward. The gateway API stays
client-neutral, so this decision does not constrain the backend.

## Security impact

LocalAuthentication and the Secure Enclave make a device-bound credential and a
genuine device-owner check possible, which the high-risk action flow depends on.

## Privacy impact

No household data is embedded in the client; it holds only a revocable client
credential.
