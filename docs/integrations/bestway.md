# Bestway AirJet

A local adapter for an AirJet controller that exposes the Gizwits GAgent LAN
protocol on TCP 12416. No vendor cloud is involved, and the adapter has its own
failure domain, so pool control keeps working when anything else is down.

All addresses in this document are RFC 5737 documentation addresses.

## The safety model

The byte layout of a status frame is product and firmware specific, and the
vendor does not document it. Writing to an offset that means something else on
your controller is how a hot tub gets told to do the wrong thing. So the layout
is treated as a claim that has to be proven, and there are **two independent
gates**:

| Gate | Setting | Until it is set |
| --- | --- | --- |
| The layout is correct | `HOMEFLOW_BESTWAY_TRUST_PROFILE` | The controller is not exposed as a device at all |
| This capability is safe to write | `HOMEFLOW_BESTWAY_WRITE_ENABLED` | The capability is not advertised and every command is refused |

Both default to refusing. Configuration that releases a write without the first
gate does not start:

```text
HOMEFLOW_BESTWAY_WRITE_ENABLED requires HOMEFLOW_BESTWAY_TRUST_PROFILE
```

Two further properties hold regardless of configuration:

- **Nothing is written blind.** A write is expressed as the status block the
  controller reported moments earlier with exactly one field changed, so the
  controller never receives a value this gateway has not seen it report.
- **A write is verified.** After writing, the adapter reads the state back. If
  the controller does not confirm the new value the command settles as
  `UNKNOWN`, never as success.

## What is verified and what is not

| Part | Status |
| --- | --- |
| Frame layout (magic, varint length, flag, command, payload) | From the published protocol description, confirmed against a physical controller |
| Command numbers, passcode and login handshake | Confirmed against a physical controller |
| Datapoint offsets in the status block | Product specific. **This is what you verify.** |
| Temperature range 20–40 °C | A placeholder until your controller's real range is read off the panel |

## Choosing a layout

Two layouts ship, and **both are `trusted: false`**. A layout is a claim about
your hardware, so every deployment confirms it against its own panel.

| Layout | Status block | Origin |
| --- | --- | --- |
| `airjet-candidate` | 12 bytes | Community documentation, unconfirmed |
| `airjet-19byte` | 19 bytes | Worked out on a physical controller, one button at a time |

Run the probe first and look at the reported length — that tells you which one
to start from. If neither fits, work out your own with watch mode.

### What `airjet-19byte` maps

| Where | Meaning |
| --- | --- |
| byte 0 | Message type, flips between request and report. **Not device state.** |
| byte 1, bit 1 | Heater |
| byte 1, bit 2 | Filter pump |
| byte 1, bit 3 | Bubbles |
| byte 1, bit 4 | Control panel lock |
| byte 2 | Target temperature |
| byte 15 | Current temperature |

The heater and the pump are separate bits, which matters: switching the heater
on sets both, switching it off clears only the heater bit while the pump keeps
running. A layout that conflated them would report a running pump as stopped.

Two things are deliberately not mapped:

- **The temperature unit flag was not identified.** Values are read as Celsius.
  A controller switched to Fahrenheit would report wrongly, so confirm the unit
  on your panel and leave it there.
- **Bits 0 and 6 of byte 1 were set throughout** and remain unexplained.

## Verifying your controller

Everything in this section is read-only. The probe never writes.

### 1. Find the controller and check the port

The controller must be reachable from the gateway host on TCP 12416. It should
**not** be reachable from anywhere else; a firewall rule limiting port 12416 to
the gateway is the right long-term shape, and network segmentation is better
still.

### 2. Take a snapshot

```bash
python scripts/bestway_probe.py --host 192.0.2.10
```

You get the raw status block in hex and binary, plus what the current layout
claims those bytes mean:

```text
status payload (12 bytes)
  [ 0] 01 00 00 00 02 26 18 00  00000001 ... 00000010 00100110 00011000 ...

decoded with the current layout
  CURRENT_TEMPERATURE    24.0 C  (raw 24)       from byte 6
  TARGET_TEMPERATURE     38.0 C  (raw 38)       from byte 5
  HEATER                 off                    from byte 4 bit 2
  ...
```

If the probe cannot reach the controller, or the payload is much shorter than
the layout expects, stop here: the protocol assumptions do not hold for your
device and the command numbers are the first thing to re-check.

### 3. Compare every line with the physical panel

Read the actual control panel and check each value:

- current water temperature,
- target temperature,
- heater on or off,
- filter pump on or off,
- bubbles on or off,
- panel lock on or off,
- the unit the panel displays (°C or °F).

**If a single value disagrees, the layout is wrong. Do not continue.**

### 4. Find the right offsets with watch mode

Watch mode is how a wrong layout gets corrected. It polls and prints exactly
which byte and which bit moved:

```bash
python scripts/bestway_probe.py --host 192.0.2.10 --watch
```

Now press one button at a time on the physical panel. Toggling the bubbles gives
you something like:

```text
change detected
  byte 4:   2 ->   3  (00000010 -> 00000011)  bits [0]
```

That is the bubbles bit: byte 4, bit 0. Work through every function, then note
the temperature bytes by changing the setpoint on the panel.

### 5. Write down the corrected layout

Put your findings in a JSON file — no code change needed:

```json
{
  "name": "airjet-mine",
  "provenance": "verified against the physical panel on 2026-09-01",
  "minimum_payload_length": 12,
  "locations": {
    "CURRENT_TEMPERATURE": { "kind": "byte", "offset": 6 },
    "TARGET_TEMPERATURE": { "kind": "byte", "offset": 5 },
    "HEATER": { "kind": "bit", "offset": 4, "bit": 2 },
    "FILTER_PUMP": { "kind": "bit", "offset": 4, "bit": 1 },
    "BUBBLES": { "kind": "bit", "offset": 4, "bit": 0 },
    "CONTROL_PANEL_LOCK": { "kind": "bit", "offset": 4, "bit": 3 },
    "UNIT_IS_FAHRENHEIT": { "kind": "bit", "offset": 4, "bit": 4 }
  },
  "target_temperature_min_c": 20.0,
  "target_temperature_max_c": 40.0,
  "target_temperature_step_c": 1.0
}
```

Set `target_temperature_min_c` and `target_temperature_max_c` to the range your
panel actually accepts. The gateway refuses any setpoint outside it.

This file describes your hardware. Keep it out of the public repository.

Re-run the probe against it until every value matches:

```bash
python scripts/bestway_probe.py --host 192.0.2.10 --profile-path /etc/homeflow/airjet.json
```

### 6. Turn on reading

```dotenv
HOMEFLOW_DEMO_MODE=false
HOMEFLOW_BESTWAY_ENABLED=true
HOMEFLOW_BESTWAY_HOST=192.0.2.10
HOMEFLOW_BESTWAY_PROFILE=airjet-19byte
HOMEFLOW_BESTWAY_TRUST_PROFILE=true
```

Use `HOMEFLOW_BESTWAY_PROFILE_PATH` instead when you wrote your own layout file.
Demo mode has to be off: a demo build must not be able to reach real hardware.

The pool now appears in the app: water temperature, target, and the state of
heater, filter and bubbles. Still no control — nothing is writable yet.

Leave it running for a while. Watch that the values track the panel as the tub
heats and the filter cycles. A layout that is right in one state can still be
wrong in another.

## Releasing control, one capability at a time

**First read the panel's own temperature limits.** Hold the down button until
it stops, then the up button, and put those numbers into your layout. The
shipped range is a placeholder, and a setpoint is the one control where a wrong
bound matters.

Release the least consequential capability first, and only after the previous
one has been observed working.

A suggested order — bubbles, then filter, then heater, then setpoint, then panel
lock:

```dotenv
HOMEFLOW_BESTWAY_WRITE_ENABLED=BUBBLES
```

Restart the gateway, then for that one capability:

1. **Stand where you can see and hear the tub.**
2. Switch it on from the app.
3. Confirm the physical effect and that the panel agrees.
4. Switch it off from the app and confirm again.
5. Confirm the app shows the change, not a stale value.

If the app reports `UNKNOWN`, the controller did not confirm the write. That is
the read-back working; do not release anything further until you understand why.

Then add the next one:

```dotenv
HOMEFLOW_BESTWAY_WRITE_ENABLED=BUBBLES,FILTER_PUMP
```

Valid names: `BUBBLES`, `FILTER_PUMP`, `HEATER`, `TARGET_TEMPERATURE`,
`CONTROL_PANEL_LOCK`.

### The control panel lock, and a possible one-way door

Whether the panel lock also blocks commands arriving over the network is
firmware specific and **has not been verified**. On many controllers the lock
only disables the physical buttons, which is what it is for.

Find out before releasing anything else: lock the panel, then toggle an already
released control from the app.

* The control still works — the lock covers the buttons only.
* The command settles as `UNKNOWN` — the lock covers the network too.

The second outcome matters, because it makes `CONTROL_PANEL_LOCK` a trap: locking
the panel from the app would disable the app's own controls, and unlocking would
mean walking to the tub. **Do not release `CONTROL_PANEL_LOCK` for writing until
this is answered**, and do not release it at all if the lock blocks the network.

Keep the panel unlocked while verifying any other capability, so a control that
does not respond has only one possible explanation.

### Before releasing the heater

The heater is the one with real physical consequences. Check that your
controller's own interlocks still behave as they should — on many models the
filter pump must run for the heater to work — and that the gateway never leaves
the heater on when the pump is off. HomeFlow does not bypass hardware
protections and must not be used to.

## Without hardware

The whole stack runs against a synthetic controller:

```bash
python backend/tests/simulators/bestway_simulator.py --port 12416
python scripts/bestway_probe.py --host 127.0.0.1 --port 12416 --watch
```

This is what CI uses. It never touches a real device.

## Configuration reference

| Setting | Default | Meaning |
| --- | --- | --- |
| `HOMEFLOW_BESTWAY_ENABLED` | `false` | Wire the adapter at all |
| `HOMEFLOW_BESTWAY_HOST` | — | Controller address, required when enabled |
| `HOMEFLOW_BESTWAY_PORT` | `12416` | Controller port |
| `HOMEFLOW_BESTWAY_POLL_SECONDS` | `15` | How often state is read |
| `HOMEFLOW_BESTWAY_PROFILE` | `airjet-candidate` | Built-in layout |
| `HOMEFLOW_BESTWAY_PROFILE_PATH` | — | Your layout file, overrides the built-in |
| `HOMEFLOW_BESTWAY_TRUST_PROFILE` | `false` | You verified the layout against the panel |
| `HOMEFLOW_BESTWAY_WRITE_ENABLED` | empty | Capabilities released for writing |

Demo mode and this adapter cannot be enabled together: a demo build must not be
able to reach real hardware.

## Security notes

The protocol is plaintext and unauthenticated on the local network, so:

- only the gateway talks to the controller; no client ever connects to port
  12416, and the port is never exposed to the VPN or the internet;
- frames are length-checked and type-checked before anything is allocated, and
  a controller that cannot frame correctly is disconnected;
- every read and every exchange has a deadline;
- the API exposes semantic commands only — there is no raw passthrough
  endpoint, and no caller-supplied bytes reach the socket;
- the passcode the controller issues is a device credential: it stays in memory
  and is never logged.

## Troubleshooting

**The pool does not appear.** The layout is not trusted. That is the default;
work through the verification above.

**`the controller sent an unexpected status block`.** The payload is shorter
than the layout expects. Re-run the probe and check the real length.

**A control is missing in the app.** That capability has not been released.
Capabilities are what the client renders from, so an unreleased control is
absent rather than broken.

**Commands settle as `UNKNOWN`.** The controller acknowledged at the transport
level but the read-back did not show the change. Either the write format differs
on your model, or the offset is wrong. Do not work around it by ignoring the
read-back.
