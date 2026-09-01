# 0010 — Client platform is an open decision

## Status

Proposed.

## Context

[ADR 0001](0001-native-swiftui-client.md) chose a native SwiftUI client. That
decision assumes a macOS toolchain, because Apple permits compiling and signing
iOS applications only on macOS. No Mac is planned for this project, and this
constraint is not specific to Swift: Flutter and React Native also require macOS
to produce an iOS build, so they do not work around it.

The gateway API is client-neutral, so this does not block any backend phase.

## Decision

Leave the client platform open and record the options honestly rather than
silently shipping a client that cannot be built. The gateway keeps its stable,
documented API in the meantime.

Options, with the significant trade-offs:

**A — Obtain a Mac (or a hosted macOS build machine).** Restores ADR 0001 in
full: platform integrations, LocalAuthentication with the Secure Enclave,
widgets, App Intents. Costs money, and a hosted build machine is awkward for
interactive UI work.

**B — Installable web client (PWA), added to the iPhone home screen.** Buildable
from any platform. Face ID remains reachable through WebAuthn platform
authenticators, whose challenge-and-signature model matches the high-risk action
flow this project needs, with keys held by the Secure Enclave. Gives up widgets,
Control Center controls, App Intents, Live Activities and background push on
iOS, and adds browser-facing concerns the API currently does not have — a
`Sec-WebSocket-Protocol` ticket for socket authentication, an explicit CORS
origin allowlist and CSRF considerations.

**C — Backend only for now.** The gateway is usable through its HTTP API; the
client decision is deferred until the Bestway phases prove the value.

## Alternatives considered

**Flutter or React Native.** Rejected: forbidden by the project constraints, and
they still require macOS for an iOS build, so they solve nothing here.

**Declaring option B and rewriting ADR 0001.** Rejected: the decision has not
been made yet, and a record should not be rewritten to look prescient.

## Consequences

Backend phases 2 to 5 proceed unchanged. Whichever option is chosen becomes a
new ADR that supersedes ADR 0001 if it changes it.

## Security impact

Option B moves the client into a browser security model: cookie-free bearer
credentials, an origin allowlist, CSRF review and a WebSocket authentication
mechanism that keeps credentials out of URLs. None of that is required today,
and none of it is added speculatively.

## Privacy impact

None. No option changes what data the gateway holds or exposes.
