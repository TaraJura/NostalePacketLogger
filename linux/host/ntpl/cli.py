"""Command line front end.

    ntpl monitor                 live packet stream (and a small command shell)
    ntpl send walk 19 146 0 10   inject one packet
    ntpl status                  hook addresses and state
    ntpl replay capture.log      resend the SEND packets from a log
    ntpl gui                     GTK4 window

Run it without installing anything:

    PYTHONPATH=linux/host python3 -m ntpl monitor
"""

from __future__ import annotations

import argparse
import re
import sys
import threading
import time
from pathlib import Path

from . import __version__
from .client import ConnectionLost, PacketClient
from .engine import Engine
from .protocol import (
    DEFAULT_HOST,
    Kind,
    Message,
    Packet,
    ProtocolError,
    default_port,
)

RESET = "\033[0m"
DIM = "\033[2m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"

# The DLL writes "10:19:19.123 [RECV] walk ..." to its file log.
LOG_LINE = re.compile(
    r"^\d\d:\d\d:\d\d\.\d+\s+\[(RECV|SEND|MANUAL_SEND)\]\s+(.*)$"
)


def _paint(text: str, code: str, enabled: bool) -> str:
    return f"{code}{text}{RESET}" if enabled else text


def _add_connection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=DEFAULT_HOST, help="default: %(default)s")
    parser.add_argument(
        "--port", type=int, default=default_port(), help="default: %(default)s"
    )
    parser.add_argument(
        "--retries", type=int, default=30, help="connection attempts (default: %(default)s)"
    )


def _connect(args: argparse.Namespace) -> PacketClient | None:
    client = PacketClient(args.host, args.port)
    try:
        client.connect(retries=args.retries)
    except ConnectionLost as exc:
        print(f"ntpl: {exc}", file=sys.stderr)
        print(
            "ntpl: is the game running and the DLL injected? "
            "(linux/scripts/inject.sh)",
            file=sys.stderr,
        )
        return None
    return client


def _matches(packet: Packet, needle: str) -> bool:
    if not needle:
        return True
    needle = needle.lower()
    return needle in packet.text.lower() or needle in packet.opcode.lower()


# ------------------------------------------------------------------ monitor


def cmd_monitor(args: argparse.Namespace) -> int:
    client = _connect(args)
    if client is None:
        return 2

    color = sys.stdout.isatty() and not args.no_color
    engine = Engine() if args.engine else None

    filter_text = args.filter or ""
    log_file = None
    if args.log:
        log_file = open(args.log, "a", encoding="utf-8")

    def stdin_loop() -> None:
        nonlocal filter_text
        for line in sys.stdin:
            line = line.rstrip("\n")
            if not line:
                continue

            if line.startswith("/send "):
                try:
                    client.send_packet(line[6:])
                except (ConnectionLost, ValueError) as exc:
                    print(f"ntpl: send failed: {exc}", file=sys.stderr)
            elif line.startswith("/filter "):
                filter_text = line[8:].strip()
                print(f"filter: {filter_text!r}")
            elif line == "/status":
                client.request_status()
            elif line == "/engine":
                print(engine.world if engine is not None else "engine is off (--engine)")
            elif line == "/quit":
                client.close()
                return
            else:
                print("commands: /send <packet> | /filter <text> | /status | "
                      "/engine | /quit")

    threading.Thread(target=stdin_loop, name="ntpl-stdin", daemon=True).start()

    print(f"connected to {args.host}:{args.port}  (ctrl-c to quit)")
    try:
        for message in client.messages():
            if message.kind is Kind.STATUS:
                print(_paint(f"[status] {message.payload}", DIM, color))
                continue
            if message.kind is Kind.QUIT:
                break

            packet = Packet.from_message(message)
            if engine is not None:
                engine.feed(packet)

            if _matches(packet, filter_text):
                direction_color = CYAN if packet.direction is Kind.RECV else YELLOW
                print(_paint(str(packet), direction_color, color))

            if log_file is not None:
                log_file.write(message.encode() + "\n")
                log_file.flush()
    except KeyboardInterrupt:
        pass
    except ConnectionLost as exc:
        print(f"ntpl: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()
        if log_file is not None:
            log_file.close()

    return 0


# --------------------------------------------------------------------- send


def cmd_send(args: argparse.Namespace) -> int:
    packet = " ".join(args.packet).strip()
    if not packet:
        print("ntpl: empty packet", file=sys.stderr)
        return 2

    client = _connect(args)
    if client is None:
        return 2

    confirmed = False
    failed = False
    try:
        client.send_packet(packet)
        # Give the DLL a moment to answer with STATUS|Sent: ...
        deadline = time.monotonic() + args.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            message = client.recv(timeout=remaining)
            if message is None:
                break
            if message.kind is Kind.STATUS:
                print(message.payload)
                if message.payload.startswith("Sent:"):
                    confirmed = True
                    break
                if "send failed" in message.payload.lower():
                    failed = True
                    break
    except (ConnectionLost, ProtocolError, ValueError) as exc:
        print(f"ntpl: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()

    if failed:
        return 1
    if not confirmed:
        print("ntpl: the DLL never confirmed the send", file=sys.stderr)
        return 1
    return 0


# --------------------------------------------------------------------- walk


def cmd_walk(args: argparse.Namespace) -> int:
    """Send one walk and wait for the server's position update.

    The client does not reliably echo its own movement, but the server answers
    an accepted walk with ``tp <type> <id> <x> <y> ...`` (observed live) or
    ``mv``. This command exists so the engine can act and verify, not just fire
    packets into the dark.

    The character must be standing. While sitting the server ignores movement;
    send ``rest 1 1 <charId>`` to toggle the sit state first.
    """
    client = _connect(args)
    if client is None:
        return 2

    target = (args.x, args.y)
    deadline = time.monotonic() + args.timeout
    try:
        client.send_packet(f"walk {args.x} {args.y} 0 {args.speed}")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            message = client.recv(timeout=remaining)
            if message is None:
                continue
            if message.kind is Kind.STATUS:
                print(message.payload)
                if "send failed" in message.payload.lower():
                    return 1
                continue
            if message.kind is Kind.RECV:
                packet = Packet.from_message(message)
                if packet.opcode not in ("tp", "mv"):
                    continue
                values = packet.ints(5) if packet.opcode == "mv" else packet.ints(4)
                if values and len(values) >= 4 and values[0] == 1:
                    if (values[2], values[3]) == target:
                        print(f"confirmed: {packet.text}")
                        return 0
    except (ConnectionLost, ProtocolError, ValueError) as exc:
        print(f"ntpl: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()

    print("ntpl: no server position update for the walk", file=sys.stderr)
    return 1


# --------------------------------------------------------------------- scan


def cmd_scan(args: argparse.Namespace) -> int:
    """Find an (x, y) pair in the game's writable memory.

    The DLL searches for byte, int16 and int32 pairs. This is how the engine
    locates the player's position after sending a walk: the client predicts the
    move locally, so the target coordinates appear in memory even though it
    never receives its own ``mv``.
    """
    client = _connect(args)
    if client is None:
        return 2

    deadline = time.monotonic() + args.timeout
    try:
        client.send_raw(f"SCAN|{args.x} {args.y}")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print("ntpl: no SCAN reply", file=sys.stderr)
                return 1
            message = client.recv(timeout=remaining)
            if message is None:
                continue
            if message.kind is Kind.STATUS and message.payload.startswith("SCAN "):
                print(message.payload)
                return 0
    except (ConnectionLost, ProtocolError) as exc:
        print(f"ntpl: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()


# ------------------------------------------------------------------- status


def cmd_status(args: argparse.Namespace) -> int:
    client = _connect(args)
    if client is None:
        return 2

    deadline = time.monotonic() + args.timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print("ntpl: no status line received", file=sys.stderr)
                return 1
            message = client.recv(timeout=remaining)
            if message is None:
                continue
            if message.kind is Kind.STATUS:
                print(message.payload)
                return 0
    except (ConnectionLost, ProtocolError) as exc:
        print(f"ntpl: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()


# ------------------------------------------------------------------- replay


def _parse_log_line(line: str) -> Message | None:
    match = LOG_LINE.match(line)
    if match is not None:
        direction = "SEND" if match.group(1) == "MANUAL_SEND" else match.group(1)
        return Message(Kind(direction), match.group(2))

    if "|" in line:
        try:
            return Message.parse(line)
        except ProtocolError:
            return None

    return None


def cmd_replay(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.is_file():
        print(f"ntpl: no such file: {path}", file=sys.stderr)
        return 2

    client = _connect(args)
    if client is None:
        return 2

    sent = 0
    skipped = 0
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            message = _parse_log_line(line)
            if message is None or message.kind is not Kind.SEND:
                skipped += 1
                continue

            client.send_packet(message.payload)
            sent += 1
            if args.delay > 0:
                time.sleep(args.delay)
    except (ConnectionLost, ValueError) as exc:
        print(f"ntpl: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()

    print(f"replayed {sent} packet(s), skipped {skipped} non-SEND line(s)")
    return 0


# ---------------------------------------------------------------------- gui


# --------------------------------------------------------------------- find


def cmd_find(args: argparse.Namespace) -> int:
    """Find a 32-bit value in game memory (usually a character/entity id)."""
    client = _connect(args)
    if client is None:
        return 2

    deadline = time.monotonic() + args.timeout
    try:
        client.send_raw(f"FIND|{args.value}")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print("ntpl: no FIND reply", file=sys.stderr)
                return 1
            message = client.recv(timeout=remaining)
            if message is None:
                continue
            if message.kind is Kind.STATUS and message.payload.startswith("FIND "):
                print(message.payload)
                return 0
    except (ConnectionLost, ProtocolError) as exc:
        print(f"ntpl: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()


# --------------------------------------------------------------------- read


def cmd_read(args: argparse.Namespace) -> int:
    """Dump game memory returned by SCAN, without a debugger attached."""
    client = _connect(args)
    if client is None:
        return 2

    deadline = time.monotonic() + args.timeout
    try:
        client.send_raw(f"READ|0x{args.address:X} {args.length}")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print("ntpl: no READ reply", file=sys.stderr)
                return 1
            message = client.recv(timeout=remaining)
            if message is None:
                continue
            if message.kind is Kind.STATUS and (
                message.payload.startswith("READ ") or message.payload == "READ failed"
            ):
                print(message.payload)
                return 0 if message.payload.startswith("READ ") else 1
    except (ConnectionLost, ProtocolError) as exc:
        print(f"ntpl: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        from .gui import run_gui
    except (ImportError, ValueError) as exc:
        print(
            "ntpl: the GTK4 GUI needs PyGObject and GTK4:\n"
            "  sudo apt install python3-gi gir1.2-gtk-4.0\n"
            f"({exc})",
            file=sys.stderr,
        )
        return 2

    return run_gui(args.host, args.port)


# --------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ntpl",
        description="Native host for the NosTale Packet Logger DLL.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    monitor = sub.add_parser("monitor", help="print the live packet stream")
    _add_connection(monitor)
    monitor.add_argument("--filter", default="", help="only show packets containing this text")
    monitor.add_argument("--log", default=None, help="append the raw stream to this file")
    monitor.add_argument("--engine", action="store_true", help="keep a WorldState in sync")
    monitor.add_argument("--no-color", action="store_true")
    monitor.set_defaults(handler=cmd_monitor)

    walk = sub.add_parser(
        "walk", help="send a walk and wait for the server's position update"
    )
    _add_connection(walk)
    walk.add_argument("x", type=int)
    walk.add_argument("y", type=int)
    walk.add_argument("--speed", type=int, default=10)
    walk.add_argument("--timeout", type=float, default=6.0)
    walk.set_defaults(handler=cmd_walk)

    send = sub.add_parser("send", help="inject one packet")
    _add_connection(send)
    send.add_argument("packet", nargs="+", help="e.g. walk 19 146 0 10")
    send.add_argument("--timeout", type=float, default=2.0)
    send.set_defaults(handler=cmd_send)

    status = sub.add_parser("status", help="show hook state")
    _add_connection(status)
    status.add_argument("--timeout", type=float, default=3.0)
    status.set_defaults(handler=cmd_status)

    scan = sub.add_parser("scan", help="find an (x,y) pair in game memory")
    _add_connection(scan)
    scan.add_argument("x", type=int)
    scan.add_argument("y", type=int)
    scan.add_argument("--timeout", type=float, default=20.0)
    scan.set_defaults(handler=cmd_scan)

    find = sub.add_parser("find", help="find a 32-bit value in game memory")
    _add_connection(find)
    find.add_argument("value", type=lambda s: int(s, 0))
    find.add_argument("--timeout", type=float, default=20.0)
    find.set_defaults(handler=cmd_find)

    read = sub.add_parser("read", help="dump game memory (reverse engineering)")
    _add_connection(read)
    read.add_argument("address", type=lambda s: int(s, 0))
    read.add_argument("length", type=int, nargs="?", default=64)
    read.add_argument("--timeout", type=float, default=5.0)
    read.set_defaults(handler=cmd_read)

    replay = sub.add_parser("replay", help="resend SEND packets from a log")
    _add_connection(replay)
    replay.add_argument("file")
    replay.add_argument("--delay", type=float, default=0.2, help="seconds between packets")
    replay.set_defaults(handler=cmd_replay)

    gui = sub.add_parser("gui", help="open the GTK4 window")
    _add_connection(gui)
    gui.set_defaults(handler=cmd_gui)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
