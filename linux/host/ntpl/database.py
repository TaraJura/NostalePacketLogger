"""Local NosTale data and packet database.

The packet client already reads the client's archives and a growing protocol
wiki; this module stores everything it can recover in one SQLite file so later
features (NosBazar, item lookups, packet tools, agents) do not have to
re-parse archives or re-fetch public sources.

Contents:

* local client data: every ``Item.dat``, ``monster.dat``, ``Skill.dat`` record,
  map grids/names, and the full ``<language>_nosmall.dat`` catalog;
* the repository's protocol wiki (opcodes, directions, formats, examples);
* public packet/crypto projects cloned from GitHub (NosSmooth, NosCore,
  OpenNos, go-noskit, NosBazar, GflessClient, NosTale-Auth, NosCrypto, ...):
  raw text files are kept and packet headers/fields are indexed.

The network part is explicit (:func:`import_online_sources`), so the test
suite and the GUI never fetch anything by surprise.  The default database is
``~/.config/ntpl/nostale.db``; ``NTPL_DB`` overrides it.

Schema version 1.  Queries are plain SQL; the module only uses the standard
library.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .bazar import BazarConfig, BazarItem, save_config
from .mapdata import DEFAULT_NSTC, load_nstc
from .monsternames import (
    MonsterNameError,
    default_lang_path,
    default_monster_data_path,
    load_monster_names,
    parse_text_table,
    read_archive_entries,
)
from .nosmall import (
    ItemInfo,
    NosMallEntry,
    NosMallGroup,
    bazar_items,
    group_entries,
    known_reference_costs,
    load_entries,
    load_item_names,
)
from .spells import load_spells

SCHEMA_VERSION = 1
DEFAULT_DB_PATH = Path(
    os.environ.get("NTPL_DB")
    or str(Path.home() / ".config/ntpl" / "nostale.db")
)
ONLINE_CACHE = Path(
    os.environ.get("NTPL_SOURCE_CACHE")
    or str(Path.home() / ".cache/ntpl/sources")
)
MAX_STORED_FILE = 2 * 1024 * 1024

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    url TEXT,
    kind TEXT,
    license TEXT,
    revision TEXT,
    fetched_at TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS source_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    sha256 TEXT,
    size INTEGER,
    content TEXT,
    UNIQUE(source_id, path)
);
CREATE TABLE IF NOT EXISTS packets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    opcode TEXT NOT NULL,
    direction TEXT,
    name TEXT,
    format TEXT,
    description TEXT,
    fields_json TEXT,
    examples_json TEXT,
    path TEXT
);
CREATE INDEX IF NOT EXISTS packets_opcode ON packets(opcode);
CREATE INDEX IF NOT EXISTS packets_source ON packets(source_id);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    vnum INTEGER NOT NULL,
    name TEXT,
    category TEXT,
    price INTEGER,
    data_json TEXT,
    UNIQUE(source_id, vnum)
);
CREATE INDEX IF NOT EXISTS items_vnum ON items(vnum);
CREATE INDEX IF NOT EXISTS items_name ON items(name);
CREATE TABLE IF NOT EXISTS monsters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    vnum INTEGER NOT NULL,
    name TEXT,
    level INTEGER,
    data_json TEXT,
    UNIQUE(source_id, vnum)
);
CREATE INDEX IF NOT EXISTS monsters_vnum ON monsters(vnum);
CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    vnum INTEGER NOT NULL,
    name TEXT,
    skill_type INTEGER,
    target_type INTEGER,
    range INTEGER,
    cast_time REAL,
    cooldown REAL,
    mp_cost INTEGER,
    data_json TEXT,
    UNIQUE(source_id, vnum)
);
CREATE TABLE IF NOT EXISTS maps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    map_id INTEGER NOT NULL,
    name TEXT,
    width INTEGER,
    height INTEGER,
    UNIQUE(source_id, map_id)
);
CREATE TABLE IF NOT EXISTS nosmall_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    mall_vnum INTEGER NOT NULL,
    item_vnum INTEGER,
    base_vnum INTEGER,
    quantity INTEGER,
    name TEXT,
    title1 TEXT,
    title2 TEXT,
    cost_json TEXT,
    link_json TEXT,
    data_json TEXT,
    UNIQUE(source_id, mall_vnum)
);
CREATE INDEX IF NOT EXISTS nosmall_item ON nosmall_records(item_vnum);
CREATE INDEX IF NOT EXISTS nosmall_base ON nosmall_records(base_vnum);
CREATE TABLE IF NOT EXISTS nosmall_groups (
    base_vnum INTEGER PRIMARY KEY,
    name TEXT,
    vnums_json TEXT,
    records INTEGER,
    source_id INTEGER
);
"""


@dataclass(frozen=True)
class SourceSpec:
    name: str
    url: str
    license: str = ""
    kind: str = "github"
    note: str = ""
    sparse: tuple[str, ...] = ()


#: Public projects that document NosTale packets, crypto or client data.
ONLINE_SOURCES = (
    SourceSpec(
        "nosmooth",
        "https://github.com/Rutherther/NosSmooth.git",
        "MIT",
        "csharp-packets",
        "Packet classes for most opcodes; [PacketHeader]/[PacketIndex].",
    ),
    SourceSpec(
        "noscore-packets",
        "https://github.com/NosCoreIO/NosCore.Packets.git",
        "MIT",
        "csharp-packets",
        "Modern server packet models with serializers.",
    ),
    SourceSpec(
        "opennos-packets",
        "https://github.com/OpenNos/OpenNos.git",
        "GPL-2.0",
        "csharp-packets",
        "Classic server packet definitions (sparse checkout).",
        ("OpenNos.GameObject/Packets", "OpenNos.Core/Handling"),
    ),
    SourceSpec(
        "go-noskit",
        "https://github.com/Scuym/go-noskit.git",
        "GPL-3.0",
        "go-packets",
        "Clientless bot packet structs and tests.",
    ),
    SourceSpec(
        "nosbazar",
        "https://github.com/hatz2/nosbazar.git",
        "MIT",
        "cpp-packets",
        "NosBazar/Bibi Basar packet packs.",
    ),
    SourceSpec(
        "gfless",
        "https://github.com/hatz2/GflessClient.git",
        "MIT",
        "cpp-client",
        "Gameforge/login protocol emulation.",
    ),
    SourceSpec(
        "nostale-auth",
        "https://github.com/stdLemon/nostale-auth.git",
        "MIT",
        "auth",
        "NoS0577 login code generation.",
    ),
    SourceSpec(
        "nostale-auth-morsisko",
        "https://github.com/morsisko/NosTale-Auth.git",
        "MIT",
        "auth",
        "NoS0577 login packets.",
    ),
    SourceSpec(
        "noscrypto",
        "https://github.com/morsisko/NosCrypto.git",
        "MIT",
        "crypto",
        "Encryption/decryption routines.",
    ),
    SourceSpec(
        "nostale-nb",
        "https://github.com/TaraJura/Nostale-NB.git",
        "unknown",
        "bot",
        "NosBazar market bot, c_blist/c_reg reference.",
    ),
    SourceSpec(
        "devtale",
        "https://github.com/imxeno/devtale.git",
        "GPL-3.0",
        "logger",
        "Packet logger and protocol experiments.",
    ),
    SourceSpec(
        "noscore-protocol",
        "https://github.com/deva-hub/noscore.git",
        "MIT",
        "elixir",
        "Protocol toolkit experiments.",
    ),
    SourceSpec(
        "celestial-protocol",
        "https://github.com/infinity-blackhole/celestial_protocol.git",
        "",
        "elixir",
        "Composition encoder/serializer experiments.",
    ),
    SourceSpec(
        "ntpl-tools",
        "https://github.com/Rutherther/NosTale-PacketLogger.git",
        "MIT",
        "logger",
        "Packet log loading/filtering tools.",
    ),
)

TEXT_SUFFIXES = (
    ".cs", ".go", ".cpp", ".c", ".h", ".hpp", ".py", ".lua", ".md",
    ".json", ".yml", ".yaml", ".txt", ".ex", ".exs", ".rs",
)
PACKET_HEADER = re.compile(
    r'\[\s*PacketHeader\(\s*"([^"]+)"(?:\s*,\s*([^\)]+))?\s*\)\s*\]'
)
PACKET_ALIAS = re.compile(
    r'\[\s*PacketHeaderAlias\(\s*"([^"]+)"'
)
CS_CLASS = re.compile(
    r'\b(?:class|record|struct)\s+([A-Za-z_]\w*)'
)
CS_FIELD = re.compile(
    r'\[\s*PacketIndex\(\s*(\d+)[^\]]*\)\s*\]\s*'
    r'(?:public|private|internal|protected)?\s*'
    r'([A-Za-z_][\w<>,?\.\[\]\s]*?)\s+([A-Za-z_]\w*)'
)
GO_NAME = re.compile(
    r'func\s*\(\s*\w+\s+\w+\s*\)\s*Name\(\)\s*string\s*\{\s*'
    r'return\s+"([^"]+)"',
    re.S,
)
GO_OPCODE = re.compile(r'Opcode\s*[:=]\s*"([^"]+)"')
CPP_OPCODE = re.compile(r'opcode\s*=\s*"([^"]+)"')
PY_OPCODE = re.compile(r'(?:opcode|OPCODE)\s*[:=]\s*["\']([a-zA-Z0-9_]+)["\']')
MD_OPCODE = re.compile(
    r'^\|\s*`?([a-zA-Z0-9_]+)`?\s*\|\s*'
    r'(SEND/RECV|SEND|RECV|C2S|S2C)\s*\|\s*([^|]+)\|',
    re.M,
)


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open/create the database and make sure the schema exists."""
    target = Path(path) if path is not None else DEFAULT_DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target))
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    connection.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    connection.commit()
    return connection


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _source(
    connection: sqlite3.Connection,
    name: str,
    url: str = "",
    kind: str = "",
    license: str = "",
    revision: str = "",
    note: str = "",
) -> int:
    connection.execute(
        """
        INSERT INTO sources(name, url, kind, license, revision, fetched_at, note)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            url=excluded.url, kind=excluded.kind, license=excluded.license,
            revision=excluded.revision, fetched_at=excluded.fetched_at,
            note=excluded.note
        """,
        (name, url, kind, license, revision, _now(), note),
    )
    row = connection.execute(
        "SELECT id FROM sources WHERE name=?", (name,)
    ).fetchone()
    assert row is not None
    return int(row["id"])


def _clear_source(connection: sqlite3.Connection, source_id: int) -> None:
    for table in (
        "source_files", "packets", "items", "monsters", "skills", "maps",
        "nosmall_records", "nosmall_groups",
    ):
        connection.execute(f"DELETE FROM {table} WHERE source_id=?", (source_id,))


def _store_file(
    connection: sqlite3.Connection,
    source_id: int,
    path: str,
    content: str | bytes,
) -> None:
    raw = content.encode("utf-8", "replace") if isinstance(content, str) else content
    text = content if isinstance(content, str) else None
    if text is not None and len(raw) > MAX_STORED_FILE:
        text = text[:MAX_STORED_FILE]
    connection.execute(
        """
        INSERT OR REPLACE INTO source_files(source_id, path, sha256, size, content)
        VALUES(?, ?, ?, ?, ?)
        """,
        (
            source_id,
            path,
            hashlib.sha256(raw).hexdigest(),
            len(raw),
            text,
        ),
    )


def _entry(entries: dict[str, bytes], name: str) -> bytes | None:
    wanted = name.lower()
    for key, value in entries.items():
        if key.lower() == wanted:
            return value
    return None


def _parse_records(text: str) -> list[dict[str, list[str]]]:
    records: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.startswith("#===="):
            if current:
                records.append(current)
                current = {}
            continue
        if "\t" not in line:
            continue
        fields = line.split("\t")
        # Item.dat/monster.dat records put a leading tab before the keyword;
        # NosMall records do not. Normalise both.
        while fields and not fields[0].strip():
            fields = fields[1:]
        if not fields:
            continue
        current[fields[0].strip()] = fields[1:]
    if current:
        records.append(current)
    return records


def _ints(values: list[str], count: int = 0) -> list[int]:
    out: list[int] = []
    for value in values:
        try:
            out.append(int(value))
        except ValueError:
            break
        if count and len(out) >= count:
            break
    return out


def _map_names() -> dict[int, str]:
    try:
        entries = read_archive_entries(
            default_lang_path().read_bytes(),
            wanted={"_code_uk_mapiddata.txt"},
        )
    except (OSError, MonsterNameError):
        return {}
    text = _entry(entries, "_code_uk_MapIDData.txt")
    if not text:
        return {}
    names: dict[int, str] = {}
    for key, value in parse_text_table(
        text.decode("cp1252", "replace")
    ).items():
        match = re.fullmatch(r"zts(\d+)e", key)
        if match:
            names[int(match.group(1))] = value
    return names


def import_local_game_data(
    connection: sqlite3.Connection,
    game_dir: str | Path | None = None,
) -> dict[str, int]:
    """Import the client archives and map grids; returns row counts."""
    directory = (
        Path(game_dir)
        if game_dir is not None
        else Path(str(DEFAULT_NSTC)).parent
    )
    gtd = directory / "NSgtdData.NOS"
    lang_dir = directory
    revision = f"{gtd.stat().st_size if gtd.exists() else 0}"
    source_id = _source(
        connection,
        "local-client-data",
        str(directory),
        "local",
        "NosTale client data",
        revision,
        "Decrypted NSgtdData/NSlangData archives and NStcData grids",
    )
    _clear_source(connection, source_id)
    counts = {"items": 0, "monsters": 0, "skills": 0, "maps": 0,
              "nosmall_records": 0, "nosmall_groups": 0}

    if not gtd.is_file():
        raise FileNotFoundError(f"missing client data archive: {gtd}")
    try:
        gtd_entries = read_archive_entries(
            gtd.read_bytes(),
            wanted={
                "item.dat", "monster.dat", "skill.dat", "npctalk.dat",
                "shoptype.dat", "mapiddata.dat", "uk_nosmall.dat",
            },
        )
    except (OSError, MonsterNameError) as exc:
        raise ValueError(f"cannot read {gtd}: {exc}") from exc

    item_text = _entry(gtd_entries, "Item.dat")
    item_names = load_item_names(gtd, lang_dir)
    if item_text:
        _store_file(connection, source_id, "Item.dat", item_text)
        for record in _parse_records(item_text.decode("cp1252", "replace")):
            vnum_values = _ints(record.get("VNUM", []), 2)
            if not vnum_values:
                continue
            vnum = vnum_values[0]
            name_key = record.get("NAME", [""])[0]
            name = item_names.get(vnum) or name_key
            price = vnum_values[1] if len(vnum_values) > 1 else None
            category = (record.get("TYPE") or [""])[0]
            connection.execute(
                """
                INSERT OR REPLACE INTO items(source_id, vnum, name, category,
                                            price, data_json)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id, vnum, name, category, price,
                    json.dumps(record, ensure_ascii=False),
                ),
            )
            counts["items"] += 1

    monster_text = _entry(gtd_entries, "monster.dat")
    monster_names = load_monster_names()
    if monster_text:
        _store_file(connection, source_id, "monster.dat", monster_text)
        for record in _parse_records(
            monster_text.decode("cp1252", "replace")
        ):
            values = _ints(record.get("VNUM", []), 1)
            if not values:
                continue
            vnum = values[0]
            levels = _ints(record.get("LEVEL", []), 1)
            connection.execute(
                """
                INSERT OR REPLACE INTO monsters(source_id, vnum, name,
                                               level, data_json)
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    source_id, vnum, monster_names.get(vnum, ""),
                    levels[0] if levels else None,
                    json.dumps(record, ensure_ascii=False),
                ),
            )
            counts["monsters"] += 1

    skill_text = _entry(gtd_entries, "Skill.dat")
    if skill_text:
        _store_file(connection, source_id, "Skill.dat", skill_text)
    spells = load_spells(gtd)
    for vnum, spell in spells.items():
        connection.execute(
            """
            INSERT OR REPLACE INTO skills(
                source_id, vnum, name, skill_type, target_type, range,
                cast_time, cooldown, mp_cost, data_json)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id, vnum, spell.name, spell.skill_type,
                spell.target_type, spell.range, spell.cast_time,
                spell.cooldown, spell.mp_cost,
                json.dumps(spell.__dict__, ensure_ascii=False),
            ),
        )
        counts["skills"] += 1

    for archive in (
        "npctalk.dat", "shoptype.dat", "MapIDData.dat", "uk_nosmall.dat"
    ):
        content = _entry(gtd_entries, archive)
        if content:
            _store_file(connection, source_id, archive, content)

    map_names = _map_names()
    try:
        grids = load_nstc(directory / "NStcData.NOS")
    except (OSError, ValueError):
        grids = {}
    for map_id, grid in grids.items():
        connection.execute(
            """
            INSERT OR REPLACE INTO maps(source_id, map_id, name, width, height)
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                source_id, map_id, map_names.get(map_id, ""),
                grid.width, grid.height,
            ),
        )
        counts["maps"] += 1

    entries = load_entries(gtd, lang_dir)
    for entry in entries:
        connection.execute(
            """
            INSERT OR REPLACE INTO nosmall_records(
                source_id, mall_vnum, item_vnum, base_vnum, quantity, name,
                title1, title2, cost_json, link_json, data_json)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id, entry.mall_vnum, entry.item_vnum, entry.base_vnum,
                entry.quantity, entry.name, entry.title1, entry.title2,
                json.dumps(entry.cost), json.dumps(entry.link),
                json.dumps(entry.to_dict(), ensure_ascii=False),
            ),
        )
        counts["nosmall_records"] += 1
    groups = group_entries(entries)
    for group in groups:
        connection.execute(
            """
            INSERT OR REPLACE INTO nosmall_groups(
                base_vnum, name, vnums_json, records, source_id)
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                group.base_vnum, group.name, json.dumps(list(group.vnums)),
                group.records, source_id,
            ),
        )
        counts["nosmall_groups"] += 1

    connection.commit()
    return counts


def import_protocol_docs(
    connection: sqlite3.Connection,
    docs_dir: str | Path | None = None,
) -> int:
    """Index the repository's Markdown protocol wiki into ``packets``."""
    directory = (
        Path(docs_dir)
        if docs_dir is not None
        else Path(__file__).resolve().parents[3] / "docs" / "protocol"
    )
    source_id = _source(
        connection, "local-protocol-wiki", str(directory), "wiki", "project docs",
        "", "Local docs/protocol Markdown tables",
    )
    _clear_source(connection, source_id)
    count = 0
    if not directory.is_dir():
        connection.commit()
        return count
    for path in sorted(directory.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        _store_file(connection, source_id, path.name, text)
        for match in MD_OPCODE.finditer(text):
            opcode, direction, description = match.groups()
            connection.execute(
                """
                INSERT INTO packets(source_id, opcode, direction, name,
                                    format, description, fields_json,
                                    examples_json, path)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id, opcode.lower(), direction, "",
                    "", description.strip(), "[]", "[]", path.name,
                ),
            )
            count += 1
    connection.commit()
    return count


# -- online sources ------------------------------------------------------


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {result.stderr.strip()[:400]}"
        )
    return result.stdout.strip()


def _clone_source(spec: SourceSpec, cache: Path) -> tuple[Path, str]:
    target = cache / spec.name
    if (target / ".git").is_dir():
        try:
            revision = _run_git(["rev-parse", "HEAD"], target)
        except RuntimeError:
            revision = ""
        return target, revision
    if target.exists():
        # A partial/previous copy without .git: keep it, no revision.
        return target, ""
    target.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone", "--depth", "1"]
    if spec.sparse:
        args += ["--filter=blob:none", "--sparse"]
    args += [spec.url, str(target)]
    _run_git(args)
    if spec.sparse:
        _run_git(["sparse-checkout", "set", *spec.sparse], target)
    revision = _run_git(["rev-parse", "HEAD"], target)
    return target, revision


def _is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES


def _walk_text_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if not _is_text(path):
            continue
        try:
            if path.stat().st_size > MAX_STORED_FILE:
                continue
        except OSError:
            continue
        yield path


def _direction(path: Path, header: str = "", source: str = "") -> str:
    text = f"{path.as_posix()} {header} {source}".lower()
    if "packetsource.client" in text or "clientpackets" in text:
        return "C2S"
    if "packetsource.server" in text or "serverpackets" in text:
        return "S2C"
    if "/client/" in text or text.endswith("client"):
        return "C2S"
    if "/server/" in text or text.endswith("server"):
        return "S2C"
    return "unknown"


def _cs_packets(text: str, path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    headers = list(PACKET_HEADER.finditer(text))
    for match in headers:
        opcode = match.group(1)
        source_hint = match.group(2) or ""
        remainder = text[match.end(): match.end() + 3000]
        class_match = CS_CLASS.search(remainder)
        name = class_match.group(1) if class_match else path.stem
        chunk = remainder[: class_match.start()] if class_match else remainder
        if class_match:
            chunk = remainder[class_match.end():]
        fields = [
            {
                "index": int(field.group(1)),
                "type": " ".join(field.group(2).split()),
                "name": field.group(3),
            }
            for field in CS_FIELD.finditer(chunk)
        ]
        fields.sort(key=lambda item: item["index"])
        direction = _direction(path, source_hint)
        format_text = " ".join(
            [opcode] + [str(field["name"]) for field in fields]
        )
        description = ""
        doc = re.search(
            r'///\s*<summary>(.*?)</summary>', text[max(0, match.start()-1500):match.start()],
            re.S,
        )
        if doc:
            description = " ".join(doc.group(1).split())
        out.append(
            {
                "opcode": opcode.lower(),
                "direction": direction,
                "name": name,
                "format": format_text,
                "description": description,
                "fields": fields,
                "path": path.as_posix(),
            }
        )
    for alias in PACKET_ALIAS.finditer(text):
        out.append(
            {
                "opcode": alias.group(1).lower(),
                "direction": _direction(path),
                "name": path.stem,
                "format": alias.group(1),
                "description": "PacketHeaderAlias",
                "fields": [],
                "path": path.as_posix(),
            }
        )
    return out


def _go_packets(text: str, path: Path) -> list[dict[str, Any]]:
    opcodes = set(GO_NAME.findall(text)) | set(GO_OPCODE.findall(text))
    return [
        {
            "opcode": opcode.lower(),
            "direction": _direction(path),
            "name": path.stem,
            "format": opcode,
            "description": "Go packet",
            "fields": [],
            "path": path.as_posix(),
        }
        for opcode in opcodes
        if re.fullmatch(r"[a-zA-Z0-9_]+", opcode)
    ]


def _generic_packets(text: str, path: Path) -> list[dict[str, Any]]:
    opcodes = set(CPP_OPCODE.findall(text)) | set(PY_OPCODE.findall(text))
    return [
        {
            "opcode": opcode.lower(),
            "direction": _direction(path),
            "name": path.stem,
            "format": opcode,
            "description": "Packet definition",
            "fields": [],
            "path": path.as_posix(),
        }
        for opcode in opcodes
        if re.fullmatch(r"[a-zA-Z0-9_]+", opcode)
    ]


def extract_packets(text: str, path: Path) -> list[dict[str, Any]]:
    """Extract packet definitions from C#, Go, C++ or Python source."""
    suffix = path.suffix.lower()
    if suffix == ".cs":
        return _cs_packets(text, path) + _generic_packets(text, path)
    if suffix == ".go":
        return _go_packets(text, path)
    return _generic_packets(text, path)


def import_online_sources(
    connection: sqlite3.Connection,
    cache_dir: str | Path | None = None,
    only: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Clone/scan the public projects and index their packets.

    Network access happens only here.  Existing clones under
    ``~/.cache/ntpl/sources`` are reused.  Returns per-source stats.
    """
    cache = Path(cache_dir) if cache_dir is not None else ONLINE_CACHE
    wanted = set(only) if only is not None else None
    result: dict[str, dict[str, Any]] = {}
    for spec in ONLINE_SOURCES:
        if wanted is not None and spec.name not in wanted:
            continue
        stats: dict[str, Any] = {"files": 0, "packets": 0, "error": ""}
        try:
            root, revision = _clone_source(spec, cache)
        except Exception as exc:  # noqa: BLE001 - network/CLI failures
            stats["error"] = str(exc)[:300]
            result[spec.name] = stats
            continue
        source_id = _source(
            connection, spec.name, spec.url, spec.kind, spec.license,
            revision, spec.note,
        )
        _clear_source(connection, source_id)
        for path in _walk_text_files(root):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            relative = path.relative_to(root).as_posix()
            _store_file(connection, source_id, relative, text)
            stats["files"] += 1
            for packet in extract_packets(text, path):
                connection.execute(
                    """
                    INSERT INTO packets(
                        source_id, opcode, direction, name, format,
                        description, fields_json, examples_json, path)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id, packet["opcode"], packet["direction"],
                        packet["name"], packet["format"],
                        packet["description"],
                        json.dumps(packet["fields"], ensure_ascii=False),
                        "[]", packet["path"],
                    ),
                )
                stats["packets"] += 1
        hidden = root / "LICENSE"
        if hidden.is_file():
            _store_file(
                connection, source_id, "LICENSE",
                hidden.read_text(encoding="utf-8", errors="replace"),
            )
        connection.commit()
        result[spec.name] = stats
    return result


# -- queries and export --------------------------------------------------


def stats(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "sources", "source_files", "packets", "items", "monsters", "skills",
        "maps", "nosmall_records", "nosmall_groups",
    )
    return {
        table: int(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        )
        for table in tables
    }


def search_packets(
    connection: sqlite3.Connection,
    opcode: str | None = None,
    query: str = "",
    source: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses = []
    values: list[Any] = []
    if opcode:
        clauses.append("p.opcode = ?")
        values.append(opcode.lower())
    if query:
        clauses.append("(p.opcode LIKE ? OR p.name LIKE ? OR p.description LIKE ?)")
        pattern = f"%{query}%"
        values.extend([pattern, pattern, pattern])
    if source:
        clauses.append("s.name = ?")
        values.append(source)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"""
        SELECT p.*, s.name AS source_name
        FROM packets p JOIN sources s ON s.id = p.source_id
        {where}
        ORDER BY p.opcode, s.name
        LIMIT ?
        """,
        (*values, max(1, min(500, limit))),
    ).fetchall()
    return [dict(row) for row in rows]


def item_info(connection: sqlite3.Connection) -> dict[int, ItemInfo]:
    """Item.dat names, NPC prices and categories keyed by template vnum.

    The GUI prefers this over re-parsing ``NSgtdData.NOS``: the database is
    already built and the lookup is a few milliseconds. Rows whose name is
    only the untranslated ``zts...e`` key fall back to ``item <vnum>``.
    """
    rows = connection.execute(
        """
        SELECT i.vnum, i.name, i.price, i.category
        FROM items i
        JOIN (SELECT vnum, MAX(id) AS id FROM items GROUP BY vnum) last
          ON last.id = i.id
        """
    ).fetchall()
    info: dict[int, ItemInfo] = {}
    for row in rows:
        try:
            vnum = int(row["vnum"])
        except (TypeError, ValueError):
            continue
        if vnum <= 0:
            continue
        name = str(row["name"] or "").strip()
        if not name or re.fullmatch(r"zts\d+e", name):
            name = f"item {vnum}"
        try:
            price = max(0, int(row["price"] or 0))
        except (TypeError, ValueError):
            price = 0
        try:
            category = int(row["category"] or 0)
        except (TypeError, ValueError):
            category = 0
        info[vnum] = ItemInfo(vnum, name, price, category)
    return info


def search_items(
    connection: sqlite3.Connection,
    query: str = "",
    vnum: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    clauses = []
    values: list[Any] = []
    if vnum is not None:
        clauses.append("vnum = ?")
        values.append(vnum)
    if query:
        clauses.append("(name LIKE ? OR CAST(vnum AS TEXT) = ?)")
        values.extend([f"%{query}%", query])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"""
        SELECT vnum, name, category, price, data_json
        FROM items {where}
        ORDER BY vnum LIMIT ?
        """,
        (*values, max(1, min(500, limit))),
    ).fetchall()
    return [dict(row) for row in rows]


def nosmall_groups(
    connection: sqlite3.Connection,
    query: str = "",
    limit: int = 1000,
) -> list[NosMallGroup]:
    clauses = []
    values: list[Any] = []
    if query:
        clauses.append("(name LIKE ? OR CAST(base_vnum AS TEXT) = ?)")
        values.extend([f"%{query}%", query])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"""
        SELECT g.base_vnum, g.name, g.vnums_json, g.records,
               (SELECT group_concat(DISTINCT json_extract(r.data_json, '$.kind'))
                  FROM nosmall_records r
                 WHERE r.base_vnum = g.base_vnum) AS kinds
        FROM nosmall_groups g {where}
        ORDER BY g.name COLLATE NOCASE, g.base_vnum LIMIT ?
        """,
        (*values, max(1, min(5000, limit))),
    ).fetchall()
    groups: list[NosMallGroup] = []
    for row in rows:
        try:
            vnums = tuple(int(v) for v in json.loads(row["vnums_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        kinds: list[int] = []
        for value in str(row["kinds"] or "").split(","):
            try:
                kinds.append(int(value))
            except ValueError:
                continue
        groups.append(
            NosMallGroup(
                base_vnum=int(row["base_vnum"]),
                name=row["name"] or f"item {row['base_vnum']}",
                vnums=vnums,
                records=int(row["records"] or 0),
                kinds=tuple(sorted(set(kinds))),
            )
        )
    return groups


def export_bazar(
    connection: sqlite3.Connection,
    path: str | Path,
    limit: int = 5000,
    costs: dict[int, float] | None = None,
) -> int:
    """Export the catalog as a BazarConfig (monitor-only by default)."""
    groups = nosmall_groups(connection, limit=limit)
    items = bazar_items(groups, known_reference_costs() if costs is None else costs)
    config = BazarConfig.from_dict(
        {
            "version": 1,
            "character": "",
            "refresh": 30.0,
            "npc_id": None,
            "open_option": 60,
            "open_page": 0,
            "items": [item.to_dict() for item in items],
        }
    )
    save_config(path, config)
    return len(items)


def build(
    db_path: str | Path | None = None,
    game_dir: str | Path | None = None,
    *,
    online: bool = True,
    cache_dir: str | Path | None = None,
    only: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build the local data, protocol wiki and optionally online sources."""
    connection = connect(db_path)
    try:
        local = import_local_game_data(connection, game_dir)
        wiki = import_protocol_docs(connection)
        online_stats = (
            import_online_sources(connection, cache_dir, only)
            if online
            else {}
        )
        return {
            "database": str(
                Path(db_path) if db_path is not None else DEFAULT_DB_PATH
            ),
            "local": local,
            "protocol_packets": wiki,
            "online": online_stats,
            "stats": stats(connection),
        }
    finally:
        connection.close()
