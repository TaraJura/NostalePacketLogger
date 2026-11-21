"""NosMall catalog from the client's own game data.

The cash shop is not a fixed vnum list: ``NSgtdData.NOS`` carries
``<language>_nosmall.dat`` with thousands of mall records.  Each record has a
mall vnum, one or more item vnums, localized title keys and a COST block.
NosBazar searches take item vnums, so a mall record maps to a
``c_blist`` group.

The record format (decrypted ``uk_nosmall.dat``) is::

    VNUM    43 999999 0 0 0 1 1
    ITEM    1 0 2174 2174 0 4
    TITLE1  zts120e
    TITLE2  zts121e
    COST    999999 0 1 10 2 0
    LINK    0 0 0 0 0 0

``ITEM[2]`` is the variant item vnum (what the bazaar sells); ``ITEM[3]`` is
the base item shared by equivalent variants.  Grouping by the base reproduces
the reference bot's groups, e.g. ``stone bles`` = 1362/5195/9075 and
``bubbl`` = 1261/2174/9480/10029.

Titles are text-table keys.  ``TITLE2`` is normally the item/bundle name;
``TITLE1`` is marketing copy.  Some records lack a localized key, so fall back
to the item name from ``Item.dat``.  Prices are *not* in this file: the
client's COST block does not carry the current web-shop ND price, so imported
entries have ``nos_cost=None`` until the user fills it in (or a price map is
provided).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .bazar import BazarItem, build_search_packet
from .monsternames import (
    MonsterNameError,
    default_lang_path,
    default_monster_data_path,
    parse_monster_dat,
    parse_text_table,
    read_archive_entries,
)

NOSMALL_SUFFIX = "_nosmall.dat"
RECORD_MARKER = "#===="


@dataclass(frozen=True)
class NosMallEntry:
    """One row of a ``<language>_nosmall.dat`` file."""

    mall_vnum: int
    item_vnum: int
    base_vnum: int
    kind: int = 0
    quantity: int = 1
    title1: str = ""
    title2: str = ""
    name: str = ""
    cost: tuple[int, ...] = ()
    link: tuple[int, ...] = ()
    record: int = 0

    def to_dict(self) -> dict:
        return {
            "mall_vnum": self.mall_vnum,
            "item_vnum": self.item_vnum,
            "base_vnum": self.base_vnum,
            "kind": self.kind,
            "quantity": self.quantity,
            "title1": self.title1,
            "title2": self.title2,
            "name": self.name,
            "cost": list(self.cost),
            "link": list(self.link),
        }


@dataclass(frozen=True)
class NosMallGroup:
    """Equivalent mall variants, searched as one ``c_blist`` group."""

    base_vnum: int
    name: str
    vnums: tuple[int, ...]
    records: int = 0
    titles: tuple[str, ...] = ()
    #: NosMall ITEM[0] of every record in the group. 2 is the partner/pet
    #: supply tab, 4 the pet tab; the rest is used by the name classifier.
    kinds: tuple[int, ...] = ()

    @property
    def is_pet(self) -> bool:
        return is_pet_group(self)

    def search_packet(self) -> str:
        return build_search_packet(list(self.vnums))

    def to_dict(self) -> dict:
        return {
            "base_vnum": self.base_vnum,
            "name": self.name,
            "vnums": list(self.vnums),
            "records": self.records,
            "titles": list(self.titles),
        }


@dataclass(frozen=True)
class ItemInfo:
    """One ``Item.dat`` row: localized name, NPC price and category."""

    vnum: int
    name: str
    npc_price: int = 0
    category: int = 0


#: NosMall record categories that only hold companions and their supplies.
PET_KINDS = frozenset({2, 4})
#: Name words for pet/fairy/partner items whose record category is the
#: ordinary item tab (for example ``Pet Food`` is a kind 0 record, and the
#: first-generation pets are in the mixed costume tab).
PET_NAME_WORDS = frozenset(
    {
        "pet",
        "pets",
        "fairy",
        "fairies",
        "partner",
        "partners",
        "sellaim",
        "woondine",
        "eperial",
        "turik",
    }
)


def is_pet_name(name: str) -> bool:
    """True when a catalog/item name is a pet, fairy or partner item."""
    return bool(PET_NAME_WORDS.intersection(match_text(name).split()))


def is_pet_group(group: NosMallGroup) -> bool:
    """True for pets, fairies and partner supplies.

    The NosBazar monitor must not import them: they are companion items, not
    ordinary mall goods, and the user asked to keep them out of the list.
    Whole-word matching keeps ``Magic Carpet`` and ``Petal Wings`` in.
    """
    if PET_KINDS.intersection(group.kinds):
        return True
    return is_pet_name(group.name)


def _text_tables(names: dict[str, str], data: bytes) -> None:
    try:
        for key, value in parse_text_table(
            data.decode("cp1252", "replace")
        ).items():
            names.setdefault(key, value)
    except (UnicodeError, ValueError):
        return


def load_item_names(
    gtd_path: str | Path | None = None,
    lang_dir: str | Path | None = None,
) -> dict[int, str]:
    """Return ``{item_vnum: name}`` from Item.dat and all language tables."""
    source = (
        Path(gtd_path)
        if gtd_path is not None
        else default_monster_data_path()
    )
    try:
        entries = read_archive_entries(
            source.read_bytes(), wanted={"item.dat"}
        )
    except (OSError, MonsterNameError):
        return {}
    item_text = entries.get("Item.dat")
    if not item_text:
        return {}
    keys = parse_monster_dat(item_text.decode("cp1252", "replace"))

    names: dict[str, str] = {}
    lang_source = (
        Path(lang_dir) if lang_dir is not None else default_lang_path().parent
    )
    for archive in _language_archives(lang_source):
        code = archive.stem.split("_")[-1].lower()
        try:
            texts = read_archive_entries(
                archive.read_bytes(),
                wanted={f"_code_{code}_item.txt"},
            )
        except (OSError, MonsterNameError):
            continue
        for text in texts.values():
            _text_tables(names, text)

    return {
        vnum: names.get(key, "")
        for vnum, key in keys.items()
        if names.get(key)
    }


def load_item_info(
    gtd_path: str | Path | None = None,
    lang_dir: str | Path | None = None,
) -> dict[int, ItemInfo]:
    """Return ``{item_vnum: ItemInfo}`` from ``Item.dat``.

    ``Item.dat`` rows carry a leading tab before the keyword::

        \tVNUM\t2174\t3000
        \tNAME\tzts...
        \tTYPE\t0\t1

    ``VNUM[0]`` is the item vnum and ``VNUM[1]`` its NPC shop price;
    ``TYPE[0]`` is the item category.  Names are resolved once through
    :func:`load_item_names`; an item without a localized name gets the
    ``item <vnum>`` fallback.  Missing or unreadable client data returns an
    empty table, like :func:`load_item_names`.
    """
    source = (
        Path(gtd_path)
        if gtd_path is not None
        else default_monster_data_path()
    )
    try:
        entries = read_archive_entries(
            source.read_bytes(), wanted={"item.dat"}
        )
    except (OSError, MonsterNameError):
        return {}
    item_text = next(
        (
            content
            for name, content in entries.items()
            if name.lower() == "item.dat"
        ),
        None,
    )
    if not item_text:
        return {}

    names = load_item_names(source, lang_dir)
    info: dict[int, ItemInfo] = {}
    for record in _parse_records(item_text.decode("cp1252", "replace")):
        values = _ints(record.get("VNUM", []), 2)
        if not values or values[0] <= 0:
            continue
        vnum = values[0]
        npc_price = values[1] if len(values) > 1 and values[1] > 0 else 0
        types = _ints(record.get("TYPE", []), 1)
        info[vnum] = ItemInfo(
            vnum=vnum,
            name=names.get(vnum) or f"item {vnum}",
            npc_price=npc_price,
            category=types[0] if types else 0,
        )
    return info


def _nosmall_text(
    gtd_path: str | Path | None = None,
    lang_dir: str | Path | None = None,
) -> dict[str, str]:
    """Merge the localized NosMall title/description tables."""
    names: dict[str, str] = {}
    source = (
        Path(lang_dir) if lang_dir is not None else default_lang_path().parent
    )
    for archive in _language_archives(source):
        code = archive.stem.split("_")[-1].lower()
        try:
            texts = read_archive_entries(
                archive.read_bytes(),
                wanted={f"_code_{code}_nosmall.txt"},
            )
        except (OSError, MonsterNameError):
            continue
        for text in texts.values():
            _text_tables(names, text)
    return names


def _parse_records(text: str) -> list[dict[str, list[str]]]:
    records: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.startswith(RECORD_MARKER):
            if current:
                records.append(current)
                current = {}
            continue
        if "\t" not in line:
            continue
        fields = line.split("\t")
        # Item.dat records put a leading tab before the keyword; NosMall
        # records do not. Normalise both.
        while fields and not fields[0].strip():
            fields = fields[1:]
        if not fields:
            continue
        current[fields[0].strip()] = fields[1:]
    if current:
        records.append(current)
    return records


def _ints(values: list[str], count: int = 0) -> tuple[int, ...]:
    out: list[int] = []
    for value in values:
        try:
            out.append(int(value))
        except ValueError:
            break
        if count and len(out) >= count:
            break
    return tuple(out)


def load_entries(
    gtd_path: str | Path | None = None,
    lang_dir: str | Path | None = None,
) -> list[NosMallEntry]:
    """Parse the client's NosMall records with localized names."""
    source = (
        Path(gtd_path)
        if gtd_path is not None
        else default_monster_data_path()
    )
    try:
        archives = read_archive_entries(
            source.read_bytes(),
            wanted={
                name.lower()
                for name in _archive_names(source, NOSMALL_SUFFIX)
            },
        )
    except (OSError, MonsterNameError):
        return []
    data = None
    for preferred in (
        "uk_nosmall.dat",
        "cz_nosmall.dat",
        "de_nosmall.dat",
    ):
        for name, content in archives.items():
            if name.lower() == preferred:
                data = content
                break
        if data is not None:
            break
    if data is None:
        for name, content in archives.items():
            if name.lower().endswith(NOSMALL_SUFFIX):
                data = content
                break
    if data is None:
        return []

    mall_text = _nosmall_text(gtd_path, lang_dir)
    item_names = load_item_names(gtd_path, lang_dir)
    entries: list[NosMallEntry] = []
    for index, record in enumerate(
        _parse_records(data.decode("cp1252", "replace"))
    ):
        mall_values = _ints(record.get("VNUM", []), 1)
        item = _ints(record.get("ITEM", []), 6)
        if not mall_values or len(item) < 4:
            continue
        mall_vnum = mall_values[0]
        item_vnum, base_vnum = item[2], item[3]
        if item_vnum <= 0 or base_vnum <= 0:
            continue
        title1 = record.get("TITLE1", [""])[0]
        title2 = record.get("TITLE2", [""])[0]
        # Item.dat names are consistent and localized; mall titles often add
        # a quantity ("10 Wings of Friendship") or are missing in some
        # language archives.
        name = (
            item_names.get(item_vnum)
            or item_names.get(base_vnum)
            or mall_text.get(title2)
            or mall_text.get(title1)
            or f"item {item_vnum}"
        )
        cost = _ints(record.get("COST", []))
        link = _ints(record.get("LINK", []))
        quantity = cost[3] if len(cost) > 3 and 0 < cost[3] <= 1000 else 1
        entries.append(
            NosMallEntry(
                mall_vnum=mall_vnum,
                item_vnum=item_vnum,
                base_vnum=base_vnum,
                kind=item[0],
                quantity=quantity,
                title1=title1,
                title2=title2,
                name=name,
                cost=cost,
                link=link,
                record=index,
            )
        )
    return entries


def _language_archives(directory: Path) -> list[Path]:
    """UK first (the client's English data), then the other languages."""
    archives = list(directory.glob("NSlangData_*.NOS"))
    return sorted(
        archives,
        key=lambda path: (
            0 if path.stem.lower().endswith("_uk") else 1,
            path.name,
        ),
    )


def _archive_names(path: Path, suffix: str) -> list[str]:
    """Entry names in the archive that end with ``suffix`` (lower-case)."""
    import struct

    try:
        data = path.read_bytes()
    except OSError:
        return []
    if len(data) < 4:
        return []
    count = struct.unpack_from("<I", data, 0)[0]
    position = 4
    names: list[str] = []
    for _ in range(count):
        if position + 12 > len(data):
            break
        _number, name_size = struct.unpack_from("<II", data, position)
        position += 8
        name = data[position : position + name_size].decode(
            "latin1", "replace"
        )
        position += name_size
        if position + 8 > len(data):
            break
        _is_dat, data_size = struct.unpack_from("<II", data, position)
        position += 8 + data_size
        if name.lower().endswith(suffix):
            names.append(name)
    return names


def group_entries(
    entries: list[NosMallEntry],
) -> list[NosMallGroup]:
    """Group variant vnums by the base item vnum, one search per group."""
    groups: dict[int, dict] = {}
    for entry in entries:
        group = groups.setdefault(
            entry.base_vnum,
            {
                "vnums": set(),
                "names": [],
                "titles": set(),
                "kinds": set(),
                "records": 0,
            },
        )
        group["vnums"].add(entry.item_vnum)
        group["kinds"].add(entry.kind)
        group["records"] += 1
        group["titles"].add(entry.title2 or entry.title1)
        if entry.name and not entry.name.startswith("item "):
            group["names"].append(entry.name)

    result: list[NosMallGroup] = []
    for base, group in groups.items():
        name = group["names"][0] if group["names"] else f"item {base}"
        result.append(
            NosMallGroup(
                base_vnum=base,
                name=name,
                vnums=tuple(sorted(group["vnums"])),
                records=group["records"],
                titles=tuple(sorted(t for t in group["titles"] if t)),
                kinds=tuple(sorted(group["kinds"])),
            )
        )
    result.sort(key=lambda item: (item.name.lower(), item.base_vnum))
    return result


def load_catalog(
    gtd_path: str | Path | None = None,
    lang_dir: str | Path | None = None,
) -> list[NosMallGroup]:
    """Convenience: parse the client data and return grouped mall items."""
    return group_entries(load_entries(gtd_path, lang_dir))


def bazar_items(
    groups: list[NosMallGroup],
    costs: dict[int, float] | None = None,
) -> list[BazarItem]:
    """Turn catalog groups into monitor-only Bazar items.

    ``costs`` maps a base vnum (or a variant vnum) to its NosMall ND cost.
    Without a price map the items are still useful for price discovery; the
    user can fill the cost later.
    """
    prices = costs or {}
    items: list[BazarItem] = []
    seen_names: set[str] = set()
    for group in groups:
        cost = prices.get(group.base_vnum)
        if cost is None:
            for vnum in group.vnums:
                if vnum in prices:
                    cost = prices[vnum]
                    break
        name = group.name[:80]
        if name in seen_names:
            name = f"{group.name[:70]} [{group.base_vnum}]"
        seen_names.add(name)
        items.append(
            BazarItem(
                name=name,
                search_packet=group.search_packet(),
                vnum=group.vnums[0],
                inv_tab=None,
                amount=5,
                min_price=0,
                nos_cost=cost,
                enabled=True,
            )
        )
    return items


def group_cost(
    group: NosMallGroup,
    costs: dict[int, float],
) -> float | None:
    """The ND cost for a group, by base vnum or any of its variants."""
    cost = costs.get(group.base_vnum)
    if cost is not None:
        return float(cost)
    for vnum in group.vnums:
        if vnum in costs:
            return float(costs[vnum])
    return None


def importable_groups(
    groups: list[NosMallGroup],
    costs: dict[int, float] | None = None,
) -> list[NosMallGroup]:
    """Groups the NosBazar importer may offer.

    Only items with a known ND cost can show a profit, so unpriced groups are
    left out entirely; pets, fairies and partner supplies are always skipped.
    """
    prices = costs or {}
    return [
        group
        for group in groups
        if not is_pet_group(group) and group_cost(group, prices) is not None
    ]


def is_importable_item(item, pet_vnums: set[int] | None = None) -> bool:
    """True when a configured Bazar item is ND-priced and not a pet."""
    cost = getattr(item, "nos_cost", None)
    if cost is None or cost <= 0:
        return False
    if is_pet_name(getattr(item, "name", "") or ""):
        return False
    if pet_vnums:
        vnums = list(getattr(item, "vnums", None) or [])
        vnum = getattr(item, "vnum", None)
        if vnum and vnum not in vnums:
            vnums.append(vnum)
        if pet_vnums.intersection(vnums):
            return False
    return True


def split_priced_non_pet(
    items: list,
    pet_vnums: set[int] | None = None,
) -> tuple[list, list]:
    """Split configured items into (keep, removed) for the cleanup action."""
    keep: list = []
    removed: list = []
    for item in items:
        if is_importable_item(item, pet_vnums):
            keep.append(item)
        else:
            removed.append(item)
    return keep, removed


def known_reference_costs() -> dict[int, float]:
    """The curated Nostale-NB ND costs (base/variant -> ND)."""
    return {
        2174: 0.5,
        1261: 0.5,
        9480: 0.5,
        10029: 0.5,
        1362: 1.0,
        5195: 1.0,
        9075: 1.0,
        2160: 0.5,
        10048: 0.5,
        5370: 2.5,
        9116: 2.5,
        13593: 2.5,
        13594: 2.5,
        907: 100.0,
        4240: 100.0,
        1286: 5.0,
        1452: 5.0,
        4717: 5.0,
        5884: 5.0,
        5885: 5.0,
        5886: 5.0,
        5887: 5.0,
        5996: 5.0,
        9041: 5.0,
        9380: 5.0,
        9874: 5.0,
        13710: 5.0,
        13731: 5.0,
        5931: 50.0,
        5932: 50.0,
        9109: 50.0,
        9110: 50.0,
        1765: 2.0,
        2159: 2.0,
        2313: 2.0,
        2390: 2.0,
        1296: 5.0,
        5194: 5.0,
        9074: 5.0,
        2158: 2.0,
        10024: 2.0,
        1904: 10.0,
        1218: 20.0,
        5369: 20.0,
        9458: 20.0,
        9459: 20.0,
        282: 50.0,
        498: 50.0,
        4262: 50.0,
        4264: 50.0,
        5735: 50.0,
        8541: 50.0,
        8543: 50.0,
        8544: 50.0,
        1364: 5.0,
        9464: 5.0,
        9498: 5.0,
        9925: 5.0,
        1363: 5.0,
        9463: 5.0,
        9497: 5.0,
        9924: 5.0,
        2173: 0.5,
        10028: 0.5,
        1156: 0.8,
        1428: 0.8,
    }


def match_text(text: str) -> str:
    """Normalize a name for matching catalog names to a price list."""
    return re.sub(r"[^0-9a-z]+", " ", text.casefold()).strip()
