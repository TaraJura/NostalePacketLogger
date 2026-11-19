"""Cross-client mirror bus for Follow mode.

Every host that attaches to a client publishes its **own** outgoing movement
(``walk``) and skill (``u_s``) packets as small JSON lines into a per-process
file under ``~/.cache/ntpl/mirror/``.  A second client that follows that
character reads the file and replays the same movement and spells, replacing
the followed character's id with its own where the packet carries it.  This is
what makes **Mirror movement & spells** exact even when the two bots run in
separate GUI processes: the server's broadcast ``mv``/``su`` packets are only
the fallback for a player whose host does not publish.

The bus is intentionally plain files:

* ``O_APPEND``-style whole-line writes are atomic for these short lines, and
  one file per publisher means two hosts never contend for the same file.
* A reader can attach and detach at any time; offsets are kept per file and a
  truncated/rotated file restarts from zero.
* Events older than :data:`EVENT_TTL_S` are ignored, so a stale file from an
  earlier session cannot move the character.

``NTPL_MIRROR_DIR`` overrides the directory (tests use a temp dir).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

#: Where the per-host mirror files live.  ``NTPL_MIRROR_DIR`` overrides it.
MIRROR_DIR_ENV = "NTPL_MIRROR_DIR"
DEFAULT_MIRROR_DIR = Path.home() / ".cache" / "ntpl" / "mirror"

#: A mirrored action is only useful while it is fresh; a late replay while the
#: followed player is already elsewhere would walk the bot backwards.
EVENT_TTL_S = 5.0
#: Drop a publisher's history past this size; the oldest part is irrelevant
#: because the reader only cares about the last few seconds.
MAX_FILE_BYTES = 2 * 1024 * 1024
#: Remove publisher files that have not changed for this long.
STALE_FILE_S = 3600.0

KIND_MOVE = "move"
KIND_SPELL = "spell"


def mirror_dir() -> Path:
    """The mirror bus directory (not created here)."""
    override = os.environ.get(MIRROR_DIR_ENV)
    return Path(override) if override else DEFAULT_MIRROR_DIR


@dataclass(frozen=True)
class MirrorEvent:
    """One action published by another client's host."""

    kind: str
    at: float
    char_id: int = 0
    name: str = ""
    map_id: int | None = None
    # move
    x: int = 0
    y: int = 0
    speed: int = 0
    # spell
    skill: int = 0
    slot: int = -1
    target_type: int = 0
    target_id: int = 0

    def matches(
        self,
        name: str | None = None,
        char_id: int | None = None,
    ) -> bool:
        """True when this event belongs to the followed character.

        A name is enough when the followed player is another of our clients;
        the char id covers a publisher that has not learned its own name yet
        (the name may also be spelled differently in another locale).
        """
        if char_id is not None and self.char_id == char_id:
            return True
        if name and self.name and self.name.casefold() == name.casefold():
            return True
        return False


def _prune_directory(directory: Path) -> None:
    """Remove publisher files no live writer has touched for a long time."""
    cutoff = time.time() - STALE_FILE_S
    try:
        paths = list(directory.glob("*.jsonl"))
    except OSError:
        return
    for path in paths:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


class MirrorWriter:
    """Append this host's own walk/spell actions to its mirror file.

    One writer per connected client.  Failures are swallowed: mirroring is an
    optional accessory and must never disturb packet logging or the bot.
    """

    def __init__(self, path: Path | None = None, directory: Path | None = None) -> None:
        if path is None:
            directory = directory or mirror_dir()
            path = (
                directory
                / f"client-{os.getpid()}-{int(time.time() * 1000) % 100000}.jsonl"
            )
        self.path = Path(path)
        self._file = None
        self._bytes = 0
        self._open()

    def _open(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _prune_directory(self.path.parent)
            # Replace the path atomically with a fresh file, so a reader can
            # never see a half-truncated line and a restart always changes
            # the inode (readers detect rotation by it).
            temp = self.path.with_name(
                f".{self.path.name}.{os.getpid()}.tmp"
            )
            try:
                handle = temp.open("w", encoding="utf-8", buffering=1)
                os.replace(temp, self.path)
            except OSError:
                handle = self.path.open("w", encoding="utf-8", buffering=1)
            self._file = handle
            self._bytes = 0
        except OSError:
            self._file = None

    @property
    def available(self) -> bool:
        return self._file is not None

    def _alive(self) -> bool:
        """True while the path still points at this writer's file.

        Another writer prunes idle files after an hour; an idle bot keeps its
        handle open, so a later action must recreate the path instead of
        appending to an unlinked inode.
        """
        if self._file is None:
            return False
        try:
            return (
                os.stat(self.path).st_ino
                == os.fstat(self._file.fileno()).st_ino
            )
        except OSError:
            return False

    def _write(self, payload: dict) -> None:
        if self._file is not None and not self._alive():
            self.close()
            self._open()
        if self._file is None:
            return
        payload.setdefault("v", 1)
        payload.setdefault("at", time.time())
        try:
            line = json.dumps(payload, separators=(",", ":")) + "\n"
            self._file.write(line)
            self._bytes += len(line)
            if self._bytes > MAX_FILE_BYTES:
                # Nothing older than a few seconds matters to a reader.
                self._file.close()
                self._open()
        except (OSError, ValueError):
            self.close()

    def move(
        self,
        x: int,
        y: int,
        speed: int = 0,
        char_id: int = 0,
        name: str = "",
        map_id: int | None = None,
    ) -> None:
        self._write(
            {
                "kind": KIND_MOVE,
                "char": int(char_id or 0),
                "name": name or "",
                "map": map_id,
                "x": int(x),
                "y": int(y),
                "speed": int(speed),
            }
        )

    def spell(
        self,
        skill: int = 0,
        slot: int = -1,
        target_type: int = 0,
        target_id: int = 0,
        char_id: int = 0,
        name: str = "",
        map_id: int | None = None,
    ) -> None:
        self._write(
            {
                "kind": KIND_SPELL,
                "char": int(char_id or 0),
                "name": name or "",
                "map": map_id,
                "skill": int(skill),
                "slot": int(slot),
                "tt": int(target_type),
                "tid": int(target_id),
            }
        )

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None


class MirrorReader:
    """Tail every publisher file and return fresh events.

    Offsets are remembered per path; a file that shrank (rotation) is read
    again from the start.  Incomplete trailing lines are carried over, so a
    read that races a publisher's write cannot lose an event.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory) if directory is not None else mirror_dir()
        self._offsets: dict[Path, int] = {}
        self._partial: dict[Path, bytes] = {}
        self._mtimes: dict[Path, int] = {}
        self._tails: dict[Path, bytes] = {}
        self._inodes: dict[Path, int] = {}
        self._pruned_at = 0.0

    def poll(self, now: float | None = None) -> list[MirrorEvent]:
        wall = time.time() if now is None else now
        try:
            paths = sorted(self.directory.glob("*.jsonl"))
        except OSError:
            return []
        events: list[MirrorEvent] = []
        for path in paths:
            events.extend(self._poll_file(path, wall))
        if wall - self._pruned_at >= 600.0:
            self._pruned_at = wall
            self._prune(wall)
        return events

    def _poll_file(self, path: Path, wall: float) -> list[MirrorEvent]:
        try:
            stat = path.stat()
        except OSError:
            return []
        size = stat.st_size
        mtime = stat.st_mtime_ns
        inode = stat.st_ino
        offset = self._offsets.get(path, 0)
        if self._inodes.get(path) not in (None, inode):
            # The publisher replaced its file (atomic rotation): start over.
            offset = 0
            self._partial.pop(path, None)
        self._inodes[path] = inode
        if size < offset:
            # The publisher rotated/restarted: read the new file from the top.
            offset = 0
            self._partial.pop(path, None)
        elif size == offset and offset > 0:
            if self._mtimes.get(path) == mtime:
                return []
            # The file did not grow but changed: a new publisher truncated
            # it and wrote the same number of bytes instantly. The tail
            # carries the timestamp, so it is a reliable identity check.
            if self._read_tail(path, size) == self._tails.get(path):
                self._mtimes[path] = mtime
                return []
            offset = 0
            self._partial.pop(path, None)
        if size == offset:
            self._mtimes[path] = mtime
            return []
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read()
        except OSError:
            return []
        self._offsets[path] = offset + len(chunk)
        self._mtimes[path] = mtime
        self._tails[path] = self._read_tail(path, size)
        data = self._partial.pop(path, b"") + chunk
        lines = data.split(b"\n")
        self._partial[path] = lines.pop()
        events: list[MirrorEvent] = []
        for raw in lines:
            event = _parse_event(raw)
            if event is None:
                continue
            if event.at < wall - EVENT_TTL_S:
                continue
            if event.at > wall + 5.0:
                # A wildly future timestamp is a clock jump, not an action.
                continue
            events.append(event)
        return events

    @staticmethod
    def _read_tail(path: Path, size: int) -> bytes:
        try:
            with path.open("rb") as handle:
                handle.seek(max(0, size - 96))
                return handle.read(96)
        except OSError:
            return b""

    def _prune(self, wall: float) -> None:
        cutoff = wall - STALE_FILE_S
        try:
            paths = list(self.directory.glob("*.jsonl"))
        except OSError:
            return
        for path in paths:
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    self._offsets.pop(path, None)
                    self._partial.pop(path, None)
                    self._mtimes.pop(path, None)
                    self._tails.pop(path, None)
                    self._inodes.pop(path, None)
            except OSError:
                pass

    def close(self) -> None:
        self._offsets.clear()
        self._partial.clear()


def _parse_event(raw: bytes) -> MirrorEvent | None:
    if not raw.strip():
        return None
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get("v") not in (1, None):
        return None
    kind = data.get("kind")
    if kind not in (KIND_MOVE, KIND_SPELL):
        return None
    try:
        at = float(data.get("at", 0.0))
        char_id = int(data.get("char", 0) or 0)
        map_raw = data.get("map")
        map_id = int(map_raw) if map_raw is not None else None
        name = str(data.get("name", "") or "")
        if kind == KIND_MOVE:
            return MirrorEvent(
                kind=kind,
                at=at,
                char_id=char_id,
                name=name,
                map_id=map_id,
                x=int(data.get("x", 0)),
                y=int(data.get("y", 0)),
                speed=int(data.get("speed", 0)),
            )
        return MirrorEvent(
            kind=kind,
            at=at,
            char_id=char_id,
            name=name,
            map_id=map_id,
            skill=int(data.get("skill", 0) or 0),
            slot=int(data.get("slot", -1)),
            target_type=int(data.get("tt", 0) or 0),
            target_id=int(data.get("tid", 0) or 0),
        )
    except (TypeError, ValueError):
        return None
