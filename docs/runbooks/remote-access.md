# Running the gateway for daily use, and reaching it from a phone

Two separate problems, often confused:

| Problem | Solved by |
| --- | --- |
| The gateway is **running** when you want the pool | An always-on host |
| You can **reach** it from a phone, anywhere | A private encrypted overlay |

Tailscale only solves the second. A laptop that sleeps takes the pool with it no
matter how good the network is.

All hostnames and addresses below are fictional.

## 1. Where the gateway runs

### The developer laptop

Fine for building, not for living with. It sleeps, it reboots, it travels. The
project assumes it is not always available.

If you want to try remote access before buying anything, it works — just expect
the pool to be unreachable whenever the laptop is closed.

### An always-on host

The intended shape. A Raspberry Pi 4 or 5, a small x86 mini PC, or a NAS that
runs containers. A Pi 5 draws a few watts and costs less than a winter of
heating the tub at the wrong time.

What it needs:

- a wired or reliable wireless connection to the same network as the controller,
- Docker, or Python 3.13 and `uv`,
- Tailscale.

The controller must be reachable from that host on TCP 12416. Check with the
probe before moving anything:

```bash
python scripts/bestway_probe.py --host 192.0.2.10
```

### Deploying with Compose

`compose.yaml` publishes the API on loopback only and builds an image that
carries the web client. On the gateway host:

```bash
git clone <your repository> homeflow
cd homeflow
cp .env.example .env      # then fill it in, see below
docker compose up -d api
```

The PostgreSQL service in the file is for a later phase; the gateway does not
use it yet.

## 2. Reaching it from a phone

### What Tailscale does

It builds a private encrypted network between the devices you enrol — the
gateway host, your phone, your laptop. Each gets a stable address inside that
network and nothing is exposed to the internet. No port forward, no public
ingress, which is what this project requires.

**Never enable Funnel.** Funnel is the public option and would put a hot tub and
eventually a door lock on the open internet.

### Setting it up

1. Install Tailscale on the gateway host and sign in.
2. Install Tailscale on the phone and sign in with the same account.
3. In the Tailscale admin console, enable **MagicDNS** and **HTTPS
   certificates** for the tailnet.

Now pick a hostname for the gateway host. Enabling HTTPS certificates publishes
that name to public certificate transparency logs, so it must not contain a
resident's name, the address, or anything else identifying. Something flat like
`hub` is right; your surname is not.

### HTTPS inside the tailnet

The phone needs HTTPS for two reasons: iOS only offers "Add to Home Screen" as a
proper app on a secure origin, and the live update socket has to be `wss://`.

Tailscale Serve terminates TLS on the host and proxies to the gateway:

```bash
tailscale serve --bg http://127.0.0.1:8000
tailscale serve status
```

The exact flags have changed between Tailscale versions; check
`tailscale serve --help` on the version you have. What matters is the shape:
**Serve listens on the tailnet, the gateway listens only on loopback.**

That is the reason to leave `HOMEFLOW_API_HOST=127.0.0.1`. The gateway never
listens on a network interface at all; the only way in is through Serve, which
only answers devices in your tailnet.

### Configuration on the gateway host

```dotenv
HOMEFLOW_ENV=development
HOMEFLOW_DEMO_MODE=false

# Serve proxies to loopback, so the gateway needs no network interface.
HOMEFLOW_API_HOST=127.0.0.1
HOMEFLOW_API_PORT=8000

# The Host header Serve forwards. A wildcard is refused in production because a
# permissive Host check is what makes DNS rebinding work.
HOMEFLOW_ALLOWED_HOSTS=hub.example-tailnet.ts.net,127.0.0.1,localhost

HOMEFLOW_ID_SALT=<generate with scripts/generate_secret.py>
HOMEFLOW_DEV_CLIENT_TOKEN=<generate with scripts/generate_client_token.py>

HOMEFLOW_BESTWAY_ENABLED=true
HOMEFLOW_BESTWAY_HOST=192.0.2.10
HOMEFLOW_BESTWAY_PROFILE=airjet-19byte
HOMEFLOW_BESTWAY_TRUST_PROFILE=true
HOMEFLOW_BESTWAY_WRITE_ENABLED=BUBBLES,FILTER_PUMP,HEATER,TARGET_TEMPERATURE
```

Generate a **new** identifier salt and a **new** credential for the new host
rather than copying them from the laptop. The salt changes the public device
identifiers, so the phone will simply see the pool as a new device once.

### On the phone

1. Open `https://hub.example-tailnet.ts.net` in Safari.
2. Paste the access token once.
3. Share → **Add to Home Screen**.

It now opens like an app, works from anywhere your phone has a connection, and
never touches the public internet.

## 3. What is not solved yet

**The credential is a development token.** `HOMEFLOW_ENV=production` refuses it
on purpose, because production is meant to use registered clients, and client
registration is not implemented. So a real deployment currently runs with
`HOMEFLOW_ENV=development`, which also leaves the interactive API documentation
enabled at `/docs`.

Inside a tailnet, with a credential only you hold, that is a defensible place to
be for a pool. It is **not** a defensible place to be before a door lock is
added: that is exactly why the Nuki phase is gated behind client registration
and fresh device-owner authorisation. See the known gaps in `SECURITY.md`.

**Nothing survives a restart yet.** The audit trail and command history live in
memory. Device state is rebuilt from the controller at startup, which is correct
anyway, but a restart loses the activity log.

**Backups.** Once there is a database, back it up and encrypt it. Until then the
only thing worth keeping is `.env`, and it belongs nowhere near the repository.

## 4. Checking it works

From the phone, on mobile data with Wi-Fi off:

```text
https://hub.example-tailnet.ts.net
```

The pool should show the current water temperature and respond to a control. If
it does not:

| Symptom | Cause |
| --- | --- |
| Browser cannot connect | Tailscale is not connected on the phone, or Serve is not running |
| "Invalid host header" | The tailnet name is missing from `HOMEFLOW_ALLOWED_HOSTS` |
| The page loads but stays on sign-in | Wrong token for this host; the new host has its own |
| Pool missing entirely | The gateway host cannot reach the controller on 12416 |
| "Getrennt" in the corner | The socket cannot be established; check that Serve proxies WebSocket traffic |
