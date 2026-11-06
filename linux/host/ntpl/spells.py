"""NosTale spell catalog from ``NSgtdData.NOS`` → ``Skill.dat``.

The game data archive uses the same count/name/size container as the language
archive. ``Skill.dat`` is nibble-coded text; each record is a block separated
by ``#====...`` and its fields are tab-separated::

    VNUM    876
    NAME    zts562e
    TYPE    1   10  38  2   0   3      # skill type, cast id, class, attack...
    COST    0   0   0
    LEVEL   0   0   0   0   0
    EFFECT  ...
    TARGET  0   1   11  4   0          # target type, hit type, range, ...
    DATA    0   7   0   0   7   900    # ..., cast time, cooldown, ..., MP
    ...
    Z_DESC  1   zts563e

``NAME`` is a key into ``_code_<lang>_Skill.txt`` (the same ``zts<id>e``
records as the monster table), which is where the readable name/description
comes from. ``DATA[4]`` is cast time and ``DATA[5]`` cooldown in deciseconds;
``DATA[8]`` is the MP cost; ``TARGET[2]`` (or ``DATA[11]``) is the range.

Only records with ``TYPE[0] == 1`` are active, castable skills. The catalog
also keeps passive/system records because they share the id space, but the
spell picker filters to active ones.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .mapdata import DEFAULT_NSTC
from .monsternames import (
    load_skill_descriptions,
    read_archive_entries,
)

DEFAULT_NSGTD = DEFAULT_NSTC.parent / "NSgtdData.NOS"

_KEYS = {
    "VNUM", "NAME", "TYPE", "COST", "LEVEL", "EFFECT", "TARGET",
    "DATA", "BASIC", "FCOMBO", "CELL", "Z_DESC",
}
_NAME_KEY = re.compile(r"zts(\d+)e")


@dataclass(frozen=True)
class SpellInfo:
    vnum: int
    name: str
    key: int
    skill_type: int
    class_id: int
    target_type: int
    range: int
    cast_time: float
    cooldown: float
    mp_cost: int

    @property
    def target_mode(self) -> str:
        if self.target_type == 1:
            return "self"
        if self.target_type == 2:
            return "ally"
        return "monster"

    @property
    def active(self) -> bool:
        return self.skill_type == 1


def default_nsgtd_path() -> Path:
    override = os.environ.get("NTPL_NSGTD")
    if override:
        return Path(override)
    return DEFAULT_NSGTD


def _int(values: list[str], index: int, default: int = 0) -> int:
    try:
        return int(values[index])
    except (IndexError, ValueError):
        return default


def _fields(block: str) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    current: str | None = None
    for token in block.split("\t"):
        token = token.strip()
        if token in _KEYS:
            current = token
            groups[current] = []
        elif current is not None:
            groups[current].append(token)
    return groups


def _first_line(text: str) -> str:
    for line in re.split(r"[\r\n]+", text):
        line = line.strip()
        if line:
            return line
    return ""


def parse_skill_text(
    text: str,
    descriptions: dict[int, str] | None = None,
) -> dict[int, SpellInfo]:
    """Parse decrypted ``Skill.dat`` into ``{vnum: SpellInfo}``."""
    descriptions = descriptions or {}
    spells: dict[int, SpellInfo] = {}
    for block in text.split("#========================================================="):
        block = block.strip("\r\n\t ")
        if not block or not block.startswith("VNUM"):
            continue
        fields = _fields(block)
        vnum_values = fields.get("VNUM", [])
        try:
            vnum = int(vnum_values[0])
        except (IndexError, ValueError):
            continue

        name_key = fields.get("NAME", [""])[0]
        match = _NAME_KEY.match(name_key)
        key = int(match.group(1)) if match else 0
        name = _first_line(descriptions.get(key, "")) or name_key

        type_values = fields.get("TYPE", [])
        target_values = fields.get("TARGET", [])
        data_values = fields.get("DATA", [])

        target_type = _int(target_values, 0)
        spell_range = _int(target_values, 2) or _int(data_values, 11)
        spells[vnum] = SpellInfo(
            vnum=vnum,
            name=name,
            key=key,
            skill_type=_int(type_values, 0, -1),
            class_id=_int(type_values, 2),
            target_type=target_type,
            range=spell_range,
            cast_time=_int(data_values, 4) / 10.0,
            cooldown=_int(data_values, 5) / 10.0,
            mp_cost=_int(data_values, 8),
        )
    return spells


def load_spells(path: str | Path | None = None) -> dict[int, SpellInfo]:
    """Load the spell catalog from ``NSgtdData.NOS``.

    Missing game data returns an empty catalog; callers fall back to raw
    vnums. Only ``Skill.dat`` is decoded.
    """
    source = Path(path) if path is not None else default_nsgtd_path()
    try:
        data = source.read_bytes()
    except OSError:
        return {}
    try:
        entries = read_archive_entries(data)
    except ValueError:
        return {}

    blob = None
    for name, content in entries.items():
        if name.lower() == "skill.dat":
            blob = content
            break
    if blob is None:
        return {}

    text = blob.decode("cp1252", "replace")
    return parse_skill_text(text, load_skill_descriptions())
