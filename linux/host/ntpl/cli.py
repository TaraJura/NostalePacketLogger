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
from .mapdata import (
    MapDataError,
    find_map_for_positions,
    load_nstc,
    terrain_rgb,
)
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


def _add_connection(
    parser: argparse.ArgumentParser, auto_port: bool = False
) -> None:
    parser.add_argument("--host", default=DEFAULT_HOST, help="default: %(default)s")
    parser.add_argument(
        "--port",
        type=int,
        default=None if auto_port else default_port(),
        help=(
            "default: the injected client's port file"
            if auto_port
            else "default: %(default)s"
        ),
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


# --------------------------------------------------------------------- cast


def build_cast_packet(
    skill: int,
    target_type: int = 3,
    target_id: int | None = None,
    x: int | None = None,
    y: int | None = None,
) -> str:
    """Default u_s layout: u_s <skillSlot> <targetType> <targetId> [x y].

    The first u_s field is a zero-based slot in the character's learned skill
    list (the `ski` packet), not the Skill.dat vnum; slot 0 is the basic
    attack. Use --packet for a literal packet.
    """
    fields = [str(skill), str(target_type)]
    if target_id is not None:
        fields.append(str(target_id))
    if x is not None and y is not None:
        fields.extend([str(x), str(y)])
    return "u_s " + " ".join(fields)


def cmd_cast(args: argparse.Namespace) -> int:
    """Use a skill on a target and wait for the server's `su` confirmation."""
    client = _connect(args)
    if client is None:
        return 2

    if args.packet:
        packet = args.packet
    elif args.skill is not None:
        packet = build_cast_packet(
            args.skill, args.target_type, args.target_id, args.x, args.y
        )
    else:
        print("ntpl: give a skill slot or --packet", file=sys.stderr)
        client.close()
        return 2

    deadline = time.monotonic() + args.timeout
    try:
        if args.stand:
            # The character cannot cast while sitting.
            client.send_packet(f"rest 1 1 {args.stand}")
        if not args.no_select and args.target_id is not None:
            if args.target_type == 3:
                # Select the monster in the client so it sends its own ncif;
                # the server validates u_s against that selection.
                client.send_raw(f"TARGET|{args.target_id}")
            else:
                client.send_packet(f"ncif {args.target_type} {args.target_id}")
        client.send_packet(packet)
        print("sent:", packet)

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
            if message.kind is not Kind.RECV:
                continue

            used = Packet.from_message(message)
            if used.opcode != "su":
                continue
            values = used.ints(5)
            if not values:
                continue
            _attacker_type, _attacker_id, _target_type, target_id, skill = values
            if args.target_id is not None and target_id != args.target_id:
                continue
            if args.skill is not None and skill != args.skill:
                continue
            print("confirmed:", used.text)
            return 0
    except (ConnectionLost, ProtocolError, ValueError) as exc:
        print(f"ntpl: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()

    print("ntpl: no su confirmation for the skill", file=sys.stderr)
    return 1


# --------------------------------------------------------------------- walk


def cmd_walk(args: argparse.Namespace) -> int:
    """Ask the client to walk to a cell and wait for its own walk packet.

    This uses the DLL's ``MOVE|x y`` command, which calls the client's own
    pathfinding. The client animates the movement and sends
    ``walk <x> <y> <checksum> <speed>`` itself. Injecting a walk packet
    directly moves only the server; the client then snaps on a correction and
    appears to teleport.

    The character must be standing. While sitting the server ignores movement;
    send ``rest 1 1 <charId>`` to toggle the sit state first.
    """
    client = _connect(args)
    if client is None:
        return 2

    target = (args.x, args.y)
    deadline = time.monotonic() + args.timeout
    try:
        # Ask the client to pathfind and animate the walk itself. A raw
        # `walk` packet only moves the server; the client then snaps when the
        # server corrects it (teleporting).
        client.send_raw(f"MOVE|{args.x} {args.y}")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            message = client.recv(timeout=min(remaining, 0.2))
            if message is None:
                continue
            if message.kind is Kind.STATUS:
                print(message.payload)
                lowered = message.payload.lower()
                if "send failed" in lowered or "move:" in lowered:
                    return 1
                continue
            if message.kind is Kind.SEND:
                packet = Packet.from_message(message)
                if packet.opcode != "walk":
                    continue
                values = packet.ints(2)
                if values and (values[0], values[1]) == target:
                    print(f"confirmed client walk: {packet.text}")
                    return 0
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

    print("ntpl: no client walk packet for the target", file=sys.stderr)
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


# ---------------------------------------------------------------------- map


def _write_ppm(grid, output: str, scale: int) -> None:
    """Fallback renderer with no third-party dependency."""
    width = grid.width * scale
    height = grid.height * scale
    with open(output, "wb") as handle:
        handle.write(f"P6\n{width} {height}\n255\n".encode())
        for y in range(grid.height):
            row = bytearray()
            for x in range(grid.width):
                row.extend(bytes(terrain_rgb(grid.at(x, y))) * scale)
            for _ in range(scale):
                handle.write(row)


def cmd_map(args: argparse.Namespace) -> int:
    """Inspect or render the client's NStcData map grids."""
    try:
        grids = load_nstc(args.file)
    except MapDataError as exc:
        print(f"ntpl: {exc}", file=sys.stderr)
        return 2

    if args.map_command == "info":
        grid = grids.get(args.map_id)
        if grid is None:
            print(f"ntpl: map {args.map_id} not found", file=sys.stderr)
            return 1
        total = grid.width * grid.height
        walkable = grid.walkable_count()
        print(
            f"map {grid.map_id}: {grid.width}x{grid.height}, "
            f"walkable {walkable}/{total} ({100 * walkable / total:.1f}%)"
        )
        if args.at:
            try:
                x, y = (int(part) for part in args.at.split(",", 1))
            except ValueError:
                print("ntpl: --at must be x,y", file=sys.stderr)
                return 2
            value = grid.at(x, y)
            state = "walkable" if grid.is_walkable(x, y) else "blocked"
            print(f"at ({x},{y}): {value:#04x} {state}" if value is not None else "out of bounds")
        return 0

    if args.map_command == "render":
        grid = grids.get(args.map_id)
        if grid is None:
            print(f"ntpl: map {args.map_id} not found", file=sys.stderr)
            return 1
        if args.scale <= 0 or args.scale > 16:
            print("ntpl: --scale must be 1..16", file=sys.stderr)
            return 2
        try:
            from PIL import Image
        except ImportError:
            _write_ppm(grid, args.output, args.scale)
            print(f"wrote {args.output} (PPM; install Pillow for PNG)")
            return 0

        image = Image.new("RGB", (grid.width * args.scale, grid.height * args.scale))
        pixels = image.load()
        for y in range(grid.height):
            for x in range(grid.width):
                color = terrain_rgb(grid.at(x, y))
                for dy in range(args.scale):
                    for dx in range(args.scale):
                        pixels[x * args.scale + dx, y * args.scale + dy] = color
        image.save(args.output)
        print(f"wrote {args.output} ({grid.width}x{grid.height} @ {args.scale}x)")
        return 0

    if args.map_command == "best":
        points: list[tuple[int, int]] = []
        if args.points:
            for part in args.points.replace(";", " ").split():
                try:
                    x, y = (int(v) for v in part.split(",", 1))
                except ValueError:
                    print(f"ntpl: bad point {part!r}", file=sys.stderr)
                    return 2
                points.append((x, y))
        if args.log:
            text = Path(args.log).read_text(encoding="utf-8", errors="replace")
            points.extend(
                (int(x), int(y))
                for x, y in re.findall(r"mv [123] \d+ (\d+) (\d+) ", text)
            )
        if not points:
            print("ntpl: give --points x,y or --log FILE", file=sys.stderr)
            return 2
        for fraction, map_id in find_map_for_positions(grids, points):
            print(f"map {map_id}: {fraction * 100:.1f}% of {len(points)} points walkable")
        return 0

    print("ntpl: unknown map command", file=sys.stderr)
    return 2


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

    return run_gui(
        args.host,
        args.port,
        args.map_data,
        args.log,
        select_client=(
            not args.no_client_picker and args.port == default_port()
        ),
    )


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import run_mcp

    return run_mcp(
        host=args.host,
        port=args.port,
        listen_port=args.listen,
        map_file=args.map_data,
        log_file=args.log,
    )


def cmd_headless(args: argparse.Namespace) -> int:
    from .headless import run_headless

    return run_headless(
        token=args.token,
        token_file=args.token_file,
        game_dir=args.game_dir,
        login_host=args.login_host,
        login_port=args.login_port,
        region=args.region,
        character=args.character,
        world_id=args.world_id,
        channel=args.channel,
        print_packets=args.print_packets,
        email=args.email,
        password=args.password,
        account_id=args.account_id,
        locale=args.locale,
        identity=args.identity,
        show_password=args.show_password,
    )


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

    cast = sub.add_parser(
        "cast", help="use a skill (experimental u_s layout; --packet overrides)"
    )
    _add_connection(cast)
    cast.add_argument("skill", type=int, nargs="?", help="u_s skill slot (0 = basic attack; not the Skill.dat vnum)")
    cast.add_argument("--target-type", type=int, default=3)
    cast.add_argument("--target-id", type=int)
    cast.add_argument("--x", type=int)
    cast.add_argument("--y", type=int)
    cast.add_argument(
        "--no-select", action="store_true", help="do not send ncif before u_s"
    )
    cast.add_argument(
        "--stand", type=int, metavar="CHAR_ID", help="toggle rest (sit/stand) first"
    )
    cast.add_argument("--packet", default=None, help="literal packet override")
    cast.add_argument("--timeout", type=float, default=4.0)
    cast.set_defaults(handler=cmd_cast)

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

    map_parser = sub.add_parser("map", help="inspect or render NStcData maps")
    map_sub = map_parser.add_subparsers(dest="map_command", required=True)

    map_info = map_sub.add_parser("info", help="map size and walkability")
    map_info.add_argument("map_id", type=int)
    map_info.add_argument("--at", default=None, help="x,y to inspect")
    map_info.add_argument("--file", default=None, help="NStcData.NOS path")
    map_info.set_defaults(handler=cmd_map)

    map_render = map_sub.add_parser("render", help="render a map to PNG/PPM")
    map_render.add_argument("map_id", type=int)
    map_render.add_argument("output")
    map_render.add_argument("--scale", type=int, default=3)
    map_render.add_argument("--file", default=None, help="NStcData.NOS path")
    map_render.set_defaults(handler=cmd_map)

    map_best = map_sub.add_parser("best", help="rank maps for live coordinates")
    map_best.add_argument("--points", default=None, help="'x,y x,y ...'")
    map_best.add_argument("--log", default=None, help="packet log to read mv lines")
    map_best.add_argument("--file", default=None, help="NStcData.NOS path")
    map_best.set_defaults(handler=cmd_map)

    replay = sub.add_parser("replay", help="resend SEND packets from a log")
    _add_connection(replay)
    replay.add_argument("file")
    replay.add_argument("--delay", type=float, default=0.2, help="seconds between packets")
    replay.set_defaults(handler=cmd_replay)

    gui = sub.add_parser("gui", help="open the GTK4 window")
    _add_connection(gui)
    gui.add_argument(
        "--map-data",
        default=None,
        help="NStcData.NOS path for the Map tab (default: NTPL_NSTC or the local install)",
    )
    gui.add_argument(
        "--log",
        default=None,
        help="DLL packet log for backfilling entity vnums/names "
        "(default: NTPL_LOG or the Wine prefix's C:\\NosTalePacketLog.txt)",
    )
    gui.add_argument(
        "--no-client-picker",
        action="store_true",
        help="connect straight to --host/--port instead of listing the "
        "running clients (implied by a non-default --port, e.g. the MCP "
        "broker on 47900)",
    )
    gui.set_defaults(handler=cmd_gui)

    mcp = sub.add_parser(
        "mcp",
        help="MCP server over stdio for agents; owns the DLL link and "
        "rebroadcasts it to the GUI/CLI",
    )
    _add_connection(mcp, auto_port=True)
    mcp.add_argument(
        "--listen",
        type=int,
        default=47900,
        help="local port for the GUI/CLI rebroadcast (0 = off; "
        "default: %(default)s)",
    )
    mcp.add_argument(
        "--map-data",
        default=None,
        help="NStcData.NOS path for pathing and map tools",
    )
    mcp.add_argument(
        "--log",
        default=None,
        help="DLL packet log used to seed the map, portals and vnums",
    )
    mcp.set_defaults(handler=cmd_mcp)

    from .headless import DEFAULT_LOGIN_HOST, DEFAULT_LOGIN_PORT

    headless = sub.add_parser(
        "headless",
        help="clientless login: play without the game client "
        "(needs a Gameforge thin token)",
    )
    headless.add_argument(
        "--token",
        default=None,
        help="hex Gameforge thin token (or NTPL_HEADLESS_TOKEN)",
    )
    headless.add_argument("--token-file", default=None)
    headless.add_argument(
        "--game-dir",
        default=".",
        help="directory containing NostaleClientX.exe (default: %(default)s)",
    )
    headless.add_argument("--login-host", default=DEFAULT_LOGIN_HOST)
    headless.add_argument("--login-port", type=int, default=DEFAULT_LOGIN_PORT)
    headless.add_argument("--region", default="0")
    headless.add_argument("--character", type=int, default=None)
    headless.add_argument("--world-id", type=int, default=None)
    headless.add_argument("--channel", type=int, default=None)
    headless.add_argument("--print-packets", action="store_true")
    headless.add_argument(
        "--email",
        default=None,
        help="Gameforge email (or NTPL_EMAIL); prompted when omitted",
    )
    headless.add_argument(
        "--password",
        default=None,
        help="Gameforge password (or NTPL_PASSWORD); prompted when omitted",
    )
    headless.add_argument("--account-id", default=None)
    headless.add_argument("--locale", default="en-US")
    headless.add_argument(
        "--identity",
        default=None,
        help="captured 30-value blackbox identity JSON "
        "(default: ~/.config/ntpl/identity.json)",
    )
    headless.add_argument(
        "--show-password",
        action="store_true",
        help="echo the password while typing (to verify a paste)",
    )
    headless.set_defaults(handler=cmd_headless)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
