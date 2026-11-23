"""NosBazar market helper: price monitor and listing maintenance.

This ports the two flows of the original ``Nostale-NB`` market bot into the
packet engine:

* **monitor** - search every configured item on the in-game NosBazar (player
  auction house), keep the cheapest listing and show its profitability against
  the NosMall ND cost or an NPC gold cost;
* **relist** - for an item in the inventory, undercut the cheapest other
  seller by one gold and register the listing again, never below the
  configured floor.

The module is deliberately free of GTK and networking.  It builds packets,
parses the server's ``rc_blist`` answers and runs a tick-driven state machine;
the GUI (or a CLI) supplies ``send``/``log`` callbacks and a
:class:`~ntpl.engine.WorldState`.

Packet shapes come from the live client and from ``Nostale-NB``:

    npc_req 2 <entityId>                     open the Bibi Basar dialog
    n_run <option> <page> 2 <entityId>       click the NosBazar option
    c_blist 0 0 0 0 0 0 0 0 0                initial listing (no filter)
    c_slist 0 0 0                            initial seller list
    c_blist 0 0 0 0 0 0 0 0 <n> <vnum...>    search, n vnums follow
    c_reg 0 <tab> <slot> <unk1> <unk2> <durability> 0 <amount> <price> <tax> <medal>

The server answers a search with ``rc_blist``.  Live entries have 15
``|``-separated fields: seller/owner name at 2, item vnum at 3, amount at 4
and per-unit price at 6.  Answers are grouped by the searched vnum order, so
this module uses the minimum visible price (and marks an answer truncated when
the DLL's 2048-byte slot cut it).

Rate limit: the game silently drops ``c_blist`` packets sent less than about
two seconds apart.  Searches are therefore paced at :data:`SEARCH_INTERVAL_S`
(2.5 s), and only one search is in flight at a time.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
    Sequence,
    TYPE_CHECKING,
)

if TYPE_CHECKING:  # pragma: no cover - only for type hints
    from .engine import WorldState

#: The Bibi Basar NPC template vnum (``in 2 793 <entityId> ...``).
BIBI_VNUM = 793
#: ``wopen`` type of the NosBazar window (``wopen 32 2 <page>``). A search
#: answer (``rc_blist``) is no proof that the window is open: the server
#: answers searches regardless, while it ignores a ``c_reg`` without it.
BAZAR_WINDOW_TYPE = 32
#: The portable **NosBazar medal** (Item.dat name "Gestore del NosBazar",
#: live item 1955). Using it from a Main/Etc bag opens the bazaar without a
#: nearby Bibi Basar: ``u_i 1 <charId> <bag> <slot> 0 0``. The controller
#: falls back to it when no Bibi entity is visible.
BAZAR_MEDAL_VNUM = 1955
#: The dialog id the server opens for Bibi Basar (live 2026-11-23:
#: ``[RECV] npc_req 2 10284 460``). It identifies the NPC before any option
#: is clicked, so a stale id that is another NPC (Carve Runes opened 11009)
#: never gets its dialog option pressed. A manual opening with a verified
#: Bibi entity can replace it.
BAZAR_DIALOG_ID = 460
#: Some servers open the bazaar with a bare client shortcut instead of an
#: NPC or the medal: FrosTale answers the client's own ``c_skill`` send
#: with ``wopen 32`` in ~0.2 s (11/11 openings in the 2026-11-23 live log,
#: four different maps). The controller replays the exact packet the client
#: sent once that pairing has been observed.
BAZAR_SKILL_PACKET = "c_skill"
#: A ``c_skill`` send this long before a ``wopen 32`` belongs to it.
BAZAR_SKILL_WINDOW_S = 2.0

#: The client drops faster searches; Nostale-NB uses 2.5 s, the relist loop 2 s.
SEARCH_INTERVAL_S = 2.5
#: No ``rc_blist`` within this window means the bazaar closed or the packet
#: was rate-limited.
RESPONSE_TIMEOUT_S = 5.0
#: Pacing of the four-packet open burst.
OPEN_STEP_S = 0.3
#: How long to wait for the initial ``rc_blist`` after the open burst.
OPEN_TIMEOUT_S = 3.6
#: Default dialog option/page for the NosBazar entry in Bibi Basar's dialog.
#: The live client's own ``n_run`` is learned when the user opens the bazaar
#: once; this is only the fallback from Nostale-NB.
DEFAULT_OPEN_OPTION = 60
DEFAULT_OPEN_PAGE = 0
#: Seconds between monitor passes.
DEFAULT_REFRESH_S = 30.0
#: NosBazar tax: 0.05 % of price * amount, truncated.
TAX_RATE = 0.0005
#: How long after the last answer the bazaar is assumed to still be open.
OPEN_FRESH_S = 90.0
#: A c_reg the server accepts is answered with ``rc_reg`` within about half
#: a second; a registration it ignores gets no packet at all.
REGISTER_ACK_TIMEOUT_S = 3.0

CONFIG_VERSION = 1
MAX_ITEMS = 5000

STATUS_TEXT = {
    "idle": "idle",
    "ok": "ok",
    "empty": "no listings",
    "timeout": "timeout",
    "bad": "bad response",
    "skipped": "skipped",
    "error": "error",
    "own": "your listing",
    "floor": "below floor",
    "register-pending": "register sent, waiting for rc_reg",
    "register-sent": "register accepted (rc_reg)",
    "register-no-ack": "register ignored (no rc_reg)",
    "register-unknown": "register sent (ack unknown)",
    "no-window": "bazaar window not open",
    "quantity": "not enough in inventory",
    "no-slot": "not in inventory",
    "no-vnum": "no item vnum",
    "not-selected": "not in relisting",
    "waiting": "waiting for Bibi Basar",
}


class BazarError(ValueError):
    """A malformed NosBazar packet or configuration."""


@dataclass(frozen=True)
class BazarListing:
    """One entry of an ``rc_blist`` answer.

    Live format (15 pipe-separated fields, captured 2026-11-19)::

        listingId|ownerId|ownerName|vnum|amount|isBundle|price|minutesLeft|
        unknown|unknown2|rarity|upgrade|unknown3|unknown4|itemData

    A multi-vnum search is answered grouped by searched vnum, price-sorted
    inside each group.  The first entry is therefore the cheapest listing of
    the **first searched vnum that has listings**, not a global cheapest.
    Nostale-NB uses that first entry; this client takes the minimum visible
    entry, which is safer for a group (and a truncated answer is flagged).
    """

    raw_fields: tuple[str, ...]
    price: int | None = None
    seller: str = ""
    vnum: int | None = None
    amount: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seller": self.seller,
            "price": self.price,
            "vnum": self.vnum,
            "amount": self.amount,
            "fields": list(self.raw_fields),
        }


@dataclass(frozen=True)
class BazarResponse:
    """A parsed ``rc_blist`` answer."""

    status: str
    listings: tuple[BazarListing, ...] = ()
    text: str = ""
    page: int | None = None
    #: The DLL's 2048-byte slot cut the answer before its end; the true
    #: cheapest listing may be missing (the visible minimum is still useful).
    truncated: bool = False

    @property
    def cheapest(self) -> BazarListing | None:
        """Minimum visible price (not simply the first entry)."""
        if not self.listings:
            return None
        priced = [listing for listing in self.listings if listing.price is not None]
        return min(priced, key=lambda listing: listing.price) if priced else None

    def cheapest_for(self, vnums: list[int] | tuple[int, ...]) -> BazarListing | None:
        """Cheapest visible listing whose vnum is in ``vnums``."""
        wanted = set(vnums)
        priced = [
            listing
            for listing in self.listings
            if listing.price is not None
            and listing.vnum is not None
            and listing.vnum in wanted
        ]
        return min(priced, key=lambda listing: listing.price) if priced else None


def parse_rc_blist(text: str) -> BazarResponse:
    """Parse one ``rc_blist`` line.

    ``Nostale-NB`` splits the packet into ``rc_blist``, one header field and
    the space-separated listings; the first listing is the cheapest and its
    pipe-separated field 6 is the price, field 2 the seller.  A line without
    listings is ``empty``; a listing with fewer than seven fields is ``bad``.
    Every parseable listing is returned, but callers normally use the first.
    """
    if not text.startswith("rc_blist"):
        raise BazarError(f"not an rc_blist packet: {text[:40]!r}")

    # Live lines can carry double spaces (the client's c_blist does); split on
    # any whitespace run and keep only the meaningful fields.
    parts = text.split(None, 2)
    if len(parts) < 3:
        return BazarResponse("empty", text=text)
    try:
        page: int | None = int(parts[1])
    except ValueError:
        page = None

    tokens = parts[2].split()
    if not tokens:
        return BazarResponse("empty", text=text, page=page)

    truncated = len(text) >= 2000  # DLL slot is 2048 bytes
    listings: list[BazarListing] = []
    for token in tokens:
        fields = tuple(token.split("|"))
        if len(fields) <= 6:
            # The DLL packet ring copies at most 2048 bytes, so a bazaar
            # answer with many listings ends in a truncated entry.  Keep
            # every intact entry and drop the tail.
            if listings:
                truncated = True
                break
            return BazarResponse("bad", text=text, page=page)
        try:
            price = int(fields[6])
        except ValueError:
            if listings:
                truncated = True
                break
            return BazarResponse("bad", text=text, page=page)
        vnum = None
        amount = None
        if len(fields) >= 5:
            try:
                vnum = int(fields[3])
                amount = int(fields[4])
            except ValueError:
                pass
        listings.append(
            BazarListing(
                raw_fields=fields,
                price=price,
                seller=fields[2],
                vnum=vnum,
                amount=amount,
            )
        )
    return BazarResponse(
        "ok", tuple(listings), text=text, page=page, truncated=truncated
    )


def build_search_packet(vnums: list[int] | tuple[int, ...]) -> str:
    """Build the ``c_blist`` search packet from template vnums."""
    values = [int(vnum) for vnum in vnums if int(vnum) > 0]
    return "c_blist 0 0 0 0 0 0 0 0 {} {}".format(
        len(values), " ".join(str(vnum) for vnum in values)
    ).strip()


def normalize_search_packet(packet: str) -> str:
    """Collapse whitespace and validate a captured ``c_blist`` packet."""
    text = " ".join(packet.split())
    if not text.startswith("c_blist "):
        raise BazarError("search packet must start with 'c_blist '")
    parts = text.split()
    if len(parts) < 10:
        raise BazarError("search packet is missing the count/vnum fields")
    try:
        count = int(parts[9])
    except ValueError as exc:
        raise BazarError("search packet has a non-numeric vnum count") from exc
    if count < 1:
        raise BazarError("search packet has no vnums")
    if len(parts) < 10 + count:
        raise BazarError("search packet has fewer vnums than its count")
    return text


def search_vnums(packet: str | None) -> list[int]:
    """The vnums inside a normalized ``c_blist`` packet."""
    if not packet:
        return []
    parts = packet.split()
    if len(parts) < 10 or parts[0] != "c_blist":
        return []
    try:
        count = int(parts[9])
        return [int(value) for value in parts[10 : 10 + count]]
    except ValueError:
        return []


def _name_key(text: str) -> str:
    """ASCII-only comparison key for owner names.

    Live seller names can contain replacement characters where the packet was
    not valid UTF-8 (``S�rox``), while the character name may come from the
    latin1 DLL log or the user (``Sérox``).  Dropping every non-ASCII and
    non-alphanumeric character makes the common accented forms compare equal;
    a false match only skips a relist, which is the safe side.
    """
    return "".join(
        char for char in text if char.isascii() and char.isalnum()
    ).casefold()


def calculate_tax(price: int, amount: int = 5) -> int:
    """NosBazar's sale tax, truncated to whole gold (Nostale-NB formula)."""
    return int(int(price) * max(1, int(amount)) * TAX_RATE)


def build_register_packet(
    item: "BazarItem",
    slot: int,
    price: int,
) -> str:
    """Build the ``c_reg`` packet that (re)lists one inventory slot."""
    if item.inv_tab not in (0, 1, 2):
        raise BazarError("inv_tab must be 0 (Equip), 1 (Main) or 2 (Etc)")
    if item.vnum is None or item.vnum <= 0:
        raise BazarError("registering needs an item vnum")
    if price <= 0:
        raise BazarError("price must be positive")
    amount = max(1, int(item.amount))
    tax = calculate_tax(price, amount)
    # The client's own c_reg carries one more trailing field (always 0):
    # every one of its 24 captured packets ends "<medal> 0" (logs 1512,
    # 1564, 2344, 2408). The server ignored every registration the host
    # sent without it in bag 2 (105 tries in log 1184, 901 in 2944, and the
    # Speech Bubble live 2026-11-23), while the same item, slot and
    # constants from the client's own UI were accepted. Send the client's
    # exact shape.
    return (
        f"c_reg 0 {item.inv_tab} {int(slot)} "
        f"{int(item.unk1)} {int(item.unk2)} {int(item.durability)} 0 "
        f"{amount} {int(price)} {tax} {int(item.medal)} 0"
    )


def build_open_packets(
    npc_id: int,
    option: int = DEFAULT_OPEN_OPTION,
    page: int = DEFAULT_OPEN_PAGE,
) -> list[str]:
    """The four-packet NPC sequence that opens the NosBazar window."""
    return [
        f"npc_req 2 {int(npc_id)}",
        f"n_run {int(option)} {int(page)} 2 {int(npc_id)}",
        "c_blist 0 0 0 0 0 0 0 0 0",
        "c_slist 0 0 0",
    ]


@dataclass
class BazarItem:
    """One configured item, mirroring the ``ITEMS`` entries of Nostale-NB."""

    name: str
    search_packet: str | None = None
    vnum: int | None = None
    inv_tab: int | None = None
    amount: int = 5
    min_price: int = 0
    nos_cost: float | None = None
    npc_cost: float | None = None
    unk1: int = 9
    unk2: int = 4
    durability: int = 1
    medal: int = 2
    #: Track this item at all: Monitor searches it. Off keeps the item in
    #: the configuration and its saved floor, but skips every pass.
    enabled: bool = True
    #: The relist set: only these items are searched by **Relist Start**
    #: and may change a listing. Monitor-only items keep a floor without
    #: ever sending `c_reg`. Configs written before this flag existed infer
    #: it from a positive ``min_price``.
    relist: bool = True

    @property
    def vnums(self) -> list[int]:
        return search_vnums(self.search_packet)

    def cost(self) -> tuple[float | None, str | None]:
        """Return ``(value, unit)``; NosMall ND wins over an NPC gold cost."""
        if self.nos_cost is not None and self.nos_cost > 0:
            return float(self.nos_cost), "ND"
        if self.npc_cost is not None and self.npc_cost > 0:
            return float(self.npc_cost), "g"
        return None, None

    def cost_text(self) -> str:
        value, unit = self.cost()
        if value is None:
            return "-"
        if unit == "ND":
            return f"{value:g} ND"
        return f"{value:g} g"

    def ratio(self, price: int | None) -> float | None:
        value, _unit = self.cost()
        if price is None or value is None or value <= 0:
            return None
        return price / value

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "search_packet": self.search_packet,
            "vnum": self.vnum,
            "inv_tab": self.inv_tab,
            "amount": self.amount,
            "min_price": self.min_price,
            "nos_cost": self.nos_cost,
            "npc_cost": self.npc_cost,
            "unk1": self.unk1,
            "unk2": self.unk2,
            "durability": self.durability,
            "medal": self.medal,
            "enabled": self.enabled,
            "relist": self.relist,
        }

    @classmethod
    def from_dict(cls, data: object) -> "BazarItem":
        if not isinstance(data, dict):
            raise BazarError("item is not an object")
        name = _as_text(data.get("name"), "", 80)
        if not name:
            raise BazarError("item has no name")
        search = data.get("search_packet")
        if search is not None and not isinstance(search, str):
            raise BazarError(f"{name}: search_packet is not text")
        search_packet = normalize_search_packet(search) if search else None
        tab_value = data.get("inv_tab")
        try:
            inv_tab = None if tab_value is None else int(tab_value)
        except (TypeError, ValueError) as exc:
            raise BazarError(f"{name}: inv_tab is not a number") from exc
        if inv_tab not in (None, 0, 1, 2):
            raise BazarError(f"{name}: inv_tab must be 0, 1, 2 or null")
        vnum_value = data.get("vnum")
        vnum = None if vnum_value is None else _as_int(vnum_value, 0, 0, 10**9)
        nos = _as_optional_float(data.get("nos_cost"))
        npc = _as_optional_float(data.get("npc_cost"))
        minimum = _as_int(data.get("min_price"), 0, 0, 10**10)
        return cls(
            name=name,
            search_packet=search_packet,
            vnum=vnum,
            inv_tab=inv_tab,
            amount=_as_int(data.get("amount"), 5, 1, 1000),
            min_price=minimum,
            nos_cost=nos,
            npc_cost=npc,
            unk1=_as_int(data.get("unk1"), 9, 0, 1000),
            unk2=_as_int(data.get("unk2"), 4, 0, 1000),
            durability=_as_int(data.get("durability"), 1, 0, 1000),
            medal=_as_int(data.get("medal"), 2, 0, 1000),
            enabled=_as_bool(data.get("enabled"), True),
            # Before the flag existed a floor was the only way to enable
            # relisting, so an old file keeps exactly that meaning.
            relist=_as_bool(data.get("relist"), minimum > 0),
        )


#: The two Monitor views. ``nosmall`` is the ND-cost dashboard (and the
#: original home of items whose cost is not known yet); ``npc`` is the
#: gold-cost dashboard used for reselling NPC-shop items.
MONITOR_SOURCES = ("nosmall", "npc")


def monitor_source(item: BazarItem) -> str:
    """Which Monitor view an item belongs to, from its effective cost unit.

    ``BazarItem.cost`` prefers a NosMall ND price, so an item with both a
    hand-edited ND cost and an NPC cost stays in the NosMall view; setting
    the editor's Cost type to NPC gold clears the ND value and moves it.
    """
    _value, unit = item.cost()
    return "npc" if unit == "g" else "nosmall"


# -- inventory planning ---------------------------------------------


def tracked_vnums(items: Iterable[BazarItem]) -> set[int]:
    """Every template vnum already covered by the configuration.

    ``c_blist`` searches positive template vnums, while an equip-bag slot
    carries a negative vnum (``inv 0 27.-4962...``), so both sources are
    compared by magnitude.
    """
    tracked: set[int] = set()
    for item in items:
        for vnum in item.vnums:
            tracked.add(abs(int(vnum)))
        if item.vnum is not None and item.vnum != 0:
            tracked.add(abs(int(item.vnum)))
    return tracked


@dataclass
class InventoryPlan:
    """The result of :func:`inventory_search_items`.

    ``tracked`` counts inventory vnums already configured (their slots are
    skipped); ``slots`` counts every slot looked at, including empty or
    invalid ones.
    """

    items: list[BazarItem] = field(default_factory=list)
    tracked: int = 0
    slots: int = 0


def _slot_int(slot: object, name: str, default: int = 0) -> int:
    try:
        return int(getattr(slot, name, default))
    except (TypeError, ValueError):
        return default


def _representative_slot(group: Sequence[Any]) -> Any:
    """The slot a later ``c_reg`` relist should reuse.

    A positive vnum in Main (bag 1) wins over Etc (2) and Equip (0), because
    ``c_reg`` accepts only those three tabs and needs a positive vnum.
    """
    for bag in (1, 2, 0):
        for slot in group:
            if (
                _slot_int(slot, "vnum") > 0
                and _slot_int(slot, "bag", -1) == bag
            ):
                return slot
    for slot in group:
        if _slot_int(slot, "vnum") > 0:
            return slot
    return group[0]


def _known_cost(
    mapping: Mapping[int, float] | None, vnum: int
) -> float | None:
    """A positive known cost from ``mapping``; anything else is unknown."""
    if not mapping:
        return None
    try:
        value = float(mapping.get(vnum))  # type: ignore[arg-type]
    except (AttributeError, TypeError, ValueError):
        return None
    return value if value > 0 else None


def _unique_name(name: str, vnum: int, taken: set[str]) -> str:
    """Fit ``name`` in 80 chars and avoid every name in ``taken``."""
    candidate = name[:80]
    if candidate not in taken:
        return candidate
    base = name[:72]
    candidate = f"{base} [{vnum}]"[:80]
    counter = 2
    while candidate in taken:
        candidate = f"{base[:64]} [{vnum}/{counter}]"[:80]
        counter += 1
    return candidate


def inventory_search_items(
    slots: Iterable[Any],
    names: Mapping[int, str],
    existing: Sequence[BazarItem],
    costs: Mapping[int, float] | None = None,
    npc_prices: Mapping[int, int] | None = None,
) -> InventoryPlan:
    """Plan one NosBazar search item per occupied inventory template.

    Every slot is grouped by ``abs(vnum)``, so the same item in several
    bags/slots becomes one search.  A grouped vnum already covered by
    ``existing`` (via a search packet or ``item.vnum``) is skipped and
    counted in :attr:`InventoryPlan.tracked`.

    The representative slot, which a later ``c_reg`` relist can use, is a
    positive-vnum slot in Main, then Etc, then Equip.  Its ``amount`` is the
    stack quantity clamped to 1..1000; a group with only a negative vnum
    (equip bag) gets amount 1 and no ``inv_tab``.  ``costs`` are known ND
    prices and ``npc_prices`` the Item.dat gold prices, used only when no ND
    cost is known.
    """
    plan = InventoryPlan()
    groups: dict[int, list[Any]] = {}
    for slot in slots:
        plan.slots += 1
        vnum = _slot_int(slot, "vnum")
        if vnum == 0 or abs(vnum) > 10**9:
            continue
        groups.setdefault(abs(vnum), []).append(slot)

    tracked = tracked_vnums(existing)
    taken = {item.name for item in existing}
    for vnum in sorted(groups):
        if vnum in tracked:
            plan.tracked += 1
            continue
        representative = _representative_slot(groups[vnum])
        slot_vnum = _slot_int(representative, "vnum")
        tab = _slot_int(representative, "bag", -1)
        inv_tab = tab if slot_vnum > 0 and tab in (0, 1, 2) else None
        if slot_vnum > 0:
            quantity = _slot_int(representative, "quantity")
            amount = min(1000, max(1, quantity) if quantity > 0 else 1)
        else:
            amount = 1
        name = (names.get(vnum) if names else None) or f"item {vnum}"
        nos_cost = _known_cost(costs, vnum)
        npc_cost = None if nos_cost is not None else _known_cost(
            npc_prices, vnum
        )
        item = BazarItem(
            name=_unique_name(str(name), vnum, taken),
            search_packet=build_search_packet([vnum]),
            vnum=vnum,
            inv_tab=inv_tab,
            amount=amount,
            min_price=0,
            nos_cost=nos_cost,
            npc_cost=npc_cost,
            enabled=True,
            # Inventory scan is a monitor-first action: the user chooses
            # which items join relisting in the Relist section.
            relist=False,
        )
        taken.add(item.name)
        plan.items.append(item)
    return plan


@dataclass
class BazarQuote:
    """The latest answer for one configured item."""

    item: BazarItem
    price: int | None = None
    seller: str = ""
    status: str = "idle"
    detail: str = ""
    at: float = 0.0
    listing: BazarListing | None = None

    @property
    def ratio(self) -> float | None:
        return self.item.ratio(self.price)

    def profit_text(self) -> str:
        value, unit = self.item.cost()
        if self.price is None or value is None:
            return "-"
        ratio = self.price / value
        if unit == "ND":
            return f"{int(ratio):,} g/ND"
        return f"{ratio:.1f}x"

    def status_text(self) -> str:
        text = STATUS_TEXT.get(self.status, self.status)
        if self.detail:
            text = f"{text}: {self.detail}"
        return text

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.item.name,
            "price": self.price,
            "seller": self.seller,
            "status": self.status,
            "detail": self.detail,
            "profit": self.profit_text(),
            "listing": self.listing.to_dict() if self.listing else None,
        }


@dataclass
class BazarState:
    """Live bazaar-related state filled by the engine from packets."""

    #: True after any ``rc_blist``; cleared on a map change and on a total
    #: timeout pass.
    open: bool = False
    #: Monotonic counter and raw text of the newest ``rc_blist``.
    response_count: int = 0
    last_response: str = ""
    #: Monotonic receipt time of the newest response (controllers run on
    #: ``time.monotonic()``; packet timestamps are wall time).
    last_response_at: float = 0.0
    #: Newest responses, oldest first, so a controller tick that sees several
    #: answers can pick the one matching its in-flight search instead of
    #: losing it when two packets arrive between ticks.
    responses: deque[tuple[int, str]] = field(
        default_factory=lambda: deque(maxlen=8)
    )
    #: ``rc_reg <value>`` is the server's c_reg acknowledgement (1 observed
    #: for every accepted registration). A c_reg it ignores gets no packet
    #: at all, which is how a wrong item-constant pair is detected. The
    #: monotonic counter lets a controller match its own send.
    register_count: int = 0
    register_value: int = 0
    register_at: float = 0.0
    #: ``wopen <type> ...``: the last window the server opened. Type 32 is
    #: the NosBazar window. ``open`` only says a search was answered.
    window_type: int | None = None
    window_count: int = 0
    window_at: float = 0.0
    window_open: bool = False
    #: The Bibi Basar entity id and the dialog option/page that opened it,
    #: learned from the client's own packets when the user opens the bazaar.
    npc_id: int | None = None
    open_option: int = DEFAULT_OPEN_OPTION
    open_page: int = DEFAULT_OPEN_PAGE
    #: True once a real ``n_run`` was paired with the bazaar opening; a
    #: configured option then loses to the learned one.
    option_learned: bool = False
    #: Last NPC the client talked to (``npc_req``), and the last dialog
    #: option it clicked (``n_run``), with their timestamps.
    dialog_npc_id: int | None = None
    dialog_at: float = 0.0
    #: Dialog id from the last ``[RECV] npc_req`` reply (460 for Bibi).
    dialog_id: int | None = None
    #: Expected Bibi dialog id; a different dialog means the id belongs to
    #: another NPC and its option must not be clicked.
    bazaar_dialog_id: int | None = BAZAR_DIALOG_ID
    npc_req_id: int | None = None
    npc_req_at: float = 0.0
    n_run: tuple[int, int, int] | None = None
    n_run_at: float = 0.0
    #: The client opened the bazaar with its own shortcut: ``[SEND] c_skill``
    #: answered by ``wopen 32`` within ``BAZAR_SKILL_WINDOW_S`` (FrosTale).
    #: Learned from the client's traffic or its log; replaying the exact
    #: packet needs neither a visible Bibi nor a medal item.
    open_skill: bool = False
    #: Timestamp of the newest client ``c_skill`` send, for the pairing.
    open_skill_at: float = 0.0

    def responses_since(self, count: int) -> list[tuple[int, str]]:
        """Responses newer than ``count``, oldest first."""
        return [
            (number, text)
            for number, text in self.responses
            if number > count
        ]


@dataclass
class BazarConfig:
    """Serialisable NosBazar configuration (one per game client/window)."""

    version: int = CONFIG_VERSION
    character: str = ""
    refresh: float = DEFAULT_REFRESH_S
    npc_id: int | None = None
    open_option: int = DEFAULT_OPEN_OPTION
    open_page: int = DEFAULT_OPEN_PAGE
    items: list[BazarItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": CONFIG_VERSION,
            "character": self.character,
            "refresh": self.refresh,
            "npc_id": self.npc_id,
            "open_option": self.open_option,
            "open_page": self.open_page,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: object) -> "BazarConfig":
        if not isinstance(data, dict):
            raise BazarError("configuration is not an object")
        raw_items = data.get("items", [])
        if not isinstance(raw_items, list):
            raise BazarError("items is not a list")
        if len(raw_items) > MAX_ITEMS:
            raise BazarError(f"too many items (max {MAX_ITEMS})")
        items: list[BazarItem] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_items):
            try:
                item = BazarItem.from_dict(raw)
            except BazarError as exc:
                raise BazarError(f"item {index + 1}: {exc}") from exc
            if item.name in seen:
                raise BazarError(f"duplicate item name: {item.name}")
            seen.add(item.name)
            items.append(item)
        npc_value = data.get("npc_id")
        npc_id = None if npc_value is None else _as_int(npc_value, 0, 1, 10**9)
        return cls(
            character=_as_text(data.get("character"), "", 40),
            refresh=_as_float(data.get("refresh"), DEFAULT_REFRESH_S, 5.0, 3600.0),
            npc_id=npc_id,
            open_option=_as_int(
                data.get("open_option"), DEFAULT_OPEN_OPTION, 0, 10**6
            ),
            open_page=_as_int(data.get("open_page"), DEFAULT_OPEN_PAGE, 0, 100),
            items=items,
        )


def save_config(path: str | Path, config: BazarConfig) -> Path:
    """Write the configuration atomically and return the target path."""
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BazarError(f"cannot create {target.parent}: {exc}") from exc

    payload = json.dumps(config.to_dict(), indent=2) + "\n"
    try:
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=f".{target.name}.",
            delete=False,
        )
    except OSError as exc:
        raise BazarError(f"cannot write {target}: {exc}") from exc
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise BazarError(f"cannot write {target}: {exc}") from exc
    return target


def load_config(path: str | Path) -> BazarConfig:
    """Read a configuration written by :func:`save_config`."""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise BazarError(f"cannot read {source}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BazarError(f"{source} is not valid JSON: {exc}") from exc
    return BazarConfig.from_dict(data)


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def _as_int(value: object, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _as_float(value: object, default: float, low: float, high: float) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if parsed != parsed:  # NaN
        return default
    return max(low, min(high, parsed))


def _as_optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _as_text(value: object, default: str, limit: int) -> str:
    if not isinstance(value, str):
        return default
    text = value.strip()
    return text[:limit] if text else default


class BazarController:
    """Tick-driven monitor/relist state machine.

    ``send`` pushes one game packet and returns whether it was queued; ``log``
    receives one human-readable line per event; ``item_label`` optionally maps
    a configured item to the name used in those lines (the GUI passes the real
    Item.dat name).  :meth:`tick` must be called regularly (about ten times a
    second) with the current ``WorldState``.
    """

    def __init__(
        self,
        send: Callable[[str], bool],
        log: Callable[[str], None] | None = None,
        clock: Callable[[], float] | None = None,
        item_label: Callable[[BazarItem], str] | None = None,
    ) -> None:
        self._send = send
        self._log = log or (lambda _text: None)
        self._clock = clock or time.monotonic
        #: User-facing name for log lines. The GUI resolves the real Item.dat
        #: name from the item's vnums; ``BazarItem.name`` stays the config
        #: key, result-dict key and editor label.
        self._item_label = item_label or (lambda item: item.name)

        self.config = BazarConfig()
        self.results: dict[str, BazarQuote] = {}
        self.mode: str | None = None
        self.phase = "idle"
        self.version = 0
        self.last_error = ""

        self._queue: list[BazarItem] = []
        self._pending: dict[str, Any] | None = None
        self._next_send = 0.0
        self._retry_done = False
        self._pass_number = 0
        #: Monotonic start of the current pass; only quotes written at/after
        #: it count toward this pass, while older rows stay on screen.
        self._pass_started = 0.0
        self._open_npc: int | None = None
        self._open_seen = 0
        self._open_window_seen = 0
        #: True while the open burst waits for the portable NosBazar medal,
        #: which replaces npc_req/n_run when no Bibi Basar is visible.
        self._open_medal = False
        #: True while the open waits for the client's own shortcut
        #: (``c_skill`` on FrosTale), the learned opening with no NPC.
        self._open_skill = False
        #: True once the npc_req reply carried the Bibi dialog id; the burst
        #: may then click its option and accept an already-open window.
        self._open_dialog_ok = False
        self._open_step = 0
        self._open_next = 0.0
        self._open_deadline = 0.0
        self._wait_until = 0.0
        self._pass_done_at = 0.0
        self._last_pass_summary = ""
        #: The c_reg waiting for ``rc_reg``: item, quote, the ack counter at
        #: send time, the price and the deadline.
        self._pending_registration: dict[str, Any] | None = None

    # -- configuration --------------------------------------------------

    def set_items(
        self,
        items: list[BazarItem],
        *,
        character: str = "",
        refresh: float | None = None,
    ) -> None:
        # An in-flight pass holds the old item objects; stop rather than let
        # a pending search resolve against a replaced list.
        if self.mode is not None:
            self.stop("items changed")
        self.config.items = list(items)
        self.config.character = character.strip()
        if refresh is not None:
            self.config.refresh = max(5.0, min(3600.0, float(refresh)))
        self.results = {
            item.name: BazarQuote(item=item) for item in self.config.items
        }
        self.version += 1

    def enabled_items(self) -> list[BazarItem]:
        return [
            item
            for item in self.config.items
            if item.enabled and item.search_packet
        ]

    def relist_items(self) -> list[BazarItem]:
        """The relist set: enabled items the user marked for relisting.

        A relist pass searches only these, so an item followed for price
        discovery never gets its listing touched even when it carries a
        ``min_price`` floor. Readiness (vnum, tab, floor) is still checked
        item by item in :meth:`_maybe_relist`.
        """
        return [
            item
            for item in self.config.items
            if item.enabled and item.relist and item.search_packet
        ]

    # -- lifecycle ------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.mode is not None

    def start_monitor(
        self, world: "WorldState", refresh: float | None = None
    ) -> bool:
        return self._start(world, "monitor", refresh)

    def open_bazaar(self, world: "WorldState") -> bool:
        """Only replay the NPC interaction and stop once the window opens."""
        return self._start(world, "open", None)

    def start_relist(
        self, world: "WorldState", refresh: float | None = None
    ) -> bool:
        if not self.config.character:
            self.last_error = (
                "relist needs your character name (to avoid undercutting "
                "your own listing)"
            )
            self._log(self.last_error)
            return False
        return self._start(world, "relist", refresh)

    def _start(
        self, world: "WorldState", mode: str, refresh: float | None
    ) -> bool:
        if refresh is not None:
            self.config.refresh = max(5.0, min(3600.0, float(refresh)))
        self.mode = mode
        self.last_error = ""
        # Keep the last pass on screen; it is updated item by item.
        self._sync_results()
        # A relist pass must search only the chosen relist set; Monitor
        # searches every enabled item. `open` reuses the monitor queue.
        self._queue = (
            self.relist_items() if mode == "relist" else self.enabled_items()
        )
        self._pending = None
        self._retry_done = False
        self._pass_number = 0
        self._pass_done_at = 0.0
        self._last_pass_summary = ""
        self.phase = "open"
        self._begin_open(world, self._clock())
        self.version += 1
        if self.phase == "idle":
            return False
        if mode == "open":
            self._log("opening the NosBazar window")
        else:
            self._log(
                f"NosBazar {mode} started ({len(self._queue)} item(s), "
                f"refresh {self.config.refresh:g}s)"
            )
        return True

    def stop(self, reason: str = "") -> None:
        was_running = self.mode is not None
        self.mode = None
        self.phase = "idle"
        self._pending = None
        self._queue = []
        self._wait_until = 0.0
        pending = self._pending_registration
        if pending is not None:
            # No tick will resolve it after a stop; do not leave the row
            # claiming it is still waiting for the server.
            quote: BazarQuote = pending["quote"]
            quote.status = "register-unknown"
            quote.detail = "stopped before rc_reg"
            self._pending_registration = None
        self.version += 1
        if was_running:
            self._log(f"NosBazar stopped{f': {reason}' if reason else ''}")

    def _registration_tick(
        self, world: "WorldState", now: float
    ) -> None:
        """Resolve the in-flight c_reg from the server's ``rc_reg``."""
        pending = self._pending_registration
        if pending is None:
            return
        state = world.bazar
        if state.register_count > pending["seen"]:
            quote: BazarQuote = pending["quote"]
            quote.status = "register-sent"
            quote.detail = (
                f"rc_reg {state.register_value} · {pending['price']:,}"
            )
            self._log(
                f"{self._item_label(pending['item'])}: c_reg accepted "
                f"(rc_reg {state.register_value})"
            )
            self._pending_registration = None
            self.version += 1
            return
        if now >= pending["deadline"]:
            quote = pending["quote"]
            quote.status = "register-no-ack"
            quote.detail = f"{pending['price']:,} ignored (no rc_reg)"
            self._log(
                f"{self._item_label(pending['item'])}: c_reg ignored by the "
                "server (no rc_reg); the listed slot or its item constants "
                "may not match the live inventory"
            )
            self._pending_registration = None
            self.version += 1

    # -- tick -----------------------------------------------------------

    def tick(self, world: "WorldState", now: float | None = None) -> bool:
        """Advance the state machine; return True when the display changed."""
        if self.mode is None and self._pending_registration is None:
            return False
        moment = self._clock() if now is None else now
        before = (self.phase, self.version)
        # A c_reg answer can arrive in any phase, including the cooldown.
        self._registration_tick(world, moment)
        if self.mode is None:
            return (self.phase, self.version) != before
        if self.phase == "open":
            self._open_tick(world, moment)
        elif self.phase == "waiting":
            if (
                world.bazar.open
                and world.bazar.window_open
                and moment - world.bazar.last_response_at <= OPEN_FRESH_S
            ):
                # The user opened the bazaar by hand while we were waiting.
                self._begin_pass(world, moment)
            elif moment >= self._wait_until:
                self._begin_open(world, moment)
        elif self.phase == "pass":
            self._pass_tick(world, moment)
        elif self.phase == "cooldown":
            if moment >= self._pass_done_at:
                self._pass_done_at = 0.0
                if self.mode is not None:
                    self._begin_pass(world, moment)
        return (self.phase, self.version) != before

    # -- opening --------------------------------------------------------

    def _bibi_candidates(
        self, world: "WorldState", verified_only: bool = False
    ) -> list[int]:
        candidates: list[int] = []
        # A live Bibi entity wins: after a map change the configured/learned
        # id belongs to the previous map, while `in 2 793 <id>` names the one
        # this map actually has.
        nearest: tuple[float, int] | None = None
        position = world.position
        for entity in world.entities.values():
            if getattr(entity, "vnum", 0) != BIBI_VNUM:
                continue
            if position is None:
                distance = 0.0
            else:
                distance = abs(entity.x - position[0]) + abs(entity.y - position[1])
            if nearest is None or distance < nearest[0]:
                nearest = (distance, entity.entity_id)
        if nearest is not None:
            candidates.append(nearest[1])
        if verified_only:
            return candidates
        state = world.bazar
        # Only an id that is currently a Bibi Basar entity may be used. A
        # learned or configured id belongs to an entity instance; on another
        # map the same id is a different NPC (live 2026-11-23: a stale id
        # opened Carve Runes instead of the bazaar).
        for value in (state.npc_id, self.config.npc_id):
            if value is None or value in candidates:
                continue
            entity = world.entities.get(value)
            if entity is not None and getattr(entity, "vnum", 0) != BIBI_VNUM:
                # A known non-Bibi entity: never talk to it. An id missing
                # from the entity list is still allowed, because an NPC that
                # spawned before the host attached may not be seeded.
                continue
            candidates.append(value)
        return candidates

    @staticmethod
    def _medal_slot(world: "WorldState") -> tuple[int, int] | None:
        """``(bag, slot)`` of the portable NosBazar medal, Main/Etc first."""
        if world.self_id is None:
            return None
        for bag in (1, 2, 0):
            slot = world.find_inventory(bag, BAZAR_MEDAL_VNUM)
            if slot is not None:
                return bag, slot.index
        return None

    def _begin_open(self, world: "WorldState", now: float) -> None:
        state = world.bazar
        if (
            state.open
            and state.window_open
            and now - state.last_response_at <= OPEN_FRESH_S
        ):
            self._begin_pass(world, now)
            return

        # The client's own shortcut, when it has one: on FrosTale a bare
        # `c_skill` is answered by `wopen 32` (live 2026-11-23, the reported
        # "no Bibi, no medal" case). It needs neither an NPC nor an item, so
        # it is the first choice once the pairing has been observed.
        if state.open_skill:
            self._open_skill = True
            self._open_medal = False
            self._open_dialog_ok = False
            self._open_seen = state.response_count
            self._open_window_seen = state.window_count
            self._open_step = 0
            self._open_next = now
            self._open_deadline = now + OPEN_TIMEOUT_S
            self.phase = "open"
            self._send(BAZAR_SKILL_PACKET)
            self._log(
                "opening NosBazar with the client's own shortcut "
                f"({BAZAR_SKILL_PACKET})"
            )
            return

        # A visible Bibi Basar is the normal path. Without one, use the
        # portable NosBazar medal from the inventory before ever replaying an
        # unverified stale NPC id.
        candidates = self._bibi_candidates(world, verified_only=True)
        medal = self._medal_slot(world)
        if not candidates and medal is not None:
            bag, slot = medal
            self._open_skill = False
            self._open_medal = True
            self._open_seen = state.response_count
            self._open_window_seen = state.window_count
            self._open_step = 0
            self._open_next = now
            self._open_deadline = now + OPEN_TIMEOUT_S
            self.phase = "open"
            self._send(
                f"u_i 1 {world.self_id} {bag} {slot} 0 0"
            )
            self._log(
                "Bibi Basar is not visible; using the NosBazar medal "
                f"(vnum {BAZAR_MEDAL_VNUM}, bag {bag}, slot {slot})"
            )
            return
        if not candidates:
            candidates = self._bibi_candidates(world)
        if not candidates:
            self.last_error = (
                "Bibi Basar is not visible and no NosBazar medal "
                f"(vnum {BAZAR_MEDAL_VNUM}) is in the inventory; walk next "
                "to Bibi or buy/keep a medal, then start again"
            )
            self._log(self.last_error)
            if self.mode == "open":
                self.mode = None
                self.phase = "idle"
                self.version += 1
                return
            self.phase = "waiting"
            self._wait_until = now + self.config.refresh
            return

        self._open_skill = False
        self._open_medal = False
        self._open_dialog_ok = False
        self._open_npc = candidates[0]
        self._open_seen = state.response_count
        self._open_window_seen = state.window_count
        self._open_step = 0
        self._open_next = now
        self._open_deadline = now + OPEN_TIMEOUT_S
        self.phase = "open"

    def _open_tick(self, world: "WorldState", now: float) -> None:
        state = world.bazar
        if self._open_skill:
            # The client's own shortcut (`c_skill` on FrosTale) opens the
            # window itself; a fresh wopen 32 is the whole confirmation.
            if state.window_count > self._open_window_seen:
                state.open = True
                self._log("NosBazar window is open (client shortcut)")
                if self.mode == "open":
                    self.mode = None
                    self.phase = "idle"
                    self.version += 1
                    return
                self._begin_pass(world, now)
                return
            if now >= self._open_deadline:
                state.open = False
                self.last_error = (
                    "could not open NosBazar with the client shortcut "
                    f"({BAZAR_SKILL_PACKET}); retry in "
                    f"{self.config.refresh:g}s"
                )
                self._log(self.last_error)
                if self.mode == "open":
                    self.mode = None
                    self.phase = "idle"
                    self.version += 1
                    return
                self.phase = "waiting"
                self._wait_until = now + self.config.refresh
                return
            self.version += 1
            return
        if self._open_medal:
            # The medal's own use opens the window; npc_req/n_run would be
            # wrong here. A fresh wopen 32 is the whole confirmation.
            if state.window_count > self._open_window_seen:
                state.open = True
                self._log("NosBazar window is open (medal)")
                if self.mode == "open":
                    self.mode = None
                    self.phase = "idle"
                    self.version += 1
                    return
                self._begin_pass(world, now)
                return
            if now >= self._open_deadline:
                state.open = False
                self.last_error = (
                    "could not open NosBazar with the medal; keep a "
                    f"vnum {BAZAR_MEDAL_VNUM} medal in the inventory and "
                    f"retry in {self.config.refresh:g}s"
                )
                self._log(self.last_error)
                if self.mode == "open":
                    self.mode = None
                    self.phase = "idle"
                    self.version += 1
                    return
                self.phase = "waiting"
                self._wait_until = now + self.config.refresh
                return
            self.version += 1
            return
        fresh_window = state.window_count > self._open_window_seen
        answered = state.response_count > self._open_seen
        # The full burst (npc_req, n_run, initial c_blist/c_slist) must have
        # been sent: a wopen 32 often arrives right after n_run, before the
        # initial listing request, and accepting then would skip it.
        if self._open_step >= 3 and (
            fresh_window or (self._open_dialog_ok and answered)
        ):
            # A fresh wopen 32 is the normal proof. When the bazaar window
            # was opened before this host attached, the server sends no
            # wopen again (live 2026-11-23, the reported "already open"
            # case); the verified Bibi dialog id plus the listing answer
            # prove the window exists, so continue the scan.
            if not fresh_window:
                state.window_open = True
            state.open = True
            self._log(
                "NosBazar window is open"
                + ("" if fresh_window else " (already open)")
            )
            if self.mode == "open":
                self.mode = None
                self.phase = "idle"
                self.version += 1
                return
            self._begin_pass(world, now)
            return
        if now >= self._open_deadline:
            state.open = False
            self.last_error = (
                f"could not open NosBazar (NPC {self._open_npc}); move next "
                f"to Bibi Basar and retry in {self.config.refresh:g}s"
            )
            self._log(self.last_error)
            if self.mode == "open":
                self.mode = None
                self.phase = "idle"
                self.version += 1
                return
            self.phase = "waiting"
            self._wait_until = now + self.config.refresh
            return
        if self._open_step == 1 and not self._open_dialog_ok:
            # The npc_req reply identifies the NPC before any option is
            # clicked: Bibi opens dialog 460, Carve Runes opened 11009
            # (live 2026-11-23). Never click another NPC's option.
            if state.dialog_npc_id != self._open_npc:
                return
            expected = state.bazaar_dialog_id
            if expected is not None and state.dialog_id != expected:
                state.open = False
                self.last_error = (
                    f"NPC {self._open_npc} opened dialog "
                    f"{state.dialog_id}, not the bazaar ({expected}); move "
                    "next to Bibi Basar"
                )
                self._log(self.last_error)
                if self.mode == "open":
                    self.mode = None
                    self.phase = "idle"
                    self.version += 1
                    return
                self.phase = "waiting"
                self._wait_until = now + self.config.refresh
                return
            self._open_dialog_ok = True
            self.version += 1
        if now < self._open_next:
            return

        option = (
            state.open_option
            if state.option_learned
            else self.config.open_option
        )
        page = (
            state.open_page
            if state.option_learned
            else self.config.open_page
        )
        packets = build_open_packets(self._open_npc or 0, option, page)
        if self._open_step == 0:
            self._send(packets[0])
            self._open_next = now + OPEN_STEP_S
        elif self._open_step == 1:
            self._send(packets[1])
            self._open_next = now + OPEN_STEP_S
        elif self._open_step == 2:
            self._send(packets[2])
            self._send(packets[3])
            self._open_next = now + OPEN_TIMEOUT_S
        else:  # pragma: no cover - guarded by the deadline above
            self._open_next = now + OPEN_STEP_S
        self._open_step += 1
        self.version += 1

    # -- searching ------------------------------------------------------

    def _begin_pass(self, world: "WorldState", now: float) -> None:
        self.phase = "pass"
        self._queue = (
            self.relist_items()
            if self.mode == "relist"
            else self.enabled_items()
        )
        self._pending = None
        self._next_send = now
        self._retry_done = False
        self._pass_number += 1
        self._pass_started = now
        # Do not clear the table at the start of a cycle: the previous prices
        # remain visible and each row is replaced when its answer arrives.
        self._sync_results()
        self.version += 1
        if not self._queue:
            self.last_error = (
                "no item in the relist set with a search packet"
                if self.mode == "relist"
                else "no enabled item with a search packet"
            )
            self._log(self.last_error)
            self.mode = None
            self.phase = "idle"
            self.version += 1

    def _pass_tick(self, world: "WorldState", now: float) -> None:
        if self._pending is not None:
            self._pending_tick(world, now)
            return
        if not self._queue:
            # The last answer already arrived; do not wait out the pacing
            # interval before showing the pass summary.
            self._finish_pass(world, now)
            return
        if now < self._next_send:
            return

        item = self._queue.pop(0)
        if not item.search_packet:
            self._record(item, BazarQuote(item=item, status="skipped"))
            self.version += 1
            self._next_send = now
            return
        if not self._send(item.search_packet):
            self._record(
                item,
                self._failure_quote(item, "error", "send failed", now),
            )
            self.last_error = "send failed"
            self._next_send = now + SEARCH_INTERVAL_S
            self.version += 1
            return
        self._pending = {
            "item": item,
            "seen": world.bazar.response_count,
            "deadline": now + RESPONSE_TIMEOUT_S,
        }
        self._next_send = now + SEARCH_INTERVAL_S

    def _pending_tick(self, world: "WorldState", now: float) -> None:
        pending = self._pending
        if pending is None:  # pragma: no cover - defensive
            return
        item: BazarItem = pending["item"]
        events = world.bazar.responses_since(pending["seen"])
        if events:
            wanted = set(item.vnums)
            for count, raw in events:
                response = parse_rc_blist(raw)
                seen_vnums = {
                    listing.vnum
                    for listing in response.listings
                    if listing.vnum is not None
                }
                if wanted and seen_vnums and not (seen_vnums & wanted):
                    # Another search (the user browsing the bazaar, or a
                    # stale answer). Keep looking at the other events.
                    continue
                pending["seen"] = count
                if response.status == "ok" and response.cheapest is not None:
                    listing = response.cheapest
                    quote = BazarQuote(
                        item=item,
                        price=listing.price,
                        seller=listing.seller,
                        status="ok",
                        detail="truncated answer" if response.truncated else "",
                        at=now,
                        listing=listing,
                    )
                elif response.status == "bad":
                    quote = self._failure_quote(
                        item,
                        "bad",
                        "truncated answer" if response.truncated else "",
                        now,
                    )
                else:
                    quote = BazarQuote(
                        item=item,
                        status=response.status,
                        detail=(
                            "truncated answer" if response.truncated else ""
                        ),
                        at=now,
                        listing=None,
                    )
                self._record(item, quote)
                self._pending = None
                if self.mode == "relist" and quote.status == "ok":
                    self._maybe_relist(world, item, quote, now)
                self.version += 1
                return
            # Every event was foreign; mark them consumed.
            pending["seen"] = world.bazar.response_count
            return
        if now >= pending["deadline"]:
            self._record(
                item, self._failure_quote(item, "timeout", "", now)
            )
            self._pending = None
            self.version += 1

    def _record(self, item: BazarItem, quote: BazarQuote) -> None:
        self.results[item.name] = quote

    def _sync_results(self) -> None:
        """Drop removed items and add new ones without clearing old prices."""
        known = {item.name: item for item in self.config.items}
        self.results = {
            name: quote
            for name, quote in self.results.items()
            if name in known
        }
        for name, item in known.items():
            if name not in self.results:
                self.results[name] = BazarQuote(item=item)

    def _failure_quote(
        self, item: BazarItem, status: str, detail: str, now: float
    ) -> BazarQuote:
        """A failed update keeps the last price and is marked stale."""
        previous = self.results.get(item.name)
        if previous is not None and previous.price is not None:
            return BazarQuote(
                item=item,
                price=previous.price,
                seller=previous.seller,
                status=status,
                detail=detail or "stale price",
                at=now,
                listing=previous.listing,
            )
        return BazarQuote(item=item, status=status, detail=detail, at=now)

    def _finish_pass(self, world: "WorldState", now: float) -> None:
        self._pending = None
        quotes = [self.results.get(item.name) for item in self.config.items]
        queried = [
            quote
            for quote in quotes
            if quote is not None
            and quote.status not in ("idle", "skipped")
            and quote.at >= self._pass_started
        ]
        timed_out = [
            quote.item.name
            for quote in queried
            if quote is not None and quote.status == "timeout"
        ]
        if not self._retry_done and timed_out:
            self._retry_done = True
            self._queue = [
                item for item in self.config.items if item.name in timed_out
            ]
            self._next_send = now + 2.0
            self._log(
                f"retrying {len(self._queue)} timed-out item(s)"
            )
            self.version += 1
            return

        all_failed = bool(queried) and all(
            quote is not None and quote.status == "timeout" for quote in queried
        )
        answered = sum(
            1 for quote in queried if quote is not None and quote.status != "timeout"
        )
        summary = (
            f"{answered}/{len(queried)} answered"
            if queried
            else "no items configured"
        )
        if timed_out:
            summary += f", {len(timed_out)} timed out"
        self._last_pass_summary = summary
        self.version += 1

        if all_failed:
            world.bazar.open = False
            self.phase = "waiting"
            self._wait_until = now + self.config.refresh
            self.last_error = (
                "all searches timed out; the bazaar window probably closed "
                "or the character left Bibi Basar"
            )
            self._log(self.last_error)
            return

        self.phase = "cooldown"
        self._pass_done_at = now + self.config.refresh
        self._log(f"NosBazar pass done: {summary}")

    # -- relisting ------------------------------------------------------

    def _maybe_relist(
        self,
        world: "WorldState",
        item: BazarItem,
        quote: BazarQuote,
        now: float,
    ) -> None:
        if not item.relist:
            # Items can be unticked while a pass is in flight; the tick is
            # the user's decision, so never touch their listing.
            quote.status = "not-selected"
            self._log(
                f"{self._item_label(item)}: not relisted "
                "(not in the relist list)"
            )
            return
        # Resolve a previous registration first; never chain a second c_reg
        # while the server's answer to the first one is still unknown.
        self._registration_tick(world, now)
        if self._pending_registration is not None:
            quote.status = "error"
            quote.detail = "previous c_reg still pending"
            self._log(
                f"{self._item_label(item)}: not relisted "
                "(previous c_reg still pending)"
            )
            return
        if not world.bazar.window_open:
            # The server ignores c_reg without the NosBazar window; a search
            # answer does not prove it is open (live 2026-11-23).
            quote.status = "no-window"
            quote.detail = "the NosBazar window is not open"
            self._log(
                f"{self._item_label(item)}: not relisted "
                "(the NosBazar window is not open)"
            )
            return
        if item.vnum is None or item.vnum <= 0:
            quote.status = "no-vnum"
            self._log(
                f"{self._item_label(item)}: not relisted "
                "(no item vnum configured)"
            )
            return
        if item.inv_tab not in (0, 1, 2):
            quote.status = "no-slot"
            quote.detail = "no inventory tab"
            self._log(
                f"{self._item_label(item)}: not relisted (no inventory tab)"
            )
            return
        if item.min_price <= 0:
            quote.status = "floor"
            quote.detail = "min_price not set"
            self._log(
                f"{self._item_label(item)}: not relisted "
                "(set a min_price floor first)"
            )
            return
        if quote.price is None:
            return
        if (
            item.vnums
            and quote.listing is not None
            and quote.listing.vnum is not None
            and quote.listing.vnum not in item.vnums
        ):
            quote.status = "error"
            quote.detail = (
                f"vnum mismatch: listing {quote.listing.vnum}, searched "
                f"{'/'.join(str(vnum) for vnum in item.vnums)}"
            )
            self._log(f"{self._item_label(item)}: not relisted ({quote.detail})")
            return
        own_name = (
            world.self_name or self.config.character or ""
        ).strip()
        own_key = _name_key(own_name)
        seller_key = _name_key(quote.seller)
        if own_name and (
            quote.seller.strip() == own_name
            or (own_key and own_key == seller_key)
        ):
            quote.status = "own"
            self._log(
                f"{self._item_label(item)}: you are already the cheapest "
                "seller"
            )
            return
        if quote.price <= item.min_price:
            quote.status = "floor"
            self._log(
                f"{self._item_label(item)}: cheapest {quote.price:,} is at/below the "
                f"{item.min_price:,} floor"
            )
            return

        slot = world.find_inventory(item.inv_tab, item.vnum)
        if slot is None:
            quote.status = "no-slot"
            quote.detail = f"vnum {item.vnum} not in tab {item.inv_tab}"
            self._log(f"{self._item_label(item)}: not relisted ({quote.detail})")
            return
        # Equipment rows report quantity 0 but hold one item; `amount`
        # normalises that, so an equip slot can be relisted with amount 1.
        available = max(1, slot.amount)
        if available < item.amount:
            quote.status = "quantity"
            quote.detail = (
                f"inventory has {available}, amount is {item.amount}"
            )
            self._log(f"{self._item_label(item)}: not relisted ({quote.detail})")
            return

        new_price = quote.price - 1
        if new_price < item.min_price:
            quote.status = "floor"
            self._log(
                f"{self._item_label(item)}: {new_price:,} would be below the "
                f"{item.min_price:,} floor"
            )
            return
        try:
            packet = build_register_packet(item, slot.index, new_price)
        except BazarError as exc:
            quote.status = "error"
            quote.detail = str(exc)
            self._log(f"{self._item_label(item)}: not relisted ({exc})")
            return
        if not self._send(packet):
            quote.status = "error"
            quote.detail = "register send failed"
            self._log(f"{self._item_label(item)}: register send failed")
            return
        quote.status = "register-pending"
        quote.detail = f"{new_price:,} (was {quote.price:,})"
        self._pending_registration = {
            "item": item,
            "quote": quote,
            "seen": world.bazar.register_count,
            "deadline": now + REGISTER_ACK_TIMEOUT_S,
            "price": new_price,
        }
        self._log(
            f"{self._item_label(item)}: c_reg sent for {new_price:,} "
            f"(cheapest was {quote.price:,} by {quote.seller}); waiting for "
            "rc_reg"
        )

    # -- display --------------------------------------------------------

    def sorted_quotes(self) -> list[BazarQuote]:
        """Quotes sorted by profitability, most profitable first."""
        quotes = [
            self.results.get(item.name) or BazarQuote(item=item)
            for item in self.config.items
        ]

        def key(quote: BazarQuote) -> float:
            ratio = quote.ratio
            if quote.price is None or ratio is None:
                return -1.0
            return ratio

        return sorted(quotes, key=key, reverse=True)

    @property
    def summary(self) -> str:
        return self._last_pass_summary


def default_config_path() -> Path:
    """Per-user default location, overridable with ``NTPL_BAZAR_CONFIG``."""
    override = os.environ.get("NTPL_BAZAR_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ntpl" / "bazar.json"
