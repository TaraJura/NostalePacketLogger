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
_ENTITY = re.compile(r"^([23]) (\d+) (\d+) ")
_PLAYER = re.compile(r"^1 (.+) - (\d+) ")


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
