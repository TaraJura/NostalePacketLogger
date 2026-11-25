"""Recover entity ids from the DLL packet log.

A host that attaches after login only sees `mv`/`tp` for monsters and players
that spawned earlier, so their `Entity.vnum` and player name stay empty. The
DLL's packet log was opened when the DLL was injected and contains the whole
current session, including those `in` spawn packets. Reading only the
`[RECV] in ...` lines gives the missing mapping:

    in 3 <vnum> <entityId> <x> <y> ...     monster/NPC
    in 1 <name> - <charId> <x> <y> ...     player

Entity ids are per-session. Each DLL writes its own log
(``C:\\NosTalePacketLog-<pid>.txt``), truncated when that client is injected,
so the mapping belongs to one live client session even when several clients
run in the same Wine prefix. Later entries override earlier ones, which
handles a reused entity id.
"""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import time
from pathlib import Path

from .bazar import BAZAR_SKILL_WINDOW_S
from .engine import InventorySlot, parse_inventory_token

_IN_MARKER = "[RECV] in "
_GIDX_MARKER = "[RECV] gidx "
_PAIRY_MARKER = "[RECV] pairy "
_SKI_MARKER = "[RECV] ski "
_AT_MARKER = "[RECV] at "
_US_MARKER = "[SEND] u_s "
_SU_MARKER = "[RECV] su "
_WALK_MARKER = "[SEND] walk "
_CMAP_MARKER = "[RECV] c_map "
_MAPOUT_MARKER = "[RECV] mapout"
_OUT_MARKER = "[RECV] out "
_MV_MARKER = "[RECV] mv "
_TP_MARKER = "[RECV] tp "
_GP_MARKER = "[RECV] gp "
_WP_MARKER = "[RECV] wp "
_NPCREQ_MARKER = "[SEND] npc_req "
_NRUN_MARKER = "[SEND] n_run "
_CBLIST_MARKER = "[SEND] c_blist "
_CSLIST_MARKER = "[SEND] c_slist "
_CSKILL_MARKER = "[SEND] c_skill"
_WOPEN32_MARKER = "[RECV] wopen 32 "
_LINE_STAMP = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})\.(\d{3}) ")
_INV_MARKER = "[RECV] inv "
_IVN_MARKER = "[RECV] ivn "
_GOLD_MARKER = "[RECV] gold "
_ENTITY = re.compile(r"^([23]) (\d+) (\d+) ")
_PLAYER = re.compile(r"^1 (.+?) - (\d+) ")

#: Backfill reads only the recent part of a session log. A long-running
#: client's file can reach hundreds of megabytes (multi-hour farming), and
#: every GUI attach used to scan all of it several times on the GTK main
#: thread — minutes of "Not Responding". The login `ski` list is read from
#: the head instead; everything else only needs the current session's tail.
LOG_TAIL_BYTES = 8 * 1024 * 1024


def log_was_truncated(path: str | Path | None) -> bool:
    """True when the log file starts with the DLL's truncation notice.

    The DLL rewrites the file in place at 16 MB, so the login inventory dump
    (and everything else before the cut) is gone. The host can then say why
    the inventory is missing instead of guessing.
    """
    if path is None:
        return False
    try:
        with open(path, "rb") as handle:
            head = handle.readline(200).decode("latin1", "replace")
    except OSError:
        return False
    return "[STATUS] log truncated" in head


def _open_log_tail(
    source: Path, tail_bytes: int = LOG_TAIL_BYTES
) -> io.TextIOWrapper:
    handle = open(source, "rb")
    try:
        size = os.fstat(handle.fileno()).st_size
        if size > tail_bytes:
            handle.seek(size - tail_bytes)
            handle.readline()  # drop the partial line at the cut
        return io.TextIOWrapper(handle, encoding="latin1", errors="replace")
    except OSError:
        handle.close()
        raise


def _log_head_lines(
    source: Path, head_bytes: int = LOG_TAIL_BYTES
) -> list[str]:
    """Lines from the start of the log (the login-time `ski` list)."""
    with open(source, "rb") as handle:
        data = handle.read(head_bytes)
    if len(data) == head_bytes and not data.endswith(b"\n"):
        cut = data.rfind(b"\n")
        data = data[: cut + 1] if cut >= 0 else b""
    return data.decode("latin1", "replace").splitlines()


def _ints_after(line: str, marker: str, count: int) -> list[int]:
    """The first ``count`` integers after ``marker``, or ``[]``."""
    start = line.find(marker)
    if start < 0:
        return []
    values: list[int] = []
    for token in line[start + len(marker) :].split():
        try:
            values.append(int(token))
        except ValueError:
            break
        if len(values) == count:
            return values
    return []


def client_log_path(prefix: str | os.PathLike[str], winpid: int) -> Path:
    """The DLL log of one game process: ``C:\\NosTalePacketLog-<pid>.txt``.

    Each injected DLL writes its own log under the prefix's ``drive_c``, named
    by the Windows PID that the injector reports and that also names the
    ``ntpl-<pid>.port`` file. Several clients in one prefix therefore never
    share, truncate or interleave a log.
    """
    return Path(prefix) / "drive_c" / f"NosTalePacketLog-{winpid}.txt"


def _newest_client_log(prefix: Path) -> Path | None:
    """Newest ``NosTalePacketLog-<pid>.txt`` in a prefix, if any."""
    try:
        logs = [
            entry
            for entry in (prefix / "drive_c").glob("NosTalePacketLog-*.txt")
            if entry.is_file()
        ]
    except OSError:
        return None
    if not logs:
        return None
    try:
        return max(logs, key=lambda entry: entry.stat().st_mtime_ns)
    except OSError:
        return logs[0]


def default_log_path() -> Path | None:
    """Locate the best DLL packet log inside a Wine prefix.

    ``NTPL_LOG`` wins. Otherwise the newest per-client log
    (``C:\\NosTalePacketLog-<pid>.txt``) is preferred, with the legacy single
    ``C:\\NosTalePacketLog.txt`` as a fallback. Prefixes are checked in this
    order: ``WINEPREFIX``, the dedicated ``~/.wine-nostale`` prefix, then the
    Gameforge prefix on this machine. Returns None when no log exists.
    """
    override = os.environ.get("NTPL_LOG")
    if override:
        return Path(override)

    prefixes: list[Path] = []
    prefix = os.environ.get("WINEPREFIX")
    if prefix:
        prefixes.append(Path(prefix))
    home = Path.home()
    prefixes.extend(
        [
            home / ".wine-nostale",
            home / "games/gameforge/prefix",
        ]
    )
    for prefix in prefixes:
        newest = _newest_client_log(prefix)
        if newest is not None:
            return newest
        legacy = prefix / "drive_c" / "NosTalePacketLog.txt"
        if legacy.is_file():
            return legacy
    return None


def load_spawns(
    path: str | Path | None = None,
) -> tuple[dict[int, int], dict[int, str]]:
    """Return ``(entity_vnums, player_names)`` from a packet log.

    Missing or unreadable logs yield empty mappings; the caller then falls
    back to whatever the live packet stream has already provided.
    """
    source = Path(path) if path is not None else default_log_path()
    if source is None:
        return {}, {}

    vnums: dict[int, int] = {}
    names: dict[int, str] = {}
    try:
        log = _open_log_tail(source)
    except OSError:
        return vnums, names

    with log:
        for line in log:
            marker = line.find(_IN_MARKER)
            if marker < 0:
                continue
            record = line[marker + len(_IN_MARKER) :].strip()

            match = _ENTITY.match(record)
            if match:
                entity_id = int(match.group(3))
                vnums[entity_id] = int(match.group(2))
                continue

            match = _PLAYER.match(record)
            if match:
                names[int(match.group(2))] = match.group(1).strip()

    return vnums, names


def load_self_name(
    char_id: int, path: str | Path | None = None
) -> str | None:
    """The local character's name from an ``in 1`` line anywhere in the log.

    :func:`load_spawns` reads only the recent tail; the self spawn is a
    login event and can be older than that.  The DLL log is capped at
    16 MB, so a one-shot full read at attach is cheap.  The newest matching
    line wins.
    """
    if int(char_id) <= 0:
        return None
    source = Path(path) if path is not None else default_log_path()
    if source is None:
        return None
    try:
        data = source.read_bytes()
    except OSError:
        return None
    name: str | None = None
    for line in data.decode("latin1", "replace").splitlines():
        marker = line.find(_IN_MARKER)
        if marker < 0:
            continue
        record = line[marker + len(_IN_MARKER):].strip()
        match = _PLAYER.match(record)
        if match and int(match.group(2)) == int(char_id):
            name = match.group(1).strip()
    return name or None


def load_player_spawns(
    path: str | Path | None = None,
) -> dict[int, tuple[str, int, int]]:
    """Return ``charId -> (name, x, y)`` for the players in the log.

    Combines the `in 1 <name> - <charId> <x> <y> ...` spawn with later
    `mv 1 <charId> <x> <y> ...` moves (last one wins). A GUI that attaches
    after another player spawned still knows where that player is, so
    following by name works instead of reporting "not in sight" forever.
    """
    source = Path(path) if path is not None else default_log_path()
    if source is None:
        return {}
    try:
        log = _open_log_tail(source)
    except OSError:
        return {}

    players: dict[int, tuple[str, int, int]] = {}
    with log:
        for line in log:
            marker = line.find(_IN_MARKER)
            if marker >= 0:
                record = line[marker + len(_IN_MARKER):].strip()
                match = _PLAYER.match(record)
                if match is None:
                    continue
                try:
                    rest = record[len(match.group(0)):].split()
                    x, y = int(rest[0]), int(rest[1])
                except (IndexError, ValueError):
                    continue
                players[int(match.group(2))] = (match.group(1).strip(), x, y)
                continue
            change = _ints_after(line, _CMAP_MARKER, 3)
            if change and change[2] == 1:
                # Entering another map clears the client's entity list.
                players.clear()
                continue
            if _MAPOUT_MARKER in line:
                players.clear()
                continue
            despawn = _ints_after(line, _OUT_MARKER, 2)
            if despawn and despawn[0] == 1:
                players.pop(despawn[1], None)
                continue
            values = _ints_after(line, _MV_MARKER, 4)
            if values and values[0] == 1:
                char_id = values[1]
                name = players.get(char_id, ("", 0, 0))[0]
                players[char_id] = (name, values[2], values[3])
    return players


def load_entities(
    path: str | Path | None = None,
) -> dict[int, tuple[int, int, int, int]]:
    """Return ``entityId -> (kind, vnum, x, y)`` for the current map.

    Replays the log tail in order the way the client's entity list works:
    ``in`` spawns carry the vnum, ``mv``/``tp`` update the position (an entity
    seen only moving is kept with vnum 0), ``out`` removes it, and entering a
    new map (``c_map ... 1``/``mapout``) clears the list. A host that attaches
    mid-map can therefore start with every entity the log still knows instead
    of only those that happen to move after it connects.
    """
    source = Path(path) if path is not None else default_log_path()
    if source is None:
        return {}
    try:
        log = _open_log_tail(source)
    except OSError:
        return {}

    entities: dict[int, tuple[int, int, int, int]] = {}
    with log:
        for line in log:
            marker = line.find(_IN_MARKER)
            if marker >= 0:
                record = line[marker + len(_IN_MARKER):].strip()
                match = _ENTITY.match(record)
                if match:
                    kind = int(match.group(1))
                    vnum = int(match.group(2))
                    entity_id = int(match.group(3))
                    rest = record[len(match.group(0)):].split()
                    try:
                        x, y = int(rest[0]), int(rest[1])
                    except (IndexError, ValueError):
                        continue
                    entities[entity_id] = (kind, vnum, x, y)
                    continue
                match = _PLAYER.match(record)
                if match:
                    entity_id = int(match.group(2))
                    rest = record[len(match.group(0)):].split()
                    try:
                        x, y = int(rest[0]), int(rest[1])
                    except (IndexError, ValueError):
                        continue
                    entities[entity_id] = (1, 0, x, y)
                continue
            change = _ints_after(line, _CMAP_MARKER, 3)
            if change and change[2] == 1:
                entities.clear()
                continue
            if _MAPOUT_MARKER in line:
                entities.clear()
                continue
            despawn = _ints_after(line, _OUT_MARKER, 2)
            if despawn:
                entities.pop(despawn[1], None)
                continue
            move = _ints_after(line, _MV_MARKER, 4)
            if move:
                kind, entity_id, x, y = move
                old = entities.get(entity_id)
                entities[entity_id] = (kind, old[1] if old else 0, x, y)
                continue
            tele = _ints_after(line, _TP_MARKER, 4)
            if tele:
                kind, entity_id, x, y = tele
                old = entities.get(entity_id)
                entities[entity_id] = (kind, old[1] if old else 0, x, y)
    return entities


def _family_fields(token: str) -> int:
    head = token.split(".", 1)[0]
    try:
        return int(head)
    except ValueError:
        return -1


def load_player_metadata(
    path: str | Path | None = None,
) -> dict[int, tuple[int, str, bool, int, int, bool]]:
    """Return family and fairy fields for the logged players.

    ``charId -> (familyId, familyName, familyKnown,
                 fairyVnum, fairyElement, fairyKnown)``.
    `in 1` carries the family fields and the equipment sub-packet's fairy
    vnum; `gidx` and `pairy` update family and fairy element. A GUI that
    attaches after the players spawned never sees those `in` packets, so
    without this backfill everyone looked "family unknown"/"no fairy" and the
    auto-invite filters could not select the right players.
    """
    source = Path(path) if path is not None else default_log_path()
    if source is None:
        return {}
    try:
        log = _open_log_tail(source)
    except OSError:
        return {}

    metadata: dict[int, tuple[int, str, bool, int, int, bool]] = {}
    with log:
        for line in log:
            marker = line.find(_GIDX_MARKER)
            if marker >= 0:
                parts = line[marker + len(_GIDX_MARKER):].split()
                if len(parts) >= 4 and parts[0] == "1":
                    try:
                        char_id = int(parts[1])
                    except ValueError:
                        continue
                    old = metadata.get(
                        char_id, (-1, "", False, -1, -1, False)
                    )
                    metadata[char_id] = (
                        _family_fields(parts[2]),
                        "" if parts[3] in ("-", "") else parts[3],
                        True,
                        old[3],
                        old[4],
                        old[5],
                    )
                continue
            marker = line.find(_PAIRY_MARKER)
            if marker >= 0:
                parts = line[marker + len(_PAIRY_MARKER):].split()
                if len(parts) >= 4 and parts[0] == "1":
                    try:
                        char_id = int(parts[1])
                        element = int(parts[3])
                    except ValueError:
                        continue
                    old = metadata.get(
                        char_id, (-1, "", False, -1, -1, False)
                    )
                    metadata[char_id] = (
                        old[0],
                        old[1],
                        old[2],
                        old[3],
                        element,
                        True,
                    )
                continue
            marker = line.find(_IN_MARKER)
            if marker >= 0:
                record = line[marker + len(_IN_MARKER):].strip()
                match = _PLAYER.match(record)
                if match is None:
                    continue
                char_id = int(match.group(2))
                rest = record[len(match.group(0)):].split()
                old = metadata.get(
                    char_id, (-1, "", False, -1, -1, False)
                )
                family_id, family_name, family_known = old[:3]
                if len(rest) >= 23:
                    family_id = _family_fields(rest[21])
                    family_name = "" if rest[22] in ("-", "") else rest[22]
                    family_known = True
                fairy_vnum, fairy_element, fairy_known = old[3:]
                if len(rest) > 8:
                    equipment = rest[8].split(".")
                    if len(equipment) > 5:
                        try:
                            fairy_vnum = int(equipment[5])
                        except ValueError:
                            fairy_vnum = -1
                        fairy_known = True
                metadata[char_id] = (
                    family_id,
                    family_name,
                    family_known,
                    fairy_vnum,
                    fairy_element,
                    fairy_known,
                )
                continue
            change = _ints_after(line, _CMAP_MARKER, 3)
            if change and change[2] == 1:
                metadata.clear()
                continue
            if _MAPOUT_MARKER in line:
                metadata.clear()
                continue
            despawn = _ints_after(line, _OUT_MARKER, 2)
            if despawn and despawn[0] == 1:
                metadata.pop(despawn[1], None)
    return metadata


def load_player_families(
    path: str | Path | None = None,
) -> dict[int, tuple[int, str, bool]]:
    """Family-only projection of :func:`load_player_metadata`."""
    return {
        char_id: (fields[0], fields[1], fields[2])
        for char_id, fields in load_player_metadata(path).items()
    }


def load_skill_list(
    path: str | Path | None = None,
) -> list[int]:
    """Return the ordered, deduplicated skill list from `[RECV] ski` lines.

    The client's `u_s` packet sends a zero-based slot in this list, not the
    Skill.dat vnum (`u_s 10` → server `su 876` for the live specialist).
    """
    source = Path(path) if path is not None else default_log_path()
    if source is None:
        return []
    try:
        # `ski` is sent at login, i.e. at the start of the session log; the
        # tail of a multi-hour file would not contain it.
        lines = _log_head_lines(source)
    except OSError:
        return []

    pages: dict[int, list[int]] = {}
    for line in lines:
        marker = line.find(_SKI_MARKER)
        if marker < 0:
            continue
        values = []
        for token in line[marker + len(_SKI_MARKER) :].split():
            try:
                values.append(int(token))
            except ValueError:
                break
        if values:
            pages[values[0]] = [n for n in values[1:] if n > 0]

    ordered: list[int] = []
    for page in sorted(pages):
        for vnum in pages[page]:
            if vnum not in ordered:
                ordered.append(vnum)
    return ordered


def load_last_position(
    path: str | Path | None = None,
) -> tuple[int | None, int | None, int | None, int | None]:
    """Return ``(map_id, x, y, self_id)`` from the log's last position event.

    A GUI that attaches after login has no `at` yet, so the map auto-detect
    has to guess. The DLL log already holds the login/map-change `at` and the
    client's own `walk` packets; seeding the engine with them makes the map
    authoritative without waiting for another crossing.
    """
    source = Path(path) if path is not None else default_log_path()
    if source is None:
        return None, None, None, None
    try:
        log = _open_log_tail(source)
    except OSError:
        return None, None, None, None

    map_id: int | None = None
    x: int | None = None
    y: int | None = None
    self_id: int | None = None
    with log:
        for line in log:
            at = _ints_after(line, _AT_MARKER, 4)
            if at:
                self_id, map_id, x, y = at
                continue
            walk = _ints_after(line, _WALK_MARKER, 2)
            if walk and walk[0] >= 0 and walk[1] >= 0:
                x, y = walk
                continue
            move = _ints_after(line, _MV_MARKER, 4)
            if move and move[0] == 1:
                x, y = move[2], move[3]
                continue
            tele = _ints_after(line, _TP_MARKER, 4)
            if tele and tele[0] == 1:
                x, y = tele[2], tele[3]
                continue
            change = _ints_after(line, _CMAP_MARKER, 3)
            if change and change[2] == 1:
                map_id = change[1]

    return map_id, x, y, self_id


def load_gates(
    path: str | Path | None = None,
) -> dict[tuple[int, int, int], int]:
    """Return ``(mapId, x, y) -> destination`` from the log's `gp` lines.

    The server sends a map's whole portal list right after entering it::

        gp <x> <y> <toMapId> <unknown> <index> <unknown>

    The packet carries no map id, so the last `at` or entering `c_map` gives
    the current map. These are authoritative; crossing learning is only the
    fallback when `gp` was missed.
    """
    source = Path(path) if path is not None else default_log_path()
    gates: dict[tuple[int, int, int], int] = {}
    if source is None:
        return gates
    try:
        log = _open_log_tail(source)
    except OSError:
        return gates

    current_map: int | None = None
    with log:
        for line in log:
            change = _ints_after(line, _CMAP_MARKER, 3)
            if change:
                if change[2] == 1:
                    current_map = change[1]
                continue
            at = _ints_after(line, _AT_MARKER, 4)
            if at:
                current_map = at[1]
                continue
            gate = _ints_after(line, _GP_MARKER, 3)
            if gate and current_map is not None:
                x, y, destination = gate
                if x >= 0 and y >= 0 and destination > 0:
                    gates[(current_map, x, y)] = destination
    return gates


def load_timespaces(
    path: str | Path | None = None,
) -> dict[tuple[int, int, int], tuple[int, int, int, int]]:
    """Return ``(mapId, x, y) -> (tsId, flag, minLevel, maxLevel)`` from `wp`.

    The server sends a map's Time-Space entrances right after entering it::

        wp <x> <y> <tsId> <flag> <minLevel> <maxLevel>

    It carries no map id, so the last `at` or entering `c_map` gives it.
    Live map 4: ``wp 35 142 16 4 26 99`` … ``wp 229 33 704 0 90 99``.
    """
    source = Path(path) if path is not None else default_log_path()
    timespaces: dict[tuple[int, int, int], tuple[int, int, int, int]] = {}
    if source is None:
        return timespaces
    try:
        log = _open_log_tail(source)
    except OSError:
        return timespaces

    current_map: int | None = None
    with log:
        for line in log:
            change = _ints_after(line, _CMAP_MARKER, 3)
            if change:
                if change[2] == 1:
                    current_map = change[1]
                continue
            at = _ints_after(line, _AT_MARKER, 4)
            if at:
                current_map = at[1]
                continue
            entry = _ints_after(line, _WP_MARKER, 6)
            if entry and current_map is not None:
                x, y, ts_id, flag, min_level, max_level = entry
                if x >= 0 and y >= 0 and ts_id > 0:
                    timespaces[(current_map, x, y)] = (
                        ts_id,
                        flag,
                        min_level,
                        max_level,
                    )
    return timespaces


def load_portals(
    path: str | Path | None = None,
) -> dict[tuple[int, int, int], int]:
    """Return ``(mapId, x, y) -> destination`` learned from the log.

    A portal is a server-side warp tile, so the client has no table for it.
    The last own position before a leaving `c_map`/`mapout` is the portal
    cell; the following `at` names the destination map. Parsing the log lets
    a fresh GUI show the portals crossed earlier in the same session.
    """
    source = Path(path) if path is not None else default_log_path()
    portals: dict[tuple[int, int, int], int] = {}
    if source is None:
        return portals
    try:
        log = _open_log_tail(source)
    except OSError:
        return portals

    last: tuple[int, int] | None = None
    pending: tuple[int, int, int] | None = None  # from map, x, y
    with log:
        for line in log:
            walk = _ints_after(line, _WALK_MARKER, 2)
            if walk and walk[0] >= 0 and walk[1] >= 0:
                last = (walk[0], walk[1])
                continue
            move = _ints_after(line, _MV_MARKER, 4)
            if move and move[0] == 1:
                last = (move[2], move[3])
                continue
            tele = _ints_after(line, _TP_MARKER, 4)
            if tele and tele[0] == 1:
                last = (tele[2], tele[3])
                continue

            change = _ints_after(line, _CMAP_MARKER, 3)
            if change:
                _kind, changed_map, state = change
                if state == 0 and last is not None:
                    pending = (changed_map, last[0], last[1])
                elif (
                    state == 1
                    and pending is not None
                    and changed_map != pending[0]
                ):
                    portals[(pending[0], pending[1], pending[2])] = changed_map
                    pending = None
                continue
            if _MAPOUT_MARKER in line:
                continue

            at = _ints_after(line, _AT_MARKER, 4)
            if at:
                _char_id, at_map, at_x, at_y = at
                if pending is not None and at_map != pending[0]:
                    portals[(pending[0], pending[1], pending[2])] = at_map
                    portals[(at_map, at_x, at_y)] = pending[0]
                    pending = None
                last = (at_x, at_y)

    return portals


def load_skill_slots(
    path: str | Path | None = None,
) -> tuple[dict[int, int], int | None]:
    """Return ``(slot -> vnum, self_id)`` from the log's paired packets.

    ``ski`` is only sent at login, so a host that attaches afterwards has no
    skill list. The client's own ``[SEND] u_s <slot> <type> <id>`` line is
    answered by ``[RECV] su <type> <charId> <type> <id> <vnum> ...``: matching
    the local char id (from the first ``[RECV] at`` line) and the target turns
    a used skill into the slot mapping the bot needs. Slot 0 is the equipped
    SP card's basic attack, so this also follows a card swap.
    """
    source = Path(path) if path is not None else default_log_path()
    slots: dict[int, int] = {}
    self_id: int | None = None
    if source is None:
        return slots, self_id
    try:
        log = _open_log_tail(source)
    except OSError:
        return slots, self_id

    pending: tuple[int, int, int] | None = None  # slot, target type, target id
    with log:
        for line in log:
            if self_id is None:
                at = _ints_after(line, _AT_MARKER, 1)
                if at:
                    self_id = at[0]

            used = _ints_after(line, _US_MARKER, 3)
            if used:
                pending = (used[0], used[1], used[2])
                continue

            order = _ints_after(line, _SU_MARKER, 5)
            if not order or pending is None:
                continue
            attacker_type, attacker_id, target_type, target_id, vnum = order
            if (
                self_id is not None
                and attacker_type == 1
                and attacker_id == self_id
                and (target_type, target_id) == pending[1:]
            ):
                slots[pending[0]] = vnum
                pending = None

    return slots, self_id


def load_bazaar_open(
    path: str | Path | None = None,
) -> tuple[int | None, int | None, int | None]:
    """Return ``(npcId, option, page)`` from the client's last bazaar opening.

    The user opens the bazaar by hand once; the client's own
    ``npc_req 2 <npcId>`` + ``n_run <option> <page> 2 <npcId>`` followed by
    ``c_blist``/``c_slist`` is the exact sequence the bot replays later. The
    entity id is per map/session, so a GUI that attaches afterwards must learn
    it from this client's log instead of Nostale-NB's hardcoded 10188.
    """
    source = Path(path) if path is not None else default_log_path()
    npc_id: int | None = None
    option: int | None = None
    page: int | None = None
    if source is None:
        return npc_id, option, page
    try:
        log = _open_log_tail(source)
    except OSError:
        return npc_id, option, page

    request: int | None = None
    run: tuple[int, int] | None = None
    with log:
        for line in log:
            values = _ints_after(line, _NPCREQ_MARKER, 2)
            if values:
                if values[0] == 2:
                    request = values[1]
                    run = None
                continue
            values = _ints_after(line, _NRUN_MARKER, 4)
            if values:
                if request is not None and values[3] == request:
                    run = (values[0], values[1])
                continue
            opened = False
            if _CBLIST_MARKER in line:
                start = line.find(_CBLIST_MARKER) + len(_CBLIST_MARKER)
                tokens = line[start:].split()
                # The no-filter initial listing has count 0 at index 8
                # (eight filter slots, then the vnum count).  A search has a
                # positive count and must not be mistaken for an opening.
                opened = len(tokens) >= 9 and tokens[8] == "0"
            elif _CSLIST_MARKER in line:
                opened = True
            if opened and request is not None and run is not None:
                npc_id, (option, page) = request, run
    return npc_id, option, page


def _log_stamp(line: str) -> float | None:
    """Seconds within the day from the DLL log's ``HH:MM:SS.mmm`` prefix."""
    match = _LINE_STAMP.match(line)
    if match is None:
        return None
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return hours * 3600.0 + minutes * 60.0 + seconds + millis / 1000.0


def load_bazaar_skill(path: str | Path | None = None) -> bool:
    """True when the client opened the NosBazar with its own shortcut.

    On FrosTale the bazaar is neither a Bibi dialog nor the medal item:
    pressing the client's NosBazar shortcut sends a bare ``c_skill`` and the
    server answers ``wopen 32`` within ~0.2 s (11/11 openings in the
    2026-11-23 live log, on four maps). A GUI that attaches afterwards has
    to learn the trigger from this client's log, exactly like the Bibi
    id/option, so the controller can replay it with no NPC and no medal.
    """
    source = Path(path) if path is not None else default_log_path()
    if source is None:
        return False
    try:
        log = _open_log_tail(source)
    except OSError:
        return False
    skill_at: float | None = None
    with log:
        for line in log:
            stamp = _log_stamp(line)
            if stamp is None:
                continue
            if _CSKILL_MARKER in line:
                skill_at = stamp
                continue
            if _WOPEN32_MARKER in line and skill_at is not None:
                if 0.0 <= stamp - skill_at <= BAZAR_SKILL_WINDOW_S:
                    return True
    return False


def log_inventory_bags(path: str | Path | None = None) -> set[int]:
    """Bags that had a full ``inv <bag>`` line in the log tail.

    ``load_inventory`` also replays ``ivn`` updates; a bag that only appears
    in those updates has no complete contents and must not replace a cached
    snapshot.
    """
    source = Path(path) if path is not None else default_log_path()
    if source is None:
        return set()
    try:
        log = _open_log_tail(source)
    except OSError:
        return set()
    bags: set[int] = set()
    with log:
        for line in log:
            if _INV_MARKER not in line:
                continue
            start = line.find(_INV_MARKER) + len(_INV_MARKER)
            tokens = line[start:].split()
            if not tokens:
                continue
            try:
                bags.add(int(tokens[0]))
            except ValueError:
                continue
    return bags


def load_inventory(
    path: str | Path | None = None,
) -> tuple[dict[int, dict[int, InventorySlot]], int | None]:
    """Replay the log's ``inv``/``ivn``/``gold`` lines.

    A host that attaches after login misses the login inventory dump.  The
    DLL's log still has it, so a relist can find item slots without another
    relog.  Returns ``({bag: {slot: InventorySlot}}, gold)``; a bag that was
    never sent is absent rather than empty.  Tokens go through the engine's
    own parser, so equipment bags keep their extra fields, quantity 0 and
    negative (limited/bound) vnums exactly as the live handlers do.
    """
    source = Path(path) if path is not None else default_log_path()
    bags: dict[int, dict[int, InventorySlot]] = {}
    gold: int | None = None
    if source is None:
        return bags, gold
    try:
        log = _open_log_tail(source)
    except OSError:
        return bags, gold

    with log:
        for line in log:
            if _INV_MARKER in line:
                start = line.find(_INV_MARKER) + len(_INV_MARKER)
                tokens = line[start:].split()
                if not tokens:
                    continue
                try:
                    bag = int(tokens[0])
                except ValueError:
                    continue
                slots: dict[int, InventorySlot] = {}
                for token in tokens[1:]:
                    parsed = parse_inventory_token(bag, token)
                    if parsed is None:
                        continue
                    index, slot = parsed
                    if slot is not None:
                        slots[index] = slot
                bags[bag] = slots
                continue
            if _IVN_MARKER in line:
                start = line.find(_IVN_MARKER) + len(_IVN_MARKER)
                tokens = line[start:].split()
                if len(tokens) < 2:
                    continue
                try:
                    bag = int(tokens[0])
                except ValueError:
                    continue
                parsed = parse_inventory_token(bag, tokens[1])
                if parsed is None:
                    continue
                index, slot = parsed
                slots = bags.setdefault(bag, {})
                if slot is None:
                    slots.pop(index, None)
                else:
                    slots[index] = slot
                continue
            values = _ints_after(line, _GOLD_MARKER, 1)
            if values and values[0] >= 0:
                gold = values[0]
    return bags, gold


#: The cached inventory is a snapshot of the last complete session, kept so a
#: later GUI attach survives the DLL's 16 MB log truncation.
INVENTORY_CACHE_VERSION = 1


def default_inventory_cache_dir() -> Path:
    """Directory for per-character inventory snapshots.

    ``NTPL_INVENTORY_DIR`` overrides it; otherwise XDG's config dir is used,
    matching the bazar/bot configuration files.
    """
    override = os.environ.get("NTPL_INVENTORY_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ntpl"


def inventory_cache_path(char_id: int) -> Path:
    """The snapshot file for one character id."""
    return default_inventory_cache_dir() / f"inventory-{int(char_id)}.json"


def _slot_payload(slot: InventorySlot) -> dict:
    return {
        "vnum": int(slot.vnum),
        "quantity": int(slot.quantity),
        "fields": list(slot.fields),
    }


def save_inventory_cache(
    bags: dict[int, dict[int, InventorySlot]],
    char_id: int,
    path: str | Path | None = None,
) -> Path:
    """Atomically save the inventory snapshot for ``char_id``.

    Written after a full ``inv`` dump and on shutdown; the next attach can
    then show the inventory even when the client's DLL log was truncated
    before the host connected.
    """
    target = Path(path) if path is not None else inventory_cache_path(char_id)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"cannot create {target.parent}: {exc}") from exc
    payload = {
        "version": INVENTORY_CACHE_VERSION,
        "char_id": int(char_id),
        "saved_at": time.time(),
        "bags": {
            str(bag): {
                str(index): _slot_payload(slot)
                for index, slot in sorted(slots.items())
            }
            for bag, slots in sorted(bags.items())
            if slots
        },
    }
    text = json.dumps(payload, separators=(",", ":")) + "\n"
    try:
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=f".{target.name}.",
            delete=False,
        )
    except OSError as exc:
        raise ValueError(f"cannot write {target}: {exc}") from exc
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ValueError(f"cannot write {target}: {exc}") from exc
    return target


def load_inventory_cache(
    path: str | Path | None = None,
    char_id: int | None = None,
) -> tuple[dict[int, dict[int, InventorySlot]], float] | None:
    """Read a snapshot written by :func:`save_inventory_cache`.

    Returns ``({bag: {slot: InventorySlot}}, saved_at)`` or ``None`` when the
    file is missing/malformed or belongs to a different character. Slots with
    vnum 0 or a negative quantity are dropped and field values are
    stringified; the rest of the snapshot is still usable.
    """
    if path is not None:
        target = Path(path)
    elif char_id is not None:
        target = inventory_cache_path(char_id)
    else:
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("version") != INVENTORY_CACHE_VERSION:
        return None
    if char_id is not None and data.get("char_id") != int(char_id):
        return None
    raw_bags = data.get("bags")
    if not isinstance(raw_bags, dict):
        return None
    bags: dict[int, dict[int, InventorySlot]] = {}
    for raw_bag, raw_slots in raw_bags.items():
        try:
            bag = int(raw_bag)
        except (TypeError, ValueError):
            continue
        if bag < 0 or not isinstance(raw_slots, dict):
            continue
        slots: dict[int, InventorySlot] = {}
        for raw_index, raw_slot in raw_slots.items():
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if index < 0 or not isinstance(raw_slot, dict):
                continue
            try:
                vnum = int(raw_slot.get("vnum", 0))
                quantity = int(raw_slot.get("quantity", 0))
            except (TypeError, ValueError):
                continue
            if vnum == 0 or quantity < 0:
                continue
            raw_fields = raw_slot.get("fields", [])
            fields = (
                tuple(str(value) for value in raw_fields)
                if isinstance(raw_fields, list)
                else ()
            )
            slots[index] = InventorySlot(
                bag, index, vnum, quantity, fields
            )
        if slots:
            bags[bag] = slots
    try:
        saved_at = float(data.get("saved_at", 0.0))
    except (TypeError, ValueError):
        saved_at = 0.0
    return bags, saved_at
