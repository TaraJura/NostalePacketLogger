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

    try:
        client.send_packet(packet)
        # Give the DLL a moment to answer with STATUS|Sent: ...
        deadline = time.monotonic() + 1.0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            message = client.recv(timeout=remaining)
            if message is None:
                break
            if message.kind is Kind.STATUS:
                print(message.payload)
    except (ConnectionLost, ProtocolError, ValueError) as exc:
        print(f"ntpl: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()

    return 0


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

    send = sub.add_parser("send", help="inject one packet")
    _add_connection(send)
    send.add_argument("packet", nargs="+", help="e.g. walk 19 146 0 10")
    send.set_defaults(handler=cmd_send)

    status = sub.add_parser("status", help="show hook state")
    _add_connection(status)
    status.add_argument("--timeout", type=float, default=3.0)
    status.set_defaults(handler=cmd_status)

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
