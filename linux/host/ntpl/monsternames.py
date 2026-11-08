"""NosTale monster names from the client's game data.

The displayed name needs two tables, and the numbers in them are **not** the
monster vnum:

1. ``NSgtdData.NOS`` → ``monster.dat`` maps the template vnum to a text key::

       VNUM 419
       NAME zts489e

2. ``NSlangData_<lang>.NOS`` → ``_code_<lang>_monster.txt`` maps that key to
   the localized name::

       zts489e<TAB>Violent Seedle

   The text-table key is its own id space. Vnum 419's key is ``zts489e``, so
   looking up ``zts419e`` returns the unrelated "Wheat"; 419 is a Violent
   Seedle on the live map 103. ``monster.dat`` is the authoritative bridge.

The NOS archives have no magic string and their layout is:

    count       uint32
    entry       count times:
                  number       uint32
                  name_size    uint32
                  name         name_size bytes
                  is_dat       uint32
                  data_size    uint32
                  data         data_size bytes (encrypted)

``.txt`` entries are encoded with the client's nibble codec (the "text dat"
decryptor, also used by OnexExplorer): a control byte carries a count, the
low half-byte selects one of a 16-character alphabet, the high half a second
character, and a plain run is XOR 0x33. 0xFF emits a carriage return.

This module is read-only and never touches the game process. Missing data is
not fatal: :func:`load_monster_names` returns an empty mapping/Dict.
"""

from __future__ import annotations

import os
import re
import struct
from pathlib import Path

from .mapdata import DEFAULT_NSTC

#: The codec alphabet from OnexExplorer's NosTextDatFileDecryptor.
_CRYPTO = (
    0x00, 0x20, 0x2D, 0x2E,
    0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39,
    0x0A, 0x00,
)

class MonsterNameError(ValueError):
    pass


def _data_dir() -> Path:
    nstc = os.environ.get("NTPL_NSTC")
    if nstc:
        return Path(nstc).parent
    return DEFAULT_NSTC.parent


def default_lang_path() -> Path:
    """Locate the language archive, preferring the English (UK) one.

    ``NTPL_NSLANG`` selects an explicit file. Otherwise the ``NostaleData``
    directory next to ``NStcData.NOS`` is searched for ``NSlangData_*.NOS``;
    the English file wins when it is present.
    """
    override = os.environ.get("NTPL_NSLANG")
    if override:
        return Path(override)

    directory = _data_dir()
    preferred = directory / "NSlangData_UK.NOS"
    if preferred.is_file():
        return preferred
    for candidate in sorted(directory.glob("NSlangData_*.NOS")):
        if candidate.is_file():
            return candidate
    return preferred


def default_monster_data_path() -> Path:
    """Locate ``NSgtdData.NOS`` (the archive holding ``monster.dat``).

    ``NTPL_NSGTD`` selects an explicit file, matching ``spells.py``. Otherwise
    the ``NostaleData`` directory next to ``NStcData.NOS`` is used.
    """
    override = os.environ.get("NTPL_NSGTD")
    if override:
        return Path(override)
    return _data_dir() / "NSgtdData.NOS"


def decrypt_text_dat(data: bytes) -> bytes:
    """Decode a ``.dat``/``.txt`` entry from the language archive."""
    out = bytearray()
    index = 0
    size = len(data)
    while index < size:
        current = data[index]
        index += 1
        if current == 0xFF:
            out.append(0x0D)
            continue

        count = current & 0x7F
        if current & 0x80:
            while count > 0:
                if index >= size:
                    break
                current = data[index]
                index += 1
                out.append(_CRYPTO[(current & 0xF0) >> 4])
                if count <= 1:
                    break
                second = _CRYPTO[current & 0x0F]
                if not second:
                    break
                out.append(second)
                count -= 2
        else:
            while count > 0:
                if index >= size:
                    break
                current = data[index]
                index += 1
                out.append(current ^ 0x33)
                count -= 1
    return bytes(out)


def parse_text_table(text: str) -> dict[str, str]:
    """Parse a decrypted ``zts...`` table into ``{key: text}``.

    The key is the code other data files store (``monster.dat`` NAME,
    ``Skill.dat`` NAME), not the template/skill vnum.
    """
    values: dict[str, str] = {}
    for record in re.split(r"[\r\n]+", text):
        key, separator, value = record.partition("\t")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if key.startswith("zts") and key.endswith("e") and value:
            values[key] = value
    return values


def parse_monster_text(text: str) -> dict[int, str]:
    """Parse a decrypted ``_code_<lang>_<table>.txt`` text table.

    Both the monster and skill tables use the same ``zts<id>e<TAB><text>``
    record shape. The number in the key is a **text id**, not the monster
    vnum; use :func:`parse_monster_dat` for the vnum → key bridge.
    """
    names: dict[int, str] = {}
    for key, name in parse_text_table(text).items():
        match = re.fullmatch(r"zts(\d+)e", key)
        if match:
            names[int(match.group(1))] = name
    return names


def parse_monster_dat(text: str) -> dict[int, str]:
    """Parse decrypted ``monster.dat`` into ``{vnum: text key}``.

    ``monster.dat`` (inside ``NSgtdData.NOS``) is the authoritative bridge
    between the server's monster vnum and the language text id. Records are
    tab-separated field lines::

        #========================================================
            VNUM    419
            NAME    zts489e
            LEVEL   45
            ...

    A record without a parsable VNUM or NAME is skipped.
    """
    keys: dict[int, str] = {}
    vnum: int | None = None
    for line in re.split(r"[\r\n]+", text):
        fields = line.split()
        if not fields or fields[0].startswith("#"):
            continue
        if fields[0] == "VNUM":
            try:
                vnum = int(fields[1])
            except (IndexError, ValueError):
                vnum = None
        elif fields[0] == "NAME":
            if vnum is not None and len(fields) >= 2:
                keys[vnum] = fields[1]
            # NAME closes the record; later NAME-like fields (none today)
            # must not reuse this VNUM.
            vnum = None
    return keys


def _read_archive_entries(
    data: bytes, wanted: set[str] | None = None
) -> dict[str, bytes]:
    """Return ``{name: decrypted content}`` for a language archive.

    ``wanted`` limits decryption to those lower-case entry names; the others
    are skipped without decoding (``NSgtdData.NOS`` carries several megabytes
    of ``Item.dat`` and quest text that no caller needs).
    """
    if len(data) < 4:
        raise MonsterNameError("language archive is truncated")
    count = struct.unpack_from("<I", data, 0)[0]
    position = 4
    entries: dict[str, bytes] = {}
    for _ in range(count):
        if position + 12 > len(data):
            raise MonsterNameError("language archive index is truncated")
        _number, name_size = struct.unpack_from("<II", data, position)
        position += 8
        name = data[position : position + name_size].decode("latin1")
        position += name_size
        if position + 8 > len(data):
            raise MonsterNameError("language archive entry is truncated")
        is_dat, data_size = struct.unpack_from("<II", data, position)
        position += 8
        content = data[position : position + data_size]
        position += data_size
        if wanted is not None and name.lower() not in wanted:
            continue
        if is_dat or name.endswith(".dat"):
            entries[name] = decrypt_text_dat(content)
        else:
            entries[name] = content
    return entries


def _load_text_table(
    suffix: str, path: str | Path | None = None
) -> dict[int, str]:
    source = Path(path) if path is not None else default_lang_path()
    try:
        data = source.read_bytes()
    except OSError:
        return {}

    try:
        entries = _read_archive_entries(data)
    except MonsterNameError:
        return {}

    for name, content in entries.items():
        if name.lower().endswith(suffix):
            return parse_monster_text(
                content.decode("cp1252", "replace")
            )
    return {}


def _load_text_table_by_key(
    suffix: str, path: str | Path | None = None
) -> dict[str, str]:
    """Like :func:`_load_text_table` but keyed by the full ``zts...`` code."""
    source = Path(path) if path is not None else default_lang_path()
    try:
        data = source.read_bytes()
    except OSError:
        return {}

    try:
        entries = _read_archive_entries(data)
    except MonsterNameError:
        return {}

    for name, content in entries.items():
        if name.lower().endswith(suffix):
            return parse_text_table(content.decode("cp1252", "replace"))
    return {}


def _load_monster_keys(path: str | Path | None = None) -> dict[int, str]:
    """Load the vnum → language-key map from ``NSgtdData.NOS``."""
    source = (
        Path(path) if path is not None else default_monster_data_path()
    )
    try:
        data = source.read_bytes()
    except OSError:
        return {}

    try:
        entries = _read_archive_entries(data, {"monster.dat"})
    except MonsterNameError:
        return {}

    for name, content in entries.items():
        if name.lower() == "monster.dat":
            return parse_monster_dat(content.decode("cp1252", "replace"))
    return {}


def read_archive_entries(
    data: bytes, wanted: set[str] | None = None
) -> dict[str, bytes]:
    """Public reader for the shared count/name/size NOS container."""
    return _read_archive_entries(data, wanted)


def load_monster_names(
    path: str | Path | None = None,
    monster_data_path: str | Path | None = None,
) -> dict[int, str]:
    """Load the vnum → monster-name table.

    Joins ``monster.dat`` (vnum → language key, from ``NSgtdData.NOS``) with
    ``_code_<lang>_monster.txt`` (key → name). This indirection matters: the
    live map 103 spawns vnum 419 with key ``zts489e``, i.e. "Violent Seedle",
    not vnum 419's own text record ("Wheat").

    Returns an empty dict when the language table is missing. If
    ``NSgtdData.NOS`` is missing the table is keyed by the text id as a
    best-effort fallback; callers can still fall back to the entity id.
    """
    translations = _load_text_table_by_key("_monster.txt", path)
    if not translations:
        return {}

    keys = _load_monster_keys(monster_data_path)
    if keys:
        return {
            vnum: translations[key]
            for vnum, key in keys.items()
            if key in translations
        }

    fallback: dict[int, str] = {}
    for key, name in translations.items():
        match = re.fullmatch(r"zts(\d+)e", key)
        if match:
            fallback[int(match.group(1))] = name
    return fallback


def load_skill_descriptions(
    path: str | Path | None = None,
) -> dict[int, str]:
    """Load the skill text-id → tooltip table.

    ``_code_<lang>_Skill.txt`` is the client's skill tooltip text (for
    example skill 10 is a strength-course description, 876 a Spell Gun
    description). Keys are the numbers in ``Skill.dat``'s ``NAME zts<id>e``
    codes, not necessarily the skill vnums. A missing table yields an empty
    dict.
    """
    return _load_text_table("_skill.txt", path)
