# Setting up Home Assistant for HomeFlow

HomeFlow does not talk to Hue, Sonos, tado, Miele or Ring directly. It talks to
Home Assistant, which talks to them. That keeps one set of vendor credentials in
one place and means a new lamp does not need a new HomeFlow adapter.

Home Assistant is an **integration gateway** here — not the user interface, not
the security boundary, and not the data model
(see [ADR 0004](../adr/0004-home-assistant-as-integration-gateway.md)).

All names and addresses below are fictional.

## 1. Where it runs

Same rule as the HomeFlow gateway: something that is always on. A Raspberry Pi,
a mini PC, a NAS. Home Assistant may live on the same host as HomeFlow or on a
different one; they only need to reach each other over the local network.

The three usual installations:

| Method | Good for |
| --- | --- |
| **Home Assistant OS** on a Pi or mini PC | The normal choice. Add-ons, backups and updates all work |
| **Container** (Docker Compose) | A host that already runs containers |
| **Core** in a Python environment | Not recommended; you maintain everything yourself |

Follow the official installation guide for whichever you pick — it changes often
enough that copying the steps here would only go stale.

Once it is running, open it at `http://homeassistant.local:8123`, create the
owner account, and add your integrations (Hue, Sonos, tado…). Get that working
in Home Assistant's own interface **first**. HomeFlow can only show what Home
Assistant already knows.

## 2. A separate account for HomeFlow

Do **not** give HomeFlow your owner token.

Least privilege is the point: if that credential ever leaks, it should not also
be the key to Home Assistant's own settings, add-ons and file editor.

1. **Settings → People → Add person**
2. Name it `homeflow`
3. Enable **Allow person to login**
4. Leave **Local access only** on if HomeFlow runs on the same network
5. Leave **Administrator** **off**

Then sign in to Home Assistant **as that user** (a private browser window is
easiest), and:

1. Click the user's name at the bottom of the sidebar
2. Scroll to **Long-lived access tokens**
3. **Create token**, name it `HomeFlow gateway`
4. Copy it — it is shown once

### A known limitation, stated plainly

Home Assistant's long-lived tokens are **not scoped**. A non-administrator token
cannot reach the settings, but it can still see and control every entity that
user can see. Home Assistant has no per-entity permission model, so "least
privilege" here means "not an administrator", and no more than that.

That is a real risk and it is why HomeFlow keeps the token on the gateway,
never on a phone, and why the adapter starts read-only.

## 3. Point HomeFlow at it

In the gateway's `.env` — never in the repository:

```dotenv
HOMEFLOW_HOME_ASSISTANT_ENABLED=true
HOMEFLOW_HOME_ASSISTANT_BASE_URL=http://homeassistant.example.internal:8123
HOMEFLOW_HOME_ASSISTANT_TOKEN=<the long-lived token>

# Read-only. This is where every integration starts.
HOMEFLOW_HOME_ASSISTANT_WRITE_ENABLED=
```

Restart the gateway. You should see:

```json
{"event":"providers.home_assistant_configured","released_for_writing":[]}
{"event":"home_assistant.discovered","entity_count":142,"device_count":11}
```

Neither the address nor the token is ever logged.

Open the app. Every light, socket, speaker, thermostat and door Home Assistant
knows about should now appear, in its room, showing its state — and offering
nothing to press. That is correct: **read first, verify, then write.**

### What gets imported, and what does not

| Home Assistant | HomeFlow |
| --- | --- |
| `light.*` | Light — on/off, and brightness if it can dim |
| `switch.*` | Switch — on/off |
| `media_player.*` | Media player — only the features the entity advertises |
| `climate.*` | Thermostat — measured and target temperature, with the device's own limits |
| `lock.*` | Lock — **state only**, see below |
| `sensor.*` with a temperature class | Sensor — the reading |
| everything else | not imported |

A household instance holds hundreds of entities — battery levels, update
checkers, diagnostic counters. Importing them all would bury the six things
anybody actually wants to press.

## 4. Releasing control, one domain at a time

Check the state first. Walk around and compare: is the light HomeFlow says is on
actually on? Is the thermostat reading the same number as the wall unit?

Then release one domain, restart, and try it on a real device:

```dotenv
HOMEFLOW_HOME_ASSISTANT_WRITE_ENABLED=light
```

Watch the actual lamp. If it does what the app says it did, add the next one:

```dotenv
HOMEFLOW_HOME_ASSISTANT_WRITE_ENABLED=light,switch,media_player,climate
```

Releasing a domain is what makes its controls appear at all. Until then the
capability is never advertised, so the command pipeline refuses the action and
the client cannot render a control for it — two independent gates, plus a third
inside the adapter.

### The front door is not on that list

`lock` cannot be released. Writing it into the configuration makes the gateway
refuse to start, with a message saying why.

Door control is the highest-risk thing this system will ever do. It needs the
fresh device-owner authorisation described in `SECURITY.md` — a server-issued
challenge, Face ID or a passcode on the phone immediately before the action, and
no unattended retries. None of that exists yet. Until it does, HomeFlow will
show you whether the door is locked and nothing else.

## 5. When something is wrong

| Symptom | Cause |
| --- | --- |
| `providers.home_assistant_configured` missing | `HOMEFLOW_HOME_ASSISTANT_ENABLED` is not `true` |
| `home_assistant.credential_refused` | The token is wrong, revoked, or belongs to a deleted user |
| `home_assistant.unreachable` | Wrong address or port, or the host cannot reach it |
| `device_count` is 0 | Home Assistant has no entities in the supported domains |
| Devices appear without rooms | Areas are not assigned, or the token cannot read the registries. Assign areas under **Settings → Areas** |
| A device shows state but no controls | Its domain has not been released. That is the default |
| A control does nothing and says "not taken over" | Home Assistant accepted the call and the device did not act. Check the device in Home Assistant's own interface |

## 6. What this does not do yet

- **No colour.** Lights get on/off and brightness. Colour and colour temperature
  need their own capability work.
- **No grouping.** Sonos rooms cannot be joined from HomeFlow yet.
- **No scenes or automations.** Home Assistant's own scenes are not imported.
- **No events.** Doorbell presses and motion are not surfaced yet.
- **Nothing persists.** A restart rediscovers everything from scratch, which is
  correct, but the activity log starts empty again.
