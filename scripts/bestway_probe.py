"""Read a Bestway AirJet controller and help verify its datapoint layout.

This script never writes. It is the read-only step that has to happen before any
control is released: it shows what the controller reports, what the current
layout claims those bytes mean, and — in watch mode — exactly which byte or bit
moves when you press a button on the physical panel.

    # one snapshot, decoded against the current layout
    python scripts/bestway_probe.py --host 192.0.2.10

    # keep reading and highlight what changes while you use the panel
    python scripts/bestway_probe.py --host 192.0.2.10 --watch

Verification procedure, and what to do with the result, are in
docs/integrations/bestway.md.

The output describes your hardware. Review it before pasting it anywhere public.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from homeflow.integrations.bestway.client import BestwayClient
from homeflow.integrations.bestway.datapoints import (
    BitLocation,
    Datapoint,
    DatapointProfile,
    ProfileError,
    builtin_profile,
    load_profile,
    to_celsius,
)
from homeflow.integrations.bestway.protocol import (
    STATUS_READ_PAYLOAD,
    Command,
    Frame,
    FrameReader,
    ProtocolError,
    decode_length_prefixed,
    encode_length_prefixed,
)

BOOLEAN_MARKS = {True: "on", False: "off"}


def hex_dump(payload: bytes) -> str:
    lines = []
    for start in range(0, len(payload), 8):
        chunk = payload[start : start + 8]
        columns = " ".join(f"{byte:02x}" for byte in chunk)
        bits = " ".join(f"{byte:08b}" for byte in chunk)
        lines.append(f"  [{start:2d}] {columns:<23}  {bits}")
    return "\n".join(lines)


def describe(profile: DatapointProfile, payload: bytes) -> str:
    try:
        decoded = profile.decode(payload)
    except ProfileError as exc:
        return f"  the layout does not fit this payload: {exc}"

    fahrenheit = bool(decoded.get(Datapoint.UNIT_IS_FAHRENHEIT, False))
    lines = []
    for datapoint in Datapoint:
        if datapoint not in decoded:
            continue
        location = profile.location_for(datapoint)
        where = (
            f"byte {location.offset} bit {location.bit}"
            if isinstance(location, BitLocation)
            else f"byte {location.offset}"
        )
        raw = decoded[datapoint]
        if isinstance(raw, bool):
            shown = BOOLEAN_MARKS[raw]
        elif datapoint in (Datapoint.CURRENT_TEMPERATURE, Datapoint.TARGET_TEMPERATURE):
            shown = f"{to_celsius(float(raw), fahrenheit=fahrenheit)} C  (raw {raw})"
        else:
            shown = str(raw)
        lines.append(f"  {datapoint.value:<22} {shown:<22} from {where}")
    return "\n".join(lines)


def differences(previous: bytes, current: bytes) -> list[str]:
    changes = []
    for offset in range(max(len(previous), len(current))):
        before = previous[offset] if offset < len(previous) else None
        after = current[offset] if offset < len(current) else None
        if before == after:
            continue
        if before is None or after is None:
            changes.append(f"  byte {offset}: length changed")
            continue
        moved = [bit for bit in range(8) if (before >> bit & 1) != (after >> bit & 1)]
        bits = f"  bits {moved}" if moved else ""
        changes.append(
            f"  byte {offset}: {before:3d} -> {after:3d}  ({before:08b} -> {after:08b}){bits}"
        )
    return changes


async def probe(args: argparse.Namespace) -> int:
    profile = (
        load_profile(Path(args.profile_path))
        if args.profile_path
        else builtin_profile(args.profile)
    )

    print(f"layout      : {profile.name}  ({profile.provenance})")
    print(
        f"verified    : {'yes' if profile.trusted else 'NO - nothing is exposed or writable yet'}"
    )
    print(f"controller  : {args.host}:{args.port}")
    print()

    client = BestwayClient(
        args.host, args.port, connect_timeout=args.timeout, request_timeout=args.timeout
    )
    try:
        await client.connect()
    except Exception as exc:
        print(f"could not reach the controller: {exc}")
        return 1

    previous: bytes | None = None
    try:
        while True:
            payload = await client.read_status()

            if previous is None:
                print(f"status payload ({len(payload)} bytes)")
                print(hex_dump(payload))
                print()
                print("decoded with the current layout")
                print(describe(profile, payload))
                print()
                if not args.watch:
                    print("Compare every line above with the physical control panel.")
                    print("See docs/integrations/bestway.md for what to do next.")
            elif payload != previous:
                print("change detected")
                for line in differences(previous, payload):
                    print(line)
                print(describe(profile, payload))
                print()

            previous = payload
            if not args.watch:
                return 0
            await asyncio.sleep(args.interval)
    except Exception as exc:
        print(f"the connection failed: {exc}")
        return 1
    finally:
        await client.close()


async def diagnose(args: argparse.Namespace) -> int:
    """Show exactly what the controller sends, without filtering.

    Reading only. The three frames sent below are the documented read-side
    handshake; no attribute is ever written.
    """
    print(f"controller  : {args.host}:{args.port}")
    print("mode        : diagnosis, read-only")
    print()

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(args.host, args.port), timeout=args.timeout
        )
    except Exception as exc:
        print(f"TCP connection failed: {exc}")
        return 1

    frames = FrameReader()

    async def collect(seconds: float, label: str) -> list[Frame]:
        """Read whatever arrives within a window and show it verbatim."""
        received = bytearray()
        deadline = asyncio.get_running_loop().time() + seconds
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=remaining)
            except TimeoutError:
                break
            if not chunk:
                print(f"  {label}: the controller closed the connection")
                break
            received.extend(chunk)

        if not received:
            print(f"  {label}: nothing received")
            return []

        print(f"  {label}: {len(received)} bytes")
        print(f"    raw   : {received.hex(' ')}")
        try:
            parsed = frames.feed(bytes(received))
        except ProtocolError as exc:
            print(f"    frames: cannot be framed as this protocol ({exc})")
            return []
        if not parsed:
            print("    frames: no complete frame yet (unexpected framing?)")
        for frame in parsed:
            name = frame.command.name if isinstance(frame.command, Command) else "UNKNOWN"
            print(
                f"    frame : command 0x{int(frame.command):04x} ({name}), "
                f"flag 0x{frame.flag:02x}, {len(frame.payload)} byte payload"
            )
        return parsed

    async def send(frame: Frame, label: str) -> None:
        print(f"-> {label}: {frame.encode().hex(' ')}")
        writer.write(frame.encode())
        await writer.drain()

    try:
        print("step 1: listen without sending anything")
        await collect(3.0, "passive")

        print()
        print("step 2: ask for the passcode")
        await send(Frame(command=Command.PASSCODE_REQUEST), "passcode request")
        answered = await collect(5.0, "reply")

        passcode = None
        for frame in answered:
            if frame.command == Command.PASSCODE_RESPONSE:
                passcode = decode_length_prefixed(frame.payload)
                print(f"    passcode received: {len(passcode)} bytes (value not shown)")

        if passcode is not None:
            print()
            print("step 3: log in")
            await send(
                Frame(command=Command.LOGIN_REQUEST, payload=encode_length_prefixed(passcode)),
                "login request",
            )
            await collect(5.0, "reply")

        print()
        print("step 4: ask for the status block")
        await send(
            Frame(command=Command.STATUS_REQUEST, payload=STATUS_READ_PAYLOAD),
            "status read",
        )
        await collect(5.0, "reply")

        print()
        print("step 5: keep listening in case the controller reports on its own")
        await collect(8.0, "unsolicited")
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

    print()
    print("Done. Nothing was written to the controller.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", required=True, help="controller address on the local network")
    parser.add_argument("--port", type=int, default=12416)
    parser.add_argument(
        "--profile", default="airjet-candidate", help="built-in layout to decode with"
    )
    parser.add_argument("--profile-path", help="JSON layout file, overrides --profile")
    parser.add_argument("--watch", action="store_true", help="keep reading and show what changes")
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="show the raw handshake byte by byte when the normal read fails",
    )
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    runner = diagnose if args.diagnose else probe
    with contextlib.suppress(KeyboardInterrupt):
        return asyncio.run(runner(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
