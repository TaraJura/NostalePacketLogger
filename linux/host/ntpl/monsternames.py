"""NosTale monster names from ``NSlangData_<lang>.NOS``.

The client ships a language archive with a text table per language. The
monster table is named ``_code_<lang>_monster.txt`` and its decrypted content
is one record per template:

    zts<vnum>e<TAB><name>\r
    zts1e<TAB>Baby Fox\r
    zts200e<TAB>Mountain Turtle\r

The archive has no magic string and its layout is:

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

_RECORD = re.compile(r"^zts(\d+)e\t(.*)$")


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


def parse_monster_text(text: str) -> dict[int, str]:
    """Parse the decrypted ``_code_<lang>_monster.txt`` text."""
    names: dict[int, str] = {}
    for record in re.split(r"[\r\n]+", text):
        match = _RECORD.match(record)
        if not match:
            continue
        vnum = int(match.group(1))
        name = match.group(2).strip()
        if name:
            names[vnum] = name
    return names


def _read_archive_entries(data: bytes) -> dict[str, bytes]:
    """Return ``{name: decrypted content}`` for a language archive."""
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
        if is_dat or name.endswith(".dat"):
            entries[name] = decrypt_text_dat(content)
        else:
            entries[name] = content
    return entries


def load_monster_names(path: str | Path | None = None) -> dict[int, str]:
    """Load the vnum → monster-name table.

    Returns an empty dict when the archive or the monster table is missing;
    callers can then fall back to the entity id.
    """
    source = Path(path) if path is not None else default_lang_path()
    try:
        data = source.read_bytes()
    except OSError:
        return {}

    try:
        entries = _read_archive_entries(data)
    except MonsterNameError:
        return {}

    text = None
    for name, content in entries.items():
        if name.endswith("_monster.txt"):
            text = content
            break
    if text is None:
        return {}

    return parse_monster_text(text.decode("cp1252", "replace"))
