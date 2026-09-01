# Security policy

HomeFlow controls a real home: a door lock, a heated pool, appliances and
cameras. A defect here has physical consequences, so security is treated as a
functional requirement rather than a later hardening pass.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through GitHub's
**Report a vulnerability** function on the Security tab of this repository. Do
not open a public issue, and do not include real household data, tokens, logs or
screenshots in a report.

Please include:

- what an attacker could achieve;
- the affected component or endpoint;
- reproduction steps using synthetic data;
- the commit you tested.

## Scope

In scope:

- authentication and authorisation of the gateway API;
- the command pipeline, risk classification and parameter validation;
- the WebSocket stream;
- provider adapters and their input validation;
- log and error redaction;
- configuration handling and fail-closed startup checks;
- CI configuration that could expose secrets.

Out of scope:

- vulnerabilities in third-party vendor clouds or devices themselves (report
  those to the vendor);
- attacks that require physical access to the gateway host;
- issues that depend on a deployment ignoring the documented network model.

## Design commitments

These properties are deliberate. A change that weakens one is a security change
and needs an ADR.

**No public ingress.** HomeFlow is never port-forwarded and never exposed
through Tailscale Funnel. Remote access is a private encrypted overlay between
trusted devices. The API binds to loopback unless a deployment explicitly binds
it to a private interface.

**Network membership is not authorisation.** Every `/v1` route resolves a
registered client from an `Authorization` header. Credentials are stored as
SHA-256 digests, compared in constant time, and can be revoked per client.

**Fail closed.** Startup refuses a production configuration that carries a
development credential, enables demo mode, allows a wildcard `Host`, or omits
the identifier salt. There is no flag that disables authentication.

**Capabilities authorise, kinds only describe.** A command is refused unless the
target device declares the required capability. Unlocking requires the `UNLOCK`
capability, not merely `LOCK`.

**Risk classes gate high-risk actions.** Unlocking a door and unlatching are
always `HIGH` and are currently refused outright, because the fresh
device-owner authorisation flow does not exist yet. A client-supplied boolean
will never be accepted as proof of biometric approval.

**Device limits belong to the device.** Setpoints are validated against
adapter-declared constraints derived from verified hardware behaviour. A device
with no verified range refuses setpoints instead of guessing.

**Timeouts are not failures.** After a write times out, the gateway reads state
back once and reports `SUCCEEDED` or `UNKNOWN`. It never repeats a physical
write automatically.

**WebSocket credentials are short-lived.** A browser cannot set an
`Authorization` header on a WebSocket handshake, and a credential in a URL would
end up in logs. The client therefore exchanges its credential over the
authenticated HTTP API for a ticket that is single-use, expires in 30 seconds,
is stored hashed and is compared in constant time.

**No provider passthrough.** There is no endpoint that forwards a raw request to
Home Assistant, Nuki or a device. Only semantic HomeFlow actions exist, so the
API cannot be used as a proxy into the home network.

**Authentication throttling counts failures.** Guessing a credential produces
failed attempts and is throttled per peer; a client that reloads or reconnects
produces successful ones and is not, because locking a household out of its own
home is its own kind of failure.

**Bounded resources.** Explicit timeouts on external I/O, bounded event queues
with an explicit resync signal, per-client rate limits, per-device command
serialisation, and bounded retention of commands and audit entries.

**Nothing sensitive leaves through errors or logs.** Problem details carry a
stable type, an authored message and a correlation id. Logging passes every
field through central redaction, which is covered by unit tests.

**Provider identifiers are not public identifiers.** Public device UUIDs are
derived with a keyed HMAC from a deployment secret, so they are stable across
restarts and cannot be reversed to a Home Assistant entity id.

## Secrets

`git` is not a secret store. Runtime configuration lives in an untracked `.env`
or an equivalent protected store. `.env.example` contains placeholders only.
Secrets are never printed at startup, never passed as command-line arguments and
never logged.

If a real secret is ever committed: treat it as compromised, rotate it first,
then remove it. Deleting a commit does not restore secrecy.

## Known gaps

Tracked openly rather than implied to be solved:

- **Client registration is not implemented.** Phase 1 authenticates a single
  development credential from the environment. Production has no registered
  clients and therefore rejects every request until a client registration flow
  exists.
- **No fresh action authorisation.** `HIGH`-risk actions are refused rather than
  challenged. Nuki integration is blocked on this by design.
- **Audit is in memory.** Audit entries do not survive a restart until the
  persistence work in [ADR 0009](docs/adr/0009-deferred-persistence.md).
- **No transport encryption inside the gateway host.** TLS is expected to be
  terminated by the private overlay (for example Tailscale Serve).
- **No network segmentation yet.** IoT devices share the LAN; documented in the
  threat model rather than papered over.
- **The client credential lives in browser storage.** A downgrade from a
  platform keystore, accepted in [ADR 0011](docs/adr/0011-installable-web-client.md)
  and mitigated by a strict Content Security Policy with no inline or
  third-party code, same-origin only, and no `eval`. An XSS in the client would
  expose the credential, which is why no third-party script is permitted.
