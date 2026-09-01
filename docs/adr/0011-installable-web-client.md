# 0011 — Installable web client

## Status

Accepted. Supersedes [ADR 0001](0001-native-swiftui-client.md) and resolves
[ADR 0010](0010-client-platform-open.md).

## Context

ADR 0001 chose a native SwiftUI client. Apple permits compiling and signing iOS
applications only on macOS, and no Mac is planned. Flutter and React Native do
not work around this: they also require macOS for an iOS build. ADR 0010
recorded the options; option B is chosen.

## Decision

Build an installable web client, added to the iPhone home screen, and serve it
from the gateway itself on the same origin.

Consequences of same-origin serving, which is the reason for choosing it:

- no CORS configuration and therefore no cross-origin attack surface;
- the WebSocket is same-origin as well;
- one artefact to deploy, one place to secure.

The client is written as plain ES modules and hand-written CSS with no bundler
and no framework. For an application of this size that removes an entire
dependency tree from a project that controls a door lock, and it keeps the
Content Security Policy strict: no inline script, no inline style, no external
origin.

Browsers cannot set headers on a WebSocket handshake, so the credential cannot
travel in an `Authorization` header there, and the security policy rules out
query-string tokens. The client therefore requests a **single-use ticket valid
for 30 seconds** over the authenticated HTTP API and presents it through
`Sec-WebSocket-Protocol`. Native clients continue to use the header directly.

## Alternatives considered

**Obtain a Mac.** Still the better client long term. Rejected for now on cost;
the gateway API stays client-neutral, so a native client can be added later
without backend changes.

**Flutter or React Native.** Rejected: forbidden by the project constraints and
they solve no toolchain problem.

**A bundled framework (React, Svelte, Vite).** Rejected: a build step and a
dependency tree for a handful of screens, on a project whose dependency policy
asks what each dependency actually buys.

**Token in the WebSocket URL.** Rejected: URLs get logged by proxies and
servers.

## Consequences

Lost, compared with a native client: widgets, Control Center controls, App
Intents and Siri, Live Activities, a watch app, and reliable background push on
iOS. Those roadmap items move behind a future native client.

Kept: a genuinely usable home-screen application, offline shell caching, and —
importantly — the ability to develop and ship it from the machine that exists.

## Security impact

**Fresh device-owner authentication remains achievable.** WebAuthn platform
authenticators on iOS use Face ID with keys held in the Secure Enclave, and
their challenge-and-signature model is the same shape as the flow SECURITY.md
describes. The high-risk action flow is therefore not blocked by this
decision. It is not implemented yet, and high-risk actions stay refused until it
is.

**The credential lives in browser storage rather than a platform keystore.**
This is a real downgrade from the iOS Keychain and is accepted with mitigations:
a strict CSP with no inline or third-party code, same-origin only, no `eval`,
and per-client revocation once client registration exists. An XSS in the client
would expose the credential, which is why no third-party script is permitted.

**Ticket-based WebSocket authentication** narrows exposure: a ticket is
single-use, expires in 30 seconds, is stored hashed, and is redeemed in constant
time.

## Privacy impact

None on the gateway. The client renders only what the canonical API returns, so
it cannot display or store a provider identifier. Demo mode remains the source
for screenshots.
