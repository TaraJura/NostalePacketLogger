"""Recover entity ids from the DLL packet log.

A host that attaches after login only sees `mv`/`tp` for monsters and players
that spawned earlier, so their `Entity.vnum` and player name stay empty. The
DLL's packet log was opened when the DLL was injected and contains the whole
current session, including those `in` spawn packets. Reading only the
`[RECV] in ...` lines gives the missing mapping:

    in 3 <vnum> <entityId> <x> <y> ...     monster/NPC
    in 1 <name> - <charId> <x> <y> ...     player

Entity ids are per-session. The log is truncated on every DLL injection, so
the mapping belongs to the live client session. Later entries override
earlier ones, which handles a reused entity id.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_IN_MARKER = "[RECV] in "
_SKI_MARKER = "[RECV] ski "
_AT_MARKER = "[RECV] at "
_US_MARKER = "[SEND] u_s "
_SU_MARKER = "[RECV] su "
_ENTITY = re.compile(r"^([23]) (\d+) (\d+) ")
_PLAYER = re.compile(r"^1 (.+) - (\d+) ")


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


def default_log_path() -> Path | None:
    """Locate ``C:\\NosTalePacketLog.txt`` inside a Wine prefix.

    ``NTPL_LOG`` wins. Otherwise ``WINEPREFIX`` is used, then the documented
    dedicated prefix and the Gameforge prefix on this machine. Returns None
    when no log exists.
    """
    override = os.environ.get("NTPL_LOG")
    if override:
        return Path(override)

    candidates: list[Path] = []
    prefix = os.environ.get("WINEPREFIX")
    if prefix:
        candidates.append(Path(prefix) / "drive_c" / "NosTalePacketLog.txt")
    home = Path.home()
    candidates.extend(
        [
            home / ".wine-nostale/drive_c/NosTalePacketLog.txt",
            home / "games/gameforge/prefix/drive_c/NosTalePacketLog.txt",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
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
        log = open(source, "r", encoding="latin1", errors="replace")
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
        log = open(source, "r", encoding="latin1", errors="replace")
    except OSError:
        return []

    pages: dict[int, list[int]] = {}
    with log:
        for line in log:
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
        log = open(source, "r", encoding="latin1", errors="replace")
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
