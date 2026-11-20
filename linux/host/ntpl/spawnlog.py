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
import os
import re
from pathlib import Path

_IN_MARKER = "[RECV] in "
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


def load_inventory(
    path: str | Path | None = None,
) -> tuple[dict[int, dict[int, tuple[int, int]]], int | None]:
    """Replay the log's ``inv``/``ivn``/``gold`` lines.

    A host that attaches after login misses the login inventory dump.  The
    DLL's log still has it, so a relist can find item slots without another
    relog.  Returns ``({bag: {slot: (vnum, quantity)}}, gold)``; a bag that
    was never sent is absent rather than empty.
    """
    source = Path(path) if path is not None else default_log_path()
    bags: dict[int, dict[int, tuple[int, int]]] = {}
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
                slots: dict[int, tuple[int, int]] = {}
                for token in tokens[1:]:
                    fields = token.split(".")
                    if len(fields) < 3:
                        continue
                    try:
                        index, vnum, quantity = (
                            int(fields[0]),
                            int(fields[1]),
                            int(fields[2]),
                        )
                    except ValueError:
                        continue
                    if index >= 0 and vnum > 0 and quantity > 0:
                        slots[index] = (vnum, quantity)
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
                fields = tokens[1].split(".")
                if len(fields) < 3:
                    continue
                try:
                    index, vnum, quantity = (
                        int(fields[0]),
                        int(fields[1]),
                        int(fields[2]),
                    )
                except ValueError:
                    continue
                slots = bags.setdefault(bag, {})
                if index < 0:
                    continue
                if vnum > 0 and quantity > 0:
                    slots[index] = (vnum, quantity)
                else:
                    slots.pop(index, None)
                continue
            values = _ints_after(line, _GOLD_MARKER, 1)
            if values and values[0] >= 0:
                gold = values[0]
    return bags, gold
