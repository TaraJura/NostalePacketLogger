"""MCP server (stdio) and local DLL broker for agentic play.

The DLL accepts **one host at a time** and replaces the previous one. This
module runs a broker that owns that single connection and rebroadcasts the
packet stream to local clients (the GUI, a CLI monitor) on a second port, so
an agent and the GUI can watch the same session. The MCP side speaks
JSON-RPC over stdin/stdout and exposes tools for reading the world and acting
in it: move through the client's own MoveTo, attack/cast, pick up drops, send
raw packets, wait for a server confirmation.

Run it without installing anything::

    PYTHONPATH=linux/host python3 -m ntpl mcp

Then point the GUI at the broker::

    linux/scripts/gui.sh --port 47900

MCP is standard-library only here: newline-delimited JSON-RPC 2.0 on stdio.
Logs go to stderr; stdout carries protocol messages only.
"""

from __future__ import annotations

import itertools
import json
import socket
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, TextIO

from . import __version__
from .client import PacketClient
from .clients import discover_clients
from .engine import Engine
from .mapdata import (
    MapGrid,
    PortalMap,
    add_portal,
    astar,
    load_nstc,
    load_portals,
    normalize_portals,
    set_gate,
)
from .monsternames import load_monster_names
from .protocol import (
    DEFAULT_HOST,
    Kind,
    Message,
    Packet,
    ProtocolError,
    default_port,
)
from .spawnlog import default_log_path, load_gates, load_last_position
from .spawnlog import load_portals as load_log_portals
from .spells import SpellInfo, load_spells

MCP_VERSION = "2024-11-05"
#: GUI/CLI rebroadcast port. Outside the DLL's 47821..47830 fallback range,
#: so the broker can never collide with the logger even after a port clash.
BROKER_PORT = 47900
MAX_TRACKED_PACKETS = 2000
MAX_TRACKED_STATUSES = 200


def _discover_dll_port() -> int | None:
    """The port an injected client published in its ``C:\\ntpl-<pid>.port``.

    The DLL falls back to the next free port when 47821 is taken, so the
    broker must ask the running client where it listens instead of assuming
    the default. Returns None when no injected client is found; the broker
    then falls back to NTPL_PORT/47821 and keeps retrying.
    """
    try:
        discovery = discover_clients()
    except Exception:  # noqa: BLE001 - discovery must not stop the server
        return None
    for client in discovery.clients:
        if client.port:
            return client.port
    return None


class ToolError(RuntimeError):
    """A tool call that cannot be completed; reported as isError content."""


# -- broker ---------------------------------------------------------------


class Broker:
    """Own the DLL connection and fan it out to local TCP clients.

    Upstream lines are rebroadcast verbatim. Downstream lines are forwarded
    upstream, except ``QUIT``: one observer must not unhook the DLL for
    everyone. The broker also replays the last ``STATUS`` line to a new
    client so the GUI does not start with an empty status bar.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int | None = None,
        listen_port: int | None = BROKER_PORT,
        on_packet: Callable[[Packet], None] | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self.host = host
        self.port = default_port() if port is None else port
        self.listen_port = listen_port
        self.on_packet = on_packet or (lambda _packet: None)
        self.on_status = on_status or (lambda _text: None)

        self._stop = threading.Event()
        self._upstream: socket.socket | None = None
        self._upstream_lock = threading.Lock()
        self._clients: dict[int, socket.socket] = {}
        self._clients_lock = threading.Lock()
        self._next_client = itertools.count(1)
        self._last_status = ""
        self._last_status_lock = threading.Lock()
        self._listener: socket.socket | None = None
        #: Actual port after binding (useful when ``listen_port`` is 0).
        self.bound_port: int | None = None

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        threading.Thread(
            target=self._upstream_loop, name="ntpl-broker-up", daemon=True
        ).start()
        # None disables the downstream listener; 0 asks the OS for a port.
        if self.listen_port is not None:
            threading.Thread(
                target=self._listen_loop, name="ntpl-broker-listen", daemon=True
            ).start()

    def close(self) -> None:
        self._stop.set()
        with self._upstream_lock:
            sock, self._upstream = self._upstream, None
            if sock is not None:
                _close_quietly(sock)
        listener, self._listener = self._listener, None
        if listener is not None:
            _close_quietly(listener)
        with self._clients_lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            _close_quietly(client)

    @property
    def connected(self) -> bool:
        with self._upstream_lock:
            return self._upstream is not None

    @property
    def last_status(self) -> str:
        with self._last_status_lock:
            return self._last_status

    def send_upstream(self, line: str) -> bool:
        """Write one protocol line to the DLL; False when not connected."""
        framed = (line.rstrip("\r\n") + "\n").encode("utf-8")
        with self._upstream_lock:
            sock = self._upstream
            if sock is None:
                return False
            try:
                sock.sendall(framed)
                return True
            except OSError:
                _close_quietly(sock)
                self._upstream = None
                return False

    # -- upstream -------------------------------------------------------

    def _upstream_loop(self) -> None:
        while not self._stop.is_set():
            try:
                sock = socket.create_connection(
                    (self.host, self.port), timeout=2.0
                )
            except OSError:
                time.sleep(1.0)
                continue
            sock.settimeout(None)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            with self._upstream_lock:
                self._upstream = sock
            self._broadcast("STATUS|broker connected to the DLL")

            buffer = b""
            try:
                while not self._stop.is_set():
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        self._on_upstream_line(
                            raw.decode("utf-8", "replace").rstrip("\r")
                        )
            except OSError:
                pass
            finally:
                with self._upstream_lock:
                    if self._upstream is sock:
                        self._upstream = None
                _close_quietly(sock)

            if not self._stop.is_set():
                self._broadcast("STATUS|broker lost the DLL; reconnecting")
                time.sleep(1.0)

    def _on_upstream_line(self, line: str) -> None:
        if not line:
            return
        try:
            message = Message.parse(line)
        except ProtocolError:
            return

        if message.kind is Kind.STATUS:
            with self._last_status_lock:
                self._last_status = message.payload
            self.on_status(message.payload)
        elif message.kind in (Kind.RECV, Kind.SEND):
            try:
                self.on_packet(Packet.from_message(message))
            except ProtocolError:
                pass

        self._broadcast(line)

    def _broadcast(self, line: str) -> None:
        framed = (line.rstrip("\r\n") + "\n").encode("utf-8")
        with self._clients_lock:
            clients = list(self._clients.items())
        for client_id, client in clients:
            try:
                client.sendall(framed)
            except OSError:
                with self._clients_lock:
                    self._clients.pop(client_id, None)
                _close_quietly(client)

    # -- downstream -----------------------------------------------------

    def _listen_loop(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", self.listen_port))
            listener.listen(8)
        except OSError:
            _close_quietly(listener)
            return
        self._listener = listener
        self.bound_port = listener.getsockname()[1]
        while not self._stop.is_set():
            try:
                client, _address = listener.accept()
            except OSError:
                break
            client_id = next(self._next_client)
            with self._clients_lock:
                self._clients[client_id] = client
            greeting = self.last_status or "broker waiting for the DLL"
            try:
                client.sendall(f"STATUS|{greeting}\n".encode("utf-8"))
            except OSError:
                pass
            threading.Thread(
                target=self._client_loop,
                args=(client_id, client),
                name=f"ntpl-broker-client-{client_id}",
                daemon=True,
            ).start()

    def _client_loop(self, client_id: int, client: socket.socket) -> None:
        client.settimeout(None)
        buffer = b""
        try:
            while not self._stop.is_set():
                chunk = client.recv(65536)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    line = raw.decode("utf-8", "replace").rstrip("\r")
                    if not line:
                        continue
                    if line.upper().startswith("QUIT"):
                        # One observer must not unhook the DLL for everyone.
                        continue
                    self.send_upstream(line)
        except OSError:
            pass
        finally:
            with self._clients_lock:
                self._clients.pop(client_id, None)
            _close_quietly(client)


def _close_quietly(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


# -- game service ---------------------------------------------------------


class GameService:
    """World state and actions shared by the MCP tools."""

    def __init__(
        self,
        broker: Broker,
        map_file: str | None = None,
        log_file: str | None = None,
    ) -> None:
        self.broker = broker
        self.engine = Engine()
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.tracked: deque[Packet] = deque(maxlen=MAX_TRACKED_PACKETS)
        self.statuses: deque[tuple[float, str]] = deque(
            maxlen=MAX_TRACKED_STATUSES
        )
        self.map_file = map_file
        self.log_file = log_file or str(default_log_path() or "")
        self._grids: dict[int, MapGrid] | None = None
        self._spells: dict[int, SpellInfo] | None = None
        self._names: dict[int, str] | None = None
        self._log_vnums: dict[int, int] = {}
        self.portals: PortalMap = normalize_portals(load_portals())

        broker.on_packet = self._feed_packet
        broker.on_status = self._feed_status
        self._seed_from_log()
        # The GUI polls POS; the MCP server must too, otherwise `resting` and
        # the current real cell are unknown and MoveTo arrival cannot be
        # checked (the client's `walk` reports the destination, not the cell).
        self._pos_stop = threading.Event()
        threading.Thread(
            target=self._pos_loop, name="ntpl-pos", daemon=True
        ).start()

    # -- feeds ----------------------------------------------------------

    def _pos_loop(self) -> None:
        while not self._pos_stop.is_set():
            if self.broker.connected:
                self.broker.send_upstream("POS")
            self._pos_stop.wait(0.5)

    def _feed_packet(self, packet: Packet) -> None:
        with self.cond:
            self.engine.feed(packet)
            self.tracked.append(packet)
            self.cond.notify_all()

    def _feed_status(self, text: str) -> None:
        with self.cond:
            self.statuses.append((time.time(), text))
            if text.startswith("POS "):
                parts = text.split()
                if len(parts) >= 4:
                    try:
                        x, y, char_id = (
                            int(parts[1]),
                            int(parts[2]),
                            int(parts[3]),
                        )
                    except ValueError:
                        pass
                    else:
                        world = self.engine.world
                        world.x, world.y = x, y
                        world.self_id = char_id
                        if len(parts) >= 5 and parts[4] in ("0", "1"):
                            world.resting = parts[4] == "1"
            self.cond.notify_all()

    def _seed_from_log(self) -> None:
        """Recover map, position, entity vnums and portals from the DLL log."""
        if not self.log_file:
            return
        try:
            from .spawnlog import load_spawns

            self._log_vnums, _names = load_spawns(self.log_file)
        except (OSError, ValueError):
            return
        map_id, x, y, char_id = load_last_position(self.log_file)
        with self.lock:
            world = self.engine.world
            if world.map_id is None and map_id is not None:
                world.map_id = map_id
                if x is not None and y is not None:
                    world.x, world.y = x, y
                if char_id is not None:
                    world.self_id = char_id
        for (gate_map, gx, gy), destination in load_gates(
            self.log_file
        ).items():
            set_gate(self.portals, gate_map, gx, gy, destination)
        for (portal_map, px, py), destination in load_log_portals(
            self.log_file
        ).items():
            add_portal(self.portals, portal_map, px, py, destination)
        self.portals = normalize_portals(self.portals)

    # -- lazy data ------------------------------------------------------

    @property
    def grids(self) -> dict[int, MapGrid]:
        if self._grids is None:
            try:
                self._grids = load_nstc(self.map_file)
            except Exception:  # noqa: BLE001 - a missing archive is not fatal
                self._grids = {}
        return self._grids

    @property
    def spells(self) -> dict[int, SpellInfo]:
        if self._spells is None:
            try:
                self._spells = load_spells()
            except Exception:  # noqa: BLE001
                self._spells = {}
        return self._spells

    @property
    def names(self) -> dict[int, str]:
        if self._names is None:
            try:
                self._names = load_monster_names()
            except Exception:  # noqa: BLE001
                self._names = {}
        return self._names

    def _world(self):
        return self.engine.world

    def _char_id(self) -> int | None:
        return self._world().self_id

    def _entity_vnum(self, entity) -> int:
        return entity.vnum or self._log_vnums.get(entity.entity_id, 0)

    def _require_connection(self) -> None:
        if not self.broker.connected:
            raise ToolError(
                "not connected to the DLL; is the game running and the "
                "logger injected?"
            )

    def _send(self, line: str) -> None:
        self._require_connection()
        if not self.broker.send_upstream(line):
            raise ToolError("the DLL connection dropped while sending")

    def _send_packet(self, packet: str) -> None:
        """Inject one game packet; the DLL requires the `SEND|` prefix."""
        self._send(f"SEND|{packet}")

    def _wait(
        self, predicate: Callable[[], Any], timeout: float
    ) -> Any | None:
        deadline = time.monotonic() + max(0.0, timeout)
        with self.cond:
            while True:
                value = predicate()
                if value:
                    return value
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.cond.wait(min(remaining, 0.5))

    # -- read tools -----------------------------------------------------

    def game_state(self) -> dict[str, Any]:
        with self.lock:
            world = self._world()
            stats = world.stats
            return {
                "connected": self.broker.connected,
                "broker_status": self.broker.last_status,
                "map_id": world.map_id,
                "map": self._map_summary(world.map_id),
                "position": list(world.position) if world.position else None,
                "self_id": world.self_id,
                "hp": stats.hp,
                "hp_max": stats.hp_max,
                "mp": stats.mp,
                "mp_max": stats.mp_max,
                "resting": world.resting,
                "target_id": world.target_id,
                "last_skill": world.last_skill,
                "basic_skill": world.basic_skill,
                "skill_list": list(world.skill_list),
                "entities": len(world.entities),
                "drops": len(world.drops),
                "portals": self._portal_rows(world.map_id),
                "timespaces": self._timespace_rows(world.map_id),
            }

    def game_entities(
        self,
        kind: int | None = None,
        radius: int | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        with self.lock:
            world = self._world()
            own = world.position
            rows: list[dict[str, Any]] = []
            for entity in world.entities.values():
                if kind is not None and entity.kind != kind:
                    continue
                distance = None
                if own is not None:
                    distance = max(
                        abs(entity.x - own[0]), abs(entity.y - own[1])
                    )
                if radius is not None and (
                    distance is None or distance > radius
                ):
                    continue
                vnum = self._entity_vnum(entity)
                rows.append(
                    {
                        "entity_id": entity.entity_id,
                        "kind": entity.kind,
                        "vnum": vnum,
                        "name": entity.name
                        or self.names.get(vnum, ""),
                        "x": entity.x,
                        "y": entity.y,
                        "distance": distance,
                    }
                )
            rows.sort(
                key=lambda row: (
                    row["distance"] is None,
                    row["distance"] if row["distance"] is not None else 0,
                    row["entity_id"],
                )
            )
            return {
                "count": len(rows),
                "entities": rows[: max(1, min(limit, 1000))],
            }

    def game_drops(self, radius: int | None = None) -> dict[str, Any]:
        with self.lock:
            world = self._world()
            own = world.position
            rows = []
            for drop in world.drops.values():
                distance = None
                if own is not None:
                    distance = max(abs(drop.x - own[0]), abs(drop.y - own[1]))
                if radius is not None and (
                    distance is None or distance > radius
                ):
                    continue
                rows.append(
                    {
                        "drop_id": drop.drop_id,
                        "item_vnum": drop.item_vnum,
                        "x": drop.x,
                        "y": drop.y,
                        "amount": drop.amount,
                        "owner_id": drop.owner_id,
                        "distance": distance,
                    }
                )
            rows.sort(key=lambda row: (row["distance"] or 0, row["drop_id"]))
            return {"count": len(rows), "drops": rows}

    def game_timespaces(self, map_id: int | None = None) -> dict[str, Any]:
        with self.lock:
            current = self._world().map_id
        target = current if map_id is None else map_id
        return {
            "map_id": target,
            "timespaces": self._timespace_rows(target),
        }

    def game_map(self, map_id: int | None = None) -> dict[str, Any]:
        with self.lock:
            current = self._world().map_id
        return self._map_summary(current if map_id is None else map_id)

    def game_portals(self, map_id: int | None = None) -> dict[str, Any]:
        with self.lock:
            current = self._world().map_id
        target = current if map_id is None else map_id
        return {"map_id": target, "portals": self._portal_rows(target)}

    def game_path(
        self,
        x: int,
        y: int,
        from_x: int | None = None,
        from_y: int | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            world = self._world()
            map_id = world.map_id
            own = world.position
        grid = self.grids.get(map_id) if map_id is not None else None
        if grid is None:
            raise ToolError(f"no map grid for map {map_id}")
        if from_x is not None and from_y is not None:
            start = (from_x, from_y)
        elif own is not None:
            start = own
        else:
            raise ToolError("own position unknown; pass from_x/from_y")
        goal = (x, y)
        if not (0 <= x < grid.width and 0 <= y < grid.height):
            raise ToolError(f"cell {goal} is outside map {map_id}")
        if not grid.is_walkable(x, y):
            raise ToolError(f"cell {goal} is blocked on map {map_id}")
        path = astar(grid, start, goal)
        if path is None:
            return {"found": False, "start": list(start), "goal": list(goal)}
        return {
            "found": True,
            "start": list(start),
            "goal": list(goal),
            "cells": len(path),
            "path": [list(cell) for cell in path],
        }

    def game_spells(self) -> dict[str, Any]:
        with self.lock:
            world = self._world()
            learned = list(world.skill_list)
            basic = world.basic_skill
        rows = []
        for vnum in learned:
            info = self.spells.get(vnum)
            rows.append(
                {
                    "vnum": vnum,
                    "slot": self._world().skill_slot(vnum),
                    "name": info.name if info else "",
                    "range": info.range if info else None,
                    "cast_time": info.cast_time if info else None,
                    "cooldown": info.cooldown if info else None,
                    "mp_cost": info.mp_cost if info else None,
                }
            )
        return {"basic_skill": basic, "learned": rows}

    # -- action tools ---------------------------------------------------

    def game_move(self, x: int, y: int, timeout: float = 10.0) -> dict[str, Any]:
        self._ensure_standing(min(max(timeout, 1.0), 3.0))
        with self.lock:
            here = self.engine.world.position
        if here is not None:
            distance = max(abs(x - here[0]), abs(y - here[1]))
            if distance > 3:
                # A far MoveTo is rejected by the server (the client stops
                # without walking). Use the same <=3-cell legs as the GUI.
                return self.game_travel(
                    x, y, leg=3, timeout=max(timeout, distance * 1.5)
                )
        self._send(f"MOVE|{x} {y}")

        def arrived():
            return self._world().position == (x, y)

        if self._wait(arrived, timeout) is None:
            raise ToolError(
                f"did not reach {x},{y} within {timeout:g}s "
                f"(position {self._world().position})"
            )
        return {"ok": True, "position": [x, y]}

    def _ensure_standing(self, timeout: float = 3.0) -> None:
        """Stand before moving: a seated client ignores MoveTo."""
        def known() -> bool:
            with self.lock:
                return self.engine.world.resting is not None

        self._wait(known, 1.0)
        with self.lock:
            world = self.engine.world
            resting = world.resting
            char_id = world.self_id
        if resting is not True:
            return
        if char_id is None:
            raise ToolError("character is sitting and the char id is unknown")
        self._send_packet(f"rest 1 1 {char_id}")

        def stood() -> bool:
            with self.lock:
                return self.engine.world.resting is not True

        if self._wait(stood, timeout) is None:
            raise ToolError(
                "the character is sitting and did not stand up; "
                "`rest` is a toggle, check the game"
            )

    def game_travel(
        self,
        x: int,
        y: int,
        leg: int = 4,
        timeout: float = 45.0,
    ) -> dict[str, Any]:
        """A* to a cell and walk it in short client-MoveTo legs."""
        path_result = self.game_path(x, y)
        if not path_result["found"]:
            raise ToolError(f"no path to {x},{y} on map {self._world().map_id}")
        path = [tuple(cell) for cell in path_result["path"]]
        if not path:
            return {"ok": True, "position": [x, y], "legs": 0}
        legs = 0
        deadline = time.monotonic() + timeout
        for index in range(max(0, leg) - 1, len(path), max(1, leg)):
            target = path[index]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ToolError(
                    f"travel timed out before {target}; "
                    f"position {self._world().position}"
                )
            self.game_move(target[0], target[1], min(remaining, 12.0))
            legs += 1
        final = path[-1]
        if final != (x, y):
            self.game_move(x, y, min(deadline - time.monotonic(), 12.0))
            legs += 1
        return {
            "ok": True,
            "position": list(self._world().position or (x, y)),
            "legs": legs,
        }

    def game_attack(
        self,
        target_id: int,
        slot: int = 0,
        timeout: float = 6.0,
    ) -> dict[str, Any]:
        return self._cast(slot, 3, target_id, timeout)

    def game_cast(
        self,
        slot: int,
        target_id: int,
        target_type: int = 3,
        timeout: float = 6.0,
    ) -> dict[str, Any]:
        return self._cast(slot, target_type, target_id, timeout)

    def game_cast_spell(
        self,
        vnum: int,
        target_id: int | None = None,
        target_type: int = 3,
        timeout: float = 6.0,
    ) -> dict[str, Any]:
        with self.lock:
            world = self._world()
            char_id = world.self_id
            slot = world.skill_slot(vnum)
        if slot is None:
            raise ToolError(
                f"spell {vnum} is not in the learned skill list; cast it "
                "once in game or relog with the logger attached"
            )
        if target_type == 1:
            if target_id is None:
                target_id = char_id
            if target_id is None:
                raise ToolError("own char id unknown for a self-target spell")
        elif target_id is None:
            raise ToolError("target_id is required for a monster spell")
        return self._cast(slot, target_type, target_id, timeout)

    def _cast(
        self,
        slot: int,
        target_type: int,
        target_id: int,
        timeout: float,
    ) -> dict[str, Any]:
        with self.lock:
            self_id = self._world().self_id
        start = time.time()
        if target_type == 3:
            # The client has to select the monster itself (target ring +
            # its own ncif); the server validates u_s against that.
            self._select_target(target_id)
        else:
            self._send_packet(f"ncif {target_type} {target_id}")
        self._send_packet(f"u_s {slot} {target_type} {target_id}")

        def confirmed():
            with self.lock:
                for packet in reversed(self.tracked):
                    if packet.timestamp < start:
                        break
                    if packet.opcode != "su":
                        continue
                    values = packet.ints(5)
                    if not values:
                        continue
                    _attacker_type, attacker_id, _target_type, tid, skill = (
                        values
                    )
                    if attacker_id != self_id:
                        continue
                    if tid == target_id or tid == 0:
                        return (attacker_id, tid, skill, packet.text)
            return None

        hit = self._wait(confirmed, timeout)
        if hit is None:
            raise ToolError(
                f"no `su` confirmation for u_s {slot} {target_type} "
                f"{target_id} within {timeout:g}s"
            )
        attacker_id, tid, skill, text = hit
        return {
            "ok": True,
            "slot": slot,
            "target_id": tid,
            "skill_vnum": skill,
            "attacker_id": attacker_id,
            "su": text,
        }

    def game_send(self, packet: str) -> dict[str, Any]:
        text = packet.strip()
        if not text:
            raise ToolError("packet is empty")
        self._send_packet(text)
        return {"ok": True, "sent": text}

    def game_select(self, target_id: int, target_type: int = 3) -> dict[str, Any]:
        if target_type == 3:
            result = self._select_target(target_id)
            result["target_type"] = target_type
            return result
        self._send_packet(f"ncif {target_type} {target_id}")
        return {"ok": True, "target_id": target_id, "target_type": target_type}

    def _select_target(
        self, target_id: int, timeout: float = 5.0
    ) -> dict[str, Any]:
        """Select an entity through the client's own selector (TARGET).

        An injected ncif only tells the server; the client keeps its old
        selection and the server then rejects/redirects the next u_s. TARGET
        makes the client change its selection and send its own ncif.
        """
        start = time.time()
        self._send(f"TARGET|{target_id}")

        def answered():
            with self.lock:
                for stamp, text in reversed(self.statuses):
                    if stamp < start:
                        break
                    if text.startswith("TARGET "):
                        return text
            return None

        text = self._wait(answered, timeout)
        if text is None:
            raise ToolError(
                f"no TARGET answer from the DLL for entity {target_id}"
            )
        parts = text.split()
        if len(parts) < 3 or parts[2] != "OK":
            if "entity not found" in text or "client refused" in text:
                # The client removed the object (killed monsters get no
                # `out` packet); drop it so later calls pick a live target.
                with self.lock:
                    self.engine.world.entities.pop(target_id, None)
            raise ToolError(text)
        return {"ok": True, "target_id": target_id, "status": text}

    def game_rest(self, sit: bool | None = None) -> dict[str, Any]:
        with self.lock:
            char_id = self._world().self_id
            resting = self._world().resting
        if char_id is None:
            raise ToolError("own char id unknown")
        if sit is not None and resting is sit:
            return {"ok": True, "resting": resting, "changed": False}
        expected = (not bool(resting)) if sit is None else sit
        self._send_packet(f"rest 1 1 {char_id}")

        def settled():
            with self.lock:
                return self._world().resting is expected

        state = self._wait(settled, 3.0)
        return {
            "ok": state is True,
            "resting": self._world().resting,
            "changed": True,
        }

    def game_pickup(self, drop_id: int, timeout: float = 5.0) -> dict[str, Any]:
        with self.lock:
            char_id = self._world().self_id
        if char_id is None:
            raise ToolError("own char id unknown")
        start = time.time()
        self._send_packet(f"get 1 {char_id} {drop_id}")

        def confirmed():
            with self.lock:
                if drop_id not in self._world().drops:
                    return True
                for packet in reversed(self.tracked):
                    if packet.timestamp < start:
                        break
                    if packet.opcode == "get":
                        values = packet.ints(3)
                        if values and values[2] == drop_id:
                            return True
            return False

        if self._wait(confirmed, timeout) is None:
            raise ToolError(f"no `get` confirmation for drop {drop_id}")
        return {"ok": True, "drop_id": drop_id}

    def game_wait_for(
        self,
        opcode: str,
        contains: str = "",
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        start = time.time()

        def matched():
            with self.lock:
                for packet in reversed(self.tracked):
                    if packet.timestamp < start:
                        break
                    if packet.opcode == opcode and contains in packet.text:
                        return packet.text
            return None

        text = self._wait(matched, timeout)
        if text is None:
            raise ToolError(
                f"no {opcode!r} packet containing {contains!r} within "
                f"{timeout:g}s"
            )
        return {"ok": True, "packet": text}

    def game_status(self) -> dict[str, Any]:
        return {
            "connected": self.broker.connected,
            "status": self.broker.last_status,
        }

    def game_memory_find(self, value: int, timeout: float = 3.0) -> dict[str, Any]:
        return self._memory("FIND", f"FIND|{int(value)}", timeout)

    def game_memory_read(
        self, address: int, length: int = 32, timeout: float = 3.0
    ) -> dict[str, Any]:
        length = max(1, min(int(length), 256))
        return self._memory(
            "READ", f"READ|0x{int(address):08X} {length}", timeout
        )

    def _memory(
        self, prefix: str, command: str, timeout: float
    ) -> dict[str, Any]:
        start = time.time()
        self._send(command)

        def answered():
            with self.lock:
                for stamp, text in reversed(self.statuses):
                    if stamp < start:
                        break
                    if text.startswith(prefix):
                        return text
            return None

        text = self._wait(answered, timeout)
        if text is None:
            raise ToolError(f"no {prefix} answer from the DLL")
        parts = text.split()
        if len(parts) > 1 and parts[1] == "failed":
            raise ToolError(text)
        return {"ok": True, "status": text}

    # -- helpers --------------------------------------------------------

    def _map_summary(self, map_id: int | None) -> dict[str, Any] | None:
        if map_id is None:
            return None
        grid = self.grids.get(map_id)
        if grid is None:
            return {"map_id": map_id, "grid": False}
        total = grid.width * grid.height
        walkable = grid.walkable_count()
        return {
            "map_id": map_id,
            "width": grid.width,
            "height": grid.height,
            "walkable": walkable,
            "walkable_pct": round(100.0 * walkable / total, 1),
        }

    def _portal_rows(self, map_id: int | None) -> list[dict[str, int]]:
        if map_id is None:
            return []
        rows = []
        for (portal_map, x, y), destination in self.portals.items():
            if portal_map == map_id:
                rows.append({"x": x, "y": y, "to_map": destination})
        rows.sort(key=lambda row: (row["y"], row["x"]))
        return rows

    def _timespace_rows(self, map_id: int | None) -> list[dict[str, int]]:
        if map_id is None:
            return []
        rows = []
        with self.lock:
            for (ts_map, x, y), entry in self.engine.world.timespaces.items():
                if ts_map == map_id:
                    rows.append(
                        {
                            "ts_id": entry.ts_id,
                            "x": x,
                            "y": y,
                            "min_level": entry.min_level,
                            "max_level": entry.max_level,
                        }
                    )
        rows.sort(key=lambda row: (row["y"], row["x"]))
        return rows


# -- MCP protocol ---------------------------------------------------------

TOOL_METHODS = {
    "game_state": "game_state",
    "game_entities": "game_entities",
    "game_drops": "game_drops",
    "game_map": "game_map",
    "game_portals": "game_portals",
    "game_timespaces": "game_timespaces",
    "game_path": "game_path",
    "game_spells": "game_spells",
    "game_move": "game_move",
    "game_travel": "game_travel",
    "game_attack": "game_attack",
    "game_cast": "game_cast",
    "game_cast_spell": "game_cast_spell",
    "game_send": "game_send",
    "game_select": "game_select",
    "game_rest": "game_rest",
    "game_pickup": "game_pickup",
    "game_wait_for": "game_wait_for",
    "game_status": "game_status",
    "game_memory_find": "game_memory_find",
    "game_memory_read": "game_memory_read",
}


def _schema(properties: dict[str, Any], required: list[str] | None = None):
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "game_state",
        "description": (
            "Current world snapshot: map id/size, position, HP/MP, resting "
            "state, learned skills, entity/drop counts and this map's portals."
        ),
        "inputSchema": _schema({}),
    },
    {
        "name": "game_entities",
        "description": (
            "Live entities (1 player, 2 NPC/pet, 3 monster) with vnum, name, "
            "position, distance from you, sorted by distance."
        ),
        "inputSchema": _schema(
            {
                "kind": {"type": "integer", "minimum": 1, "maximum": 3},
                "radius": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            }
        ),
    },
    {
        "name": "game_drops",
        "description": "Ground items with id, item vnum, position and owner.",
        "inputSchema": _schema({"radius": {"type": "integer", "minimum": 0}}),
    },
    {
        "name": "game_map",
        "description": "Map grid size and walkable percentage (current or given id).",
        "inputSchema": _schema({"map_id": {"type": "integer", "minimum": 1}}),
    },
    {
        "name": "game_portals",
        "description": (
            "Portals on the current (or given) map. The server sends the full "
            "list in gp packets; learned crossings fill gaps."
        ),
        "inputSchema": _schema({"map_id": {"type": "integer", "minimum": 1}}),
    },
    {
        "name": "game_timespaces",
        "description": (
            "Time-Space entrances on the current (or given) map from the "
            "server's wp list: TS id, position and level range."
        ),
        "inputSchema": _schema({"map_id": {"type": "integer", "minimum": 1}}),
    },
    {
        "name": "game_path",
        "description": (
            "8-direction A* path over walkable cells. Defaults to your "
            "position; returns the cell list."
        ),
        "inputSchema": _schema(
            {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "from_x": {"type": "integer"},
                "from_y": {"type": "integer"},
            },
            ["x", "y"],
        ),
    },
    {
        "name": "game_spells",
        "description": (
            "Learned skills in u_s slot order with Skill.dat range, cast "
            "time, cooldown and MP; basic_skill is the SP card's slot-0 skill."
        ),
        "inputSchema": _schema({}),
    },
    {
        "name": "game_move",
        "description": (
            "Move to a cell through the client's own MoveTo (pathfound and "
            "animated by the game) and wait until the position matches."
        ),
        "inputSchema": _schema(
            {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "timeout": {"type": "number", "minimum": 0.5},
            },
            ["x", "y"],
        ),
    },
    {
        "name": "game_travel",
        "description": (
            "A* to a cell and walk it in short MoveTo legs; the safe way to "
            "cross a whole map."
        ),
        "inputSchema": _schema(
            {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "leg": {"type": "integer", "minimum": 1, "maximum": 20},
                "timeout": {"type": "number", "minimum": 1},
            },
            ["x", "y"],
        ),
    },
    {
        "name": "game_attack",
        "description": (
            "Normal attack: the client selects the monster (TARGET, target ring + "
            "its own ncif), then u_s <slot> 3 <entityId>; waits for the "
            "server's su confirmation and returns the skill vnum it reports."
        ),
        "inputSchema": _schema(
            {
                "target_id": {"type": "integer"},
                "slot": {"type": "integer", "minimum": 0},
                "timeout": {"type": "number", "minimum": 0.5},
            },
            ["target_id"],
        ),
    },
    {
        "name": "game_cast",
        "description": "Cast by zero-based u_s slot at a target type/id.",
        "inputSchema": _schema(
            {
                "slot": {"type": "integer", "minimum": 0},
                "target_id": {"type": "integer"},
                "target_type": {"type": "integer", "minimum": 0, "maximum": 3},
                "timeout": {"type": "number", "minimum": 0.5},
            },
            ["slot", "target_id"],
        ),
    },
    {
        "name": "game_cast_spell",
        "description": (
            "Cast by Skill.dat vnum; resolves the learned u_s slot and waits "
            "for su. Errors when the vnum is not in the learned list."
        ),
        "inputSchema": _schema(
            {
                "vnum": {"type": "integer", "minimum": 1},
                "target_id": {"type": "integer"},
                "target_type": {"type": "integer", "minimum": 0, "maximum": 3},
                "timeout": {"type": "number", "minimum": 0.5},
            },
            ["vnum"],
        ),
    },
    {
        "name": "game_send",
        "description": "Send one raw game packet (e.g. 'walk 19 146 0 10').",
        "inputSchema": _schema({"packet": {"type": "string"}}, ["packet"]),
    },
    {
        "name": "game_select",
        "description": (
            "Select a monster through the client itself (TARGET: target "
            "ring + its own ncif); target_type 1/2 use an injected ncif."
        ),
        "inputSchema": _schema(
            {
                "target_id": {"type": "integer"},
                "target_type": {"type": "integer", "minimum": 1, "maximum": 3},
            },
            ["target_id"],
        ),
    },
    {
        "name": "game_rest",
        "description": "Sit or stand (toggles the client's rest).",
        "inputSchema": _schema({"sit": {"type": "boolean"}}),
    },
    {
        "name": "game_pickup",
        "description": "Pick up one drop: get 1 <charId> <dropId>, waits for get.",
        "inputSchema": _schema({"drop_id": {"type": "integer"}}, ["drop_id"]),
    },
    {
        "name": "game_wait_for",
        "description": (
            "Wait for a packet by opcode (and optional substring), e.g. "
            "opcode 'c_map' after walking through a portal."
        ),
        "inputSchema": _schema(
            {
                "opcode": {"type": "string"},
                "contains": {"type": "string"},
                "timeout": {"type": "number", "minimum": 0.5},
            },
            ["opcode"],
        ),
    },
    {
        "name": "game_status",
        "description": "DLL connection state and the last STATUS line.",
        "inputSchema": _schema({}),
    },
    {
        "name": "game_memory_find",
        "description": "Read-only debug: find a 32-bit value in game memory.",
        "inputSchema": _schema({"value": {"type": "integer"}}, ["value"]),
    },
    {
        "name": "game_memory_read",
        "description": "Read-only debug: dump up to 256 bytes at an address.",
        "inputSchema": _schema(
            {
                "address": {"type": "integer", "minimum": 0},
                "length": {"type": "integer", "minimum": 1, "maximum": 256},
            },
            ["address"],
        ),
    },
]

RESOURCES = [
    {
        "uri": "ntpl://game/state",
        "name": "Game state",
        "description": "The game_state tool output as JSON.",
        "mimeType": "application/json",
    }
]


class McpServer:
    """JSON-RPC 2.0 MCP server over the given text streams."""

    def __init__(self, service: GameService) -> None:
        self.service = service

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")

        if method == "initialize":
            params = request.get("params") or {}
            version = params.get("protocolVersion") or MCP_VERSION
            return _result(
                request_id,
                {
                    "protocolVersion": version,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"listChanged": False},
                    },
                    "serverInfo": {"name": "ntpl", "version": __version__},
                    "instructions": (
                        "NosTale packet-engine tools. Read game_state and "
                        "game_entities, then move/attack/cast. Movement goes "
                        "through the client's own MoveTo (game_move/game_travel) "
                        "and is confirmed by the walk packet. Attacks are "
                        "confirmed by the server's `su`."
                    ),
                },
            )
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": TOOLS})
        if method == "resources/list":
            return _result(request_id, {"resources": RESOURCES})
        if method == "resources/read":
            params = request.get("params") or {}
            if params.get("uri") == "ntpl://game/state":
                payload = json.dumps(
                    self.service.game_state(), indent=2
                )
                return _result(
                    request_id,
                    {
                        "contents": [
                            {
                                "uri": "ntpl://game/state",
                                "mimeType": "application/json",
                                "text": payload,
                            }
                        ]
                    },
                )
            return _error(request_id, -32602, f"unknown resource")
        if method == "tools/call":
            return self._call_tool(request_id, request.get("params") or {})
        if method is None:
            return _error(request_id, -32600, "missing method")
        return _error(request_id, -32601, f"method not found: {method}")

    def _call_tool(
        self, request_id: Any, params: dict[str, Any]
    ) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        method = TOOL_METHODS.get(name or "")
        if method is None:
            return _error(request_id, -32602, f"unknown tool: {name}")
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "arguments must be an object")
        try:
            payload = getattr(self.service, method)(**arguments)
        except ToolError as exc:
            return _result(
                request_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
        except TypeError as exc:
            return _error(request_id, -32602, f"bad arguments: {exc}")
        except Exception as exc:  # noqa: BLE001 - never kill the server
            return _result(
                request_id,
                {
                    "content": [
                        {"type": "text", "text": f"{type(exc).__name__}: {exc}"}
                    ],
                    "isError": True,
                },
            )
        return _result(
            request_id,
            {
                "content": [
                    {"type": "text", "text": json.dumps(payload, indent=2)}
                ],
                "isError": False,
            },
        )

    def serve(self, reader: TextIO, writer: TextIO) -> None:
        for line in reader:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except ValueError:
                response = _error(None, -32700, "parse error")
            else:
                if isinstance(request, list):
                    answers = [
                        answer
                        for answer in (
                            self.handle(item) for item in request
                        )
                        if answer is not None
                    ]
                    response = answers or None
                else:
                    response = self.handle(request)
            if response is not None:
                writer.write(json.dumps(response) + "\n")
                writer.flush()


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(
    request_id: Any, code: int, message: str
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


# -- entry point ----------------------------------------------------------


def run_mcp(
    host: str = DEFAULT_HOST,
    port: int | None = None,
    listen_port: int = BROKER_PORT,
    map_file: str | None = None,
    log_file: str | None = None,
) -> int:
    """Start the broker + MCP stdio server; returns when stdin closes.

    ``listen_port=0`` disables the GUI/CLI rebroadcast (the agent then owns
    the only DLL connection); any other value is the TCP port to listen on.
    """
    if port is None:
        port = _discover_dll_port()
    broker = Broker(
        host, port, listen_port=None if listen_port == 0 else listen_port
    )
    print(
        f"ntpl mcp: DLL 127.0.0.1:{broker.port}; "
        f"broker {broker.listen_port if broker.listen_port else 'off'}",
        file=sys.stderr,
    )
    service = GameService(broker, map_file=map_file, log_file=log_file)
    broker.start()
    server = McpServer(service)
    try:
        server.serve(sys.stdin, sys.stdout)
    except KeyboardInterrupt:
        pass
    finally:
        broker.close()
    return 0
