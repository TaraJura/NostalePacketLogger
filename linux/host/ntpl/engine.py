"""Game-state model fed by the packet stream.

This is the Phase 3 seed from the original roadmap: instead of printing packets,
turn them into a world the rest of the engine can reason about -- map, own
position, entities, stats, chat.

The formats here are the ones documented in ``docs/protocol/``. They are
deliberately tolerant: an unknown opcode, a short packet, or a non-numeric field
is ignored rather than raised, because a live game sends packets the wiki has
not documented yet. Add a handler with :meth:`Engine.on` when you learn a new
one.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from .bazar import (
    BAZAR_SKILL_WINDOW_S,
    BAZAR_WINDOW_TYPE,
    BIBI_VNUM,
    BazarError,
    BazarState,
    parse_rc_slist,
)
from .protocol import Kind, Packet


@dataclass
class MinigameInfo:
    """The last Miniland production minigame the server described.

    ``mlo_info`` is sent when a miniland object (Great Quarry 3125, Saw Mill
    3126, Shooting Range 3127, Fish Pond 3128) is opened: object vnum, slot,
    the account's production points and the reward tiers as
    ``(minimum, maximum)`` pairs (the first pair is the no-reward range).
    ``mlo_mg``/``mlpt`` update the points after a result is claimed.
    """

    vnum: int | None = None
    slot: int | None = None
    points: int | None = None
    tiers: list[tuple[int, int]] = field(default_factory=list)
    updated_at: float = 0.0
    update_count: int = 0
    #: `mlo_st <value>`: the server's answer to a minigame start/step.
    status: int | None = None
    status_count: int = 0
    #: `mlo_lv <level>`: the reward level for the reported score. It is
    #: **0-based**: the live server answered `mlo_lv 4` for a 5x score, and
    #: the client then claims with `mg 4 <slot> <vnum> 4`.
    reward_level: int | None = None
    reward_count: int = 0
    #: `mlo_rw <vnum> <amount>`: the reward item granted by the claim.
    reward_vnum: int | None = None
    reward_amount: int = 0


@dataclass
class Entity:
    entity_id: int
    kind: int = 0          # 1 = player, 2 = pet/partner/NPC, 3 = monster/NPC
    vnum: int = 0          # NPC/monster template id from `in`, 0 for players
    name: str = ""
    x: int = 0
    y: int = 0
    speed: int = 0
    # Family membership from `in 1` and `gidx`. `family_known` separates a
    # player observed without a family from one whose family was never sent,
    # so auto-invite never guesses. The wire token is `<id>.<rank>`.
    family_id: int = -1
    family_name: str = ""
    family_known: bool = False
    # Last `st` HP/max for a monster/pet (kind 2/3). -1 = unknown. The Pet
    # Trainer tamer uses the ratio to decide when to stop hitting and Tame.
    hp: int = -1
    hp_max: int = 0
    # Fairy presence: the equipment sub-packet carries the fairy item vnum;
    # `pairy` carries the element and confirms a fairy. No-fairy players are
    # the fresh/alt accounts the family auto-invite filter excludes.
    fairy_vnum: int = -1
    fairy_element: int = -1
    fairy_known: bool = False

    @property
    def has_fairy(self) -> bool:
        return self.fairy_vnum > 0 or self.fairy_element > 0


@dataclass
class GroundItem:
    """An item lying on the ground (`drop`) or just picked up (`get`)."""

    drop_id: int
    item_vnum: int
    x: int
    y: int
    amount: int = 1
    owner_id: int = 0


@dataclass(frozen=True)
class TimeSpace:
    """One Time-Space entrance from the server's `wp` list."""

    map_id: int
    x: int
    y: int
    ts_id: int
    min_level: int
    max_level: int


@dataclass
class CharacterStats:
    hp: int = 0
    hp_max: int = 0
    mp: int = 0
    mp_max: int = 0


@dataclass(frozen=True)
class InventorySlot:
    """One occupied inventory slot.

    Bags: ``0`` Equip, ``1`` Main, ``2`` Etc; the live server also sends
    equipment-style bags ``6`` and ``7``.  The server sends the whole bag as
    ``inv <bag> <slot>.<vnum>.<quantity>...`` at login and updates a single
    slot with ``ivn <bag> <slot>.<vnum>.<quantity>...``.  Equipment bags
    append extra dot fields that the engine does not decode; they are kept
    in :attr:`fields` so the row can still be displayed.  Negative vnums
    are limited/bound items, and quantity 0 is normal for an occupied
    equipment slot.
    """

    bag: int
    index: int
    vnum: int
    quantity: int
    #: Extra dot fields after quantity, e.g. ``('7', '1', '0', '0')``.
    fields: tuple[str, ...] = ()

    @property
    def amount(self) -> int:
        """Stack size; an equipment row with quantity 0 still holds one."""
        return self.quantity if self.quantity > 0 else (1 if self.fields else 0)


def parse_inventory_token(
    bag: int, token: str
) -> tuple[int, InventorySlot | None] | None:
    """Parse one ``slot.vnum.quantity[.<extra>...]`` inventory token.

    Returns ``(slot_index, slot)`` for an occupied slot, ``(slot_index,
    None)`` for an empty/removal token, and ``None`` for a malformed token.

    Equipment bags (0 Equip and the 6/7 costume bags) carry extra fields
    and may report quantity 0 for an occupied slot.  Standard bags use
    three fields in ``inv`` and add one unknown field in ``ivn``, where
    quantity 0 means the slot was emptied.  Negative vnums are
    limited/bound items: they are kept for display, while
    :meth:`WorldState.find_inventory` never returns them.
    """
    fields = token.split(".")
    if len(fields) < 3:
        return None
    try:
        index = int(fields[0])
        vnum = int(fields[1])
        quantity = int(fields[2])
    except ValueError:
        return None
    if index < 0:
        return None
    # A standard `ivn` token has a fourth (unknown) field, so the field
    # count alone cannot tell equipment from a normal bag: a 4-field token
    # is a standard update, while equipment bags use 5+ fields and keep
    # quantity 0.
    equipment_style = bag in (0, 6, 7) or len(fields) >= 5
    if vnum == 0:
        return index, None
    if not equipment_style and quantity <= 0:
        return index, None
    if equipment_style and quantity < 0:
        return index, None
    # Standard `ivn` tokens append one always-zero unknown field; only the
    # equipment bags carry fields worth keeping and showing.
    extra = tuple(fields[3:]) if equipment_style else ()
    return index, InventorySlot(bag, index, vnum, quantity, extra)


@dataclass
class WorldState:
    map_id: int | None = None
    x: int | None = None
    y: int | None = None
    # Last movement direction in map coordinates: (dx, dy), each -1, 0 or 1.
    # +x is right, +y is down on the map. None until the first walk.
    facing: tuple[int, int] | None = None
    entities: dict[int, Entity] = field(default_factory=dict)
    drops: dict[int, GroundItem] = field(default_factory=dict)
    # Portals are server-side warp tiles with no client table. The server
    # sends the whole list of a map's portals in `gp` packets when entering;
    # those are authoritative. Crossings are the fallback when `gp` was
    # missed: (mapId, x, y) held when the leaving `c_map`/`mapout` arrived,
    # plus the destination map from the following `at`.
    gates: dict[tuple[int, int, int], int] = field(default_factory=dict)
    portals: dict[tuple[int, int, int], int] = field(default_factory=dict)
    pending_departure: tuple[int, int, int] | None = None
    # `wp <x> <y> <tsId> <flag> <minLevel> <maxLevel>`: the map's Time-Space
    # entrances, sent on entry with the gp list. Live map 4 has TS 16, 17,
    # 19, 20, 21, 22 and 704; the ids and levels match the in-game list.
    timespaces: dict[tuple[int, int, int], TimeSpace] = field(
        default_factory=dict
    )
    stats: CharacterStats = field(default_factory=CharacterStats)
    chat: deque[tuple[int, int, str]] = field(default_factory=lambda: deque(maxlen=200))
    resting: bool | None = None
    self_id: int | None = None
    target_id: int | None = None
    last_skill: int | None = None
    # `cancel 0 0 -1`: the server cancelled the current cast (dead/out of
    # range target, interrupted). Monotonic counter for the bot.
    cancel_count: int = 0
    # `ski <page> <skillVnum...>`: the client's ordered skill list. `u_s`
    # carries a zero-based slot in this deduplicated list, not the vnum.
    skill_pages: dict[int, list[int]] = field(default_factory=dict)
    skill_list: list[int] = field(default_factory=list)
    # Slot -> vnum learned from the client's own `u_s <slot>` answered by the
    # server's `su ... <vnum> ...`. `ski` is only sent at login, so a host
    # that attaches later never sees the list; pairing the two packets still
    # reveals the slots of the skills that were actually used (and handles an
    # SP card swap without a relog).
    skill_learned: dict[int, int] = field(default_factory=dict)
    # The vnum the server reports for `u_s 0`, i.e. the equipped SP card's
    # basic attack (Energy Bolt 260 on the live mage, Holy Attack 866 on the
    # swordsman). This is what makes range/cast/cooldown/MP per-card correct
    # without hardcoding a class skill.
    basic_skill: int | None = None
    # `sr <castingId>`: the server's cooldown for that skill ended (the client
    # clears the skill bar's cooldown on it). Cast speed uses this as the
    # authoritative "ready" moment instead of waiting out a local timer; the
    # custom `sr -10 <vnum> <duration> <elapsed>` form starts a cooldown and
    # is not a readiness signal. Values are `time.monotonic()` so the GUI's
    # bot tick can compare them with its own clock.
    skill_ready_at: dict[int, float] = field(default_factory=dict)
    pending_skill_slot: int | None = None
    pending_skill_at: float = 0.0
    # Last entity that hit the local player, with the packet timestamp.
    last_attacker_id: int | None = None
    last_attacker_at: float = 0.0
    # Every entity that hit the local player, with its last hit timestamp.
    # The single last-attacker pair makes Retaliate switch again on each hit
    # when two monsters attack; this map lets the GUI stay on the attacker it
    # is already fighting while that one is still attacking.
    self_attackers: dict[int, float] = field(default_factory=dict)
    # The local character died (`stat` with 0 HP) and has not been revived
    # yet. The server sends `revive 1 <charId> 0` when it brings it back.
    dead: bool = False
    # The server offered the "Continue here? You'll need 10 Seeds of Power"
    # dialog (`dlgi #revival^0 #revival^1 ...`). Confirming it revives the
    # character in place and spends the seeds.
    revival_offered: bool = False
    # Hits landed by the local character (attacker type 1, id == self_id).
    # The GUI compares it around a cast: the server answers every successful
    # cast with `su`, then `cancel 0 0 -1` for the end of the animation, so a
    # cancel only means a real refusal when no self hit arrived in between.
    self_hit_count: int = 0
    combat: deque[dict] = field(default_factory=lambda: deque(maxlen=100))
    last_packet: Packet | None = None
    #: bag -> slot index -> item.  Filled from `inv`/`ivn`; an attach after
    #: login only sees the updates that happen while the host is connected.
    inventory: dict[int, dict[int, InventorySlot]] = field(
        default_factory=dict
    )
    gold: int = 0
    #: char id -> player name from `in 1 <name> - <charId> ...`.
    player_names: dict[int, str] = field(default_factory=dict)
    #: NosBazar window/NPC state (see ntpl.bazar.BazarState).
    bazar: BazarState = field(default_factory=BazarState)
    #: The last Miniland production object (Great Quarry etc.):
    #: `mlo_info` carries its vnum, slot, production points and the reward
    #: tiers; `mlo_mg`/`mlpt` update the points after a claimed reward.
    minigame: MinigameInfo = field(default_factory=MinigameInfo)
    #: `rdlst`: every character id in the current raid/party list (the local
    #: player included), and the monotonic time of that list. The game
    #: refuses the production minigames while the character is in a raid
    #: team ("You cannot use it when you are already a member of a raid
    #: team"). `raid_active` is the authoritative state; this list is the
    #: fallback for a host that attached after the last `raid` packet.
    raid_members: set[int] = field(default_factory=set)
    raid_at: float = 0.0
    #: `raid 1 <flag>`: the server's authoritative raid-team state (1 in a
    #: team, 0 not) and `raid 2 <leaderId>` (the leader char id, -1 = none).
    #: Live 2026-11-26: joining sends `raid 1 1` + `raid 2 <id>`, leaving
    #: sends `raid 1 0` + `raid 2 -1` with no new `rdlst` — the list alone
    #: cannot show that the team ended.
    raid_active: bool | None = None
    raid_leader: int = -1
    #: `msgi 0 <id> ...`: the server's last Game18N system message key and a
    #: counter, so a feature can react to a specific message. The Pet Trainer
    #: tamer watches 2614 (taming successful), 1887 (max pet slots) and 2631
    #: (taming failed).
    last_message_id: int = 0
    message_seq: int = 0
    #: `sc_p <slot> <vnum> <entityId> ...`: the pet/partner slots; `p_clear`
    #: empties it before the server repopulates the list. The tamer shows the
    #: occupancy and stops on the server's max-pet-slots message.
    pets: dict[int, int] = field(default_factory=dict)

    @property
    def position(self) -> tuple[int, int] | None:
        if self.x is None or self.y is None:
            return None
        return (self.x, self.y)

    @property
    def self_name(self) -> str | None:
        """The local character's name, once its `in 1` spawn was seen."""
        if self.self_id is None:
            return None
        return self.player_names.get(self.self_id)

    def find_inventory(self, bag: int, vnum: int) -> InventorySlot | None:
        """First relistable slot of ``vnum`` in ``bag``, lowest index first.

        Negative vnums (limited/bound equipment) are stored for display but
        never returned here, and neither is an empty row: only a real,
        positive stack can be relisted.
        """
        slots = self.inventory.get(bag, {})
        for index in sorted(slots):
            slot = slots[index]
            if slot.vnum == vnum and slot.vnum > 0 and slot.amount > 0:
                return slot
        return None

    def skill_slot(self, vnum: int) -> int | None:
        """Zero-based `u_s` slot for a skill vnum, or None if not learned."""
        try:
            return self.skill_list.index(vnum)
        except ValueError:
            pass
        for slot, learned in self.skill_learned.items():
            if learned == vnum:
                return slot
        return None

    def skill_vnum(self, slot: int) -> int | None:
        if 0 <= slot < len(self.skill_list):
            return self.skill_list[slot]
        return self.skill_learned.get(slot)


def walk_checksum(x: int, y: int) -> int:
    """The client's walk checksum: 1 when ``(x + y) % 3 == 1``, else 0.

    The client's walk builder computes exactly this before formatting
    ``walk <x> <y> <checksum> <speed>``; the live server rejects a walk with
    the wrong value. Confirmed on 2026-11-06: ``(19, 138)`` needs 1 and moved
    the character, the same target with 0 did not.
    """
    return 1 if (x + y) % 3 == 1 else 0


Handler = Callable[[WorldState, Packet], None]


def _on_inventory(world: WorldState, packet: Packet) -> None:
    # inv <bag> <slot>.<vnum>.<quantity>[.<...>] ...
    # inv 0 (Equip) has seven fields per slot, inv 1/2 three, bags 6/7 five.
    # The parser keeps the extra equipment fields and negative vnums; empty
    # tokens just leave their index out of the rebuilt bag.
    args = packet.args
    if not args:
        return
    try:
        bag = int(args[0])
    except ValueError:
        return
    slots: dict[int, InventorySlot] = {}
    for token in args[1:]:
        parsed = parse_inventory_token(bag, token)
        if parsed is None:
            continue  # malformed token
        index, slot = parsed
        if slot is None:
            continue  # removal/empty token
        slots[index] = slot
    world.inventory[bag] = slots


def _on_inventory_update(world: WorldState, packet: Packet) -> None:
    # ivn <bag> <slot>.<vnum>.<quantity>[.<...>] -- one slot or a removal.
    # Equipment quantity 0 is a normal occupied row, so the parser (not this
    # handler) decides which tokens empty the slot.
    args = packet.args
    if len(args) < 2:
        return
    try:
        bag = int(args[0])
    except ValueError:
        return
    parsed = parse_inventory_token(bag, args[1])
    if parsed is None:
        return
    index, slot = parsed
    slots = world.inventory.setdefault(bag, {})
    if slot is None:
        slots.pop(index, None)
    else:
        slots[index] = slot


def _on_gold(world: WorldState, packet: Packet) -> None:
    values = packet.ints(1)
    if values and values[0] >= 0:
        world.gold = values[0]


def _on_minigame_info(world: WorldState, packet: Packet) -> None:
    """`mlo_info`: the open Miniland production object and its tiers.

    Live 2026-11-26 (Great Quarry):
    ``mlo_info 0 3125 5 2000 0 0 0 999 1000 4999 ... 16000 1000000``.
    Fields: owner flag, object vnum, slot, production points, two flags, then
    (minimum, maximum) reward-tier pairs (the first pair has no reward).
    """
    if packet.direction is not Kind.RECV:
        return
    args = packet.args
    if len(args) < 6:
        return
    try:
        values = [int(value) for value in args]
    except ValueError:
        return
    state = world.minigame
    state.vnum = values[1]
    state.slot = values[2]
    state.points = values[3]
    tiers = [
        (values[index], values[index + 1])
        for index in range(6, len(values) - 1, 2)
    ]
    if tiers:
        state.tiers = tiers
    state.updated_at = time.monotonic()
    state.update_count += 1


def _on_minigame_result(world: WorldState, packet: Packet) -> None:
    """`mlo_mg <minigameVnum> <points> ...`: the result was accepted."""
    if packet.direction is not Kind.RECV:
        return
    values = packet.ints(2)
    if values is None:
        return
    state = world.minigame
    state.vnum = values[0]
    state.points = values[1]
    state.updated_at = time.monotonic()
    state.update_count += 1


def _on_minigame_status(world: WorldState, packet: Packet) -> None:
    """`mlo_st <value>`: the server's minigame start/step answer.

    Live 2026-11-26: the client's own GAMESTART (`mg 1 4 3125`) is answered
    by `mlo_st 0` within ~0.1 s.
    """
    if packet.direction is not Kind.RECV:
        return
    values = packet.ints(1)
    if values is None:
        return
    state = world.minigame
    state.status = values[0]
    state.status_count += 1
    state.updated_at = time.monotonic()


def _on_minigame_level(world: WorldState, packet: Packet) -> None:
    """`mlo_lv <level>`: the reward level for the reported score (0-based).

    Live 2026-11-26: `mg 3 4 3125 16124 16124` was answered by `mlo_lv 4`;
    the client's next packet is `mg 4 4 3125 4`.
    """
    if packet.direction is not Kind.RECV:
        return
    values = packet.ints(1)
    if values is None:
        return
    state = world.minigame
    state.reward_level = values[0]
    state.reward_count += 1
    state.updated_at = time.monotonic()


def _on_minigame_reward(world: WorldState, packet: Packet) -> None:
    """`mlo_rw <vnum> <amount>`: the reward item granted by the claim."""
    if packet.direction is not Kind.RECV:
        return
    values = packet.ints(2)
    if values is None:
        return
    state = world.minigame
    state.reward_vnum = values[0]
    state.reward_amount = values[1]
    state.updated_at = time.monotonic()
    # A reward is the strongest confirmation that the claim was accepted.
    state.update_count += 1


def _on_miniland_points(world: WorldState, packet: Packet) -> None:
    """`mlpt <points> <cap>`: the account's Miniland production points."""
    if packet.direction is not Kind.RECV:
        return
    values = packet.ints(1)
    if values is None:
        return
    world.minigame.points = values[0]
    world.minigame.updated_at = time.monotonic()
    world.minigame.update_count += 1


def _learn_bazar_open(world: WorldState, packet: Packet) -> None:
    """Remember the NPC/option that opened the bazaar window.

    The user normally opens the bazaar by clicking Bibi Basar; replaying that
    interaction later needs the entity id and the dialog option, and both are
    map/session specific.  Learning them from the client's own packets is more
    robust than the hardcoded id in Nostale-NB.
    """
    state = world.bazar
    if state.npc_req_id is None:
        return
    # Never learn a non-bazaar dialog: the id is per entity instance and a
    # stale one on another map is a different NPC (live 2026-11-23: Carve
    # Runes). The entity list is authoritative.
    entity = world.entities.get(state.npc_req_id)
    if entity is not None and getattr(entity, "vnum", 0) != BIBI_VNUM:
        return
    if packet.timestamp - state.npc_req_at > 15.0:
        return
    state.npc_id = state.npc_req_id
    if state.n_run is not None and packet.timestamp - state.n_run_at <= 15.0:
        option, page, entity_id = state.n_run
        if entity_id == state.npc_req_id:
            state.open_option = option
            state.open_page = page
            state.option_learned = True
            # A verified Bibi entity confirms the dialog id for this server;
            # an unverified id might be another NPC (Carve Runes), so leave
            # the known default in place then.
            if (
                entity is not None
                and getattr(entity, "vnum", 0) == BIBI_VNUM
                and state.dialog_npc_id == state.npc_req_id
                and state.dialog_id is not None
            ):
                state.bazaar_dialog_id = state.dialog_id


def _on_bazar_response(world: WorldState, packet: Packet) -> None:
    # rc_blist <header> <entry>[ <entry>...] confirms the window is open and
    # answers a c_blist search.  Entries are grouped by searched vnum; the
    # bazar module parses the pipe-separated fields and takes the minimum
    # visible price (the first entry need not be the cheapest).
    state = world.bazar
    state.open = True
    state.response_count += 1
    state.last_response = packet.text
    # Controllers run on time.monotonic(); packet timestamps are wall time.
    state.last_response_at = time.monotonic()
    state.responses.append((state.response_count, packet.text))


def _on_bazar_sellers(world: WorldState, packet: Packet) -> None:
    # rc_slist <page> <filter> <entry> …: the seller tab, always only our own
    # listings. The sold amount in each entry decides whether the in-game
    # Change (repricing) action exists; the relist uses it for c_mod.
    if packet.direction is not Kind.RECV:
        return
    try:
        entries = parse_rc_slist(packet.text)
    except BazarError:
        return
    state = world.bazar
    state.seller_entries = entries
    state.seller_count += 1
    state.seller_at = time.monotonic()
    state.seller_text = packet.text


def _on_bazar_register_ack(world: WorldState, packet: Packet) -> None:
    # rc_reg <value>: the server accepted a c_reg. It answers nothing for a
    # registration it ignores, so the missing count is the failure signal.
    if packet.direction is not Kind.RECV:
        return
    values = packet.ints(1)
    state = world.bazar
    state.register_count += 1
    state.register_value = values[0] if values else 1
    state.register_at = time.monotonic()


def _on_window_open(world: WorldState, packet: Packet) -> None:
    # wopen <type> <page> ...: a server window opened. Type 32 is NosBazar;
    # rc_blist alone is no proof because searches are answered even when the
    # window is not open.
    if packet.direction is not Kind.RECV:
        return
    values = packet.ints(1)
    if not values:
        return
    state = world.bazar
    state.window_type = values[0]
    state.window_at = time.monotonic()
    if values[0] == BAZAR_WINDOW_TYPE:
        state.window_open = True
        state.window_count += 1
        # Some servers open the bazaar from a bare client shortcut: FrosTale
        # answers the client's `c_skill` with `wopen 32` in ~0.2 s (11/11
        # live openings 2026-11-23). Remember that this client opens it
        # that way so the controller can replay it with no NPC or medal.
        if (
            0.0 <= packet.timestamp - state.open_skill_at
            <= BAZAR_SKILL_WINDOW_S
        ):
            state.open_skill = True


def _on_client_skill(world: WorldState, packet: Packet) -> None:
    # SEND: `c_skill` with no arguments -- the client's own NosBazar
    # shortcut on FrosTale. Timestamped here; `wopen 32` right after it
    # proves the pairing.
    if packet.direction is not Kind.SEND:
        return
    world.bazar.open_skill_at = packet.timestamp


def _on_npc_req(world: WorldState, packet: Packet) -> None:
    values = packet.ints(2)
    if values is None:
        return
    entity_id = values[1]
    state = world.bazar
    if packet.direction is Kind.SEND:
        state.npc_req_id = entity_id
        state.npc_req_at = packet.timestamp
    else:
        state.dialog_npc_id = entity_id
        state.dialog_at = packet.timestamp
        # npc_req 2 <entityId> <dialogId>: 460 is Bibi Basar.
        reply = packet.ints(3)
        state.dialog_id = reply[2] if reply is not None else None


def _on_npc_run(world: WorldState, packet: Packet) -> None:
    # n_run <option> <page> 2 <entityId> -- the client's dialog choice.
    if packet.direction is not Kind.SEND:
        return
    values = packet.ints(4)
    if values is None:
        return
    state = world.bazar
    state.n_run = (values[0], values[1], values[3])
    state.n_run_at = packet.timestamp


def _on_bazar_client_open(world: WorldState, packet: Packet) -> None:
    # The client's own initial c_blist/c_slist after clicking NosBazar is
    # proof that the last npc_req+n_run opened the bazaar.  A filtered search
    # (count > 0) must not be mistaken for the opening.
    if packet.direction is not Kind.SEND:
        return
    if packet.opcode == "c_blist":
        args = packet.args
        if len(args) < 9:
            return
        try:
            count = int(args[8])
        except ValueError:
            return
        if count != 0:
            return
    _learn_bazar_open(world, packet)


def _on_walk(world: WorldState, packet: Packet) -> None:
    coords = packet.ints(2)
    # Negative coordinates are the CLI/GUI's position probe, not a real move.
    if coords and coords[0] >= 0 and coords[1] >= 0:
        if world.position is not None:
            dx = coords[0] - world.x
            dy = coords[1] - world.y
            if dx != 0 or dy != 0:
                world.facing = ((dx > 0) - (dx < 0), (dy > 0) - (dy < 0))
        world.x, world.y = coords


def _remember_departure(world: WorldState) -> None:
    """Note where the character stood when the server announced a map exit."""
    if world.map_id is not None and world.position is not None:
        world.pending_departure = (world.map_id, world.x, world.y)


def _reset_bazar_map_state(world: WorldState) -> None:
    """The bazaar window belongs to a map instance: NPC ids are map-specific."""
    state = world.bazar
    state.open = False
    state.window_open = False
    state.window_type = None
    state.window_at = 0.0
    state.npc_id = None
    state.dialog_npc_id = None
    state.dialog_id = None
    state.npc_req_id = None
    state.n_run = None


def _on_map(world: WorldState, packet: Packet) -> None:
    # Live: c_map 0 <mapId> <state> (state 1 = entered, 0 = leaving).
    # The first field is a type, not the map id. Reading it made every map
    # change end with map id 0, so the UI never switched maps.
    values = packet.ints(3)
    if len(values) >= 3 and values[2] == 0:
        _remember_departure(world)
    if len(values) >= 2:
        world.map_id = values[1]
    elif values:
        world.map_id = values[0]
    world.entities.clear()
    world.drops.clear()
    _reset_bazar_map_state(world)


def _on_mapout(world: WorldState, packet: Packet) -> None:
    # `mapout` follows the leaving `c_map`, before the new map's `at` and
    # spawns. Drop the old map's entities so the ranking sees only the new map.
    _remember_departure(world)
    world.entities.clear()
    world.drops.clear()
    _reset_bazar_map_state(world)


def _on_drop(world: WorldState, packet: Packet) -> None:
    # drop <itemVnum> <dropId> <x> <y> <amount> <unknown> <ownerCharId>
    # Live: drop 1046 13961541 29 180 635 0 14881134
    values = packet.ints(7)
    if not values:
        return
    item_vnum, drop_id, x, y, amount = values[:5]
    owner_id = values[6]
    world.drops[drop_id] = GroundItem(
        drop_id=drop_id,
        item_vnum=item_vnum,
        x=x,
        y=y,
        amount=amount,
        owner_id=owner_id,
    )


def _on_get(world: WorldState, packet: Packet) -> None:
    # get <type> <charId> <dropId> <unknown>  (RECV, after a pickup)
    # The client also sends a shorter `get <type> <charId> <dropId>`; only the
    # server's confirmation removes the item.
    values = packet.ints(4)
    if values:
        world.drops.pop(values[2], None)


def _on_position(world: WorldState, packet: Packet) -> None:
    # at <charId> <mapId> <x> <y> <direction> ...
    values = packet.ints(4)
    if values:
        map_id = values[1]
        pending = world.pending_departure
        if pending is not None:
            # The `at` after a leaving `c_map`/`mapout` names the destination:
            # the remembered cell is a portal. The arrival cell is the return
            # portal on the new map (live crossings land on the warp tile), so
            # record both directions and every map gets the marker at once.
            if map_id != pending[0]:
                world.portals[(pending[0], pending[1], pending[2])] = map_id
                world.portals[(map_id, values[2], values[3])] = pending[0]
            world.pending_departure = None
        if world.map_id is not None and map_id != world.map_id:
            # A map change can be seen here even when the host missed the
            # leading c_map/mapout (for example right after attaching).
            world.entities.clear()
            world.drops.clear()
        world.self_id = values[0]
        world.map_id = map_id
        world.x, world.y = values[2], values[3]


def _on_move(world: WorldState, packet: Packet) -> None:
    values = packet.ints(5)  # <type> <id> <x> <y> <speed>
    if not values:
        return
    kind, entity_id, x, y = values[0], values[1], values[2], values[3]
    entity = world.entities.setdefault(entity_id, Entity(entity_id, kind))
    entity.kind = kind
    entity.x = x
    entity.y = y
    if entity_id == world.self_id:
        world.x, world.y = x, y


def _parse_family_id(token: str) -> int:
    """Family id from the wire token ``<id>.<rank>``; -1 means none."""
    head = token.split(".", 1)[0]
    try:
        return int(head)
    except ValueError:
        return -1


def _on_spawn(world: WorldState, packet: Packet) -> None:
    args = packet.args
    if not args:
        return

    try:
        kind = int(args[0])
    except ValueError:
        return

    if kind == 1:
        # in 1 <name> - <charId> <x> <y> <direction> ...
        # The name may contain spaces, so split on the " - " separator instead
        # of assuming a fixed field index.
        try:
            _, rest = packet.text.split(" ", 1)
            _, rest = rest.split(" ", 1)
            name, tail = rest.split(" - ", 1)
            values = tail.split()
            char_id = int(values[0])
            x = int(values[1])
            y = int(values[2])
        except (ValueError, IndexError):
            return

        entity = world.entities.get(char_id)
        if entity is None:
            entity = Entity(char_id, kind)
            world.entities[char_id] = entity
        entity.name = name
        entity.x = x
        entity.y = y
        # `in 1` carries familyId and familyName at values 22/23 (char_id is
        # value 0). "-1 -" means the player has no family. Short synthetic
        # packets leave the family state unknown instead of guessing.
        if len(values) >= 24:
            entity.family_id = _parse_family_id(values[22])
            entity.family_name = (
                "" if values[23] in ("-", "") else values[23]
            )
            entity.family_known = True
        # Equipment sub-packet: hat.armor.main.secondary.mask.fairy...
        # FairyVNum is the sixth dot-separated value; -1 means no fairy.
        if len(values) > 9:
            equipment = values[9].split(".")
            if len(equipment) > 5:
                try:
                    entity.fairy_vnum = int(equipment[5])
                except ValueError:
                    entity.fairy_vnum = -1
                entity.fairy_known = True
        world.player_names[char_id] = name
        if len(world.player_names) > 2000:
            # The id -> name map only feeds the bazaar owner check; a busy
            # town over a long session must not grow without bound.
            world.player_names.clear()
            world.player_names[char_id] = name
        return

    if kind in (2, 3):
        # in <type> <vnum> <entityId> <x> <y> ...
        # The entity is keyed by entityId, never by vnum: two NPCs can share a
        # vnum, and mv/out use the entityId.
        if len(args) < 5:
            return
        try:
            entity_id = int(args[2])
            x = int(args[3])
            y = int(args[4])
        except ValueError:
            return

        entity = world.entities.get(entity_id)
        if entity is None:
            entity = Entity(entity_id, kind)
            world.entities[entity_id] = entity
        entity.kind = kind
        try:
            entity.vnum = int(args[1])
        except ValueError:
            pass
        entity.x = x
        entity.y = y


def _on_party_list(world: WorldState, packet: Packet) -> None:
    """Learn player names from the party/raid list.

    Live shape (2026-11-26)::

        rdlst <a> <b> <c> <level>.<x>.<y>.<flags>.<name>.<class>.<charId>.<hp> ...

    The list includes the local player, so a host that attached after the
    login spawn still learns its own name (and the relist never needs it
    typed).  Player names carry no dots, so the dot fields are unambiguous.
    """
    parts = packet.text.split(None, 4)
    if len(parts) < 4:
        return
    members: set[int] = set()
    if len(parts) >= 5:
        for token in parts[4].split():
            fields = token.split(".")
            if len(fields) < 7:
                continue
            name = fields[4].strip()
            try:
                char_id = int(fields[6])
            except ValueError:
                continue
            if char_id > 0:
                members.add(char_id)
            if char_id > 0 and name and name != "-":
                world.player_names[char_id] = name
    world.raid_members = members
    world.raid_at = time.monotonic()


def _on_raid(world: WorldState, packet: Packet) -> None:
    """Track the server's own raid-team state.

    Live shape (2026-11-26)::

        raid 1 1                 joined a raid team
        raid 2 1168              the leader's char id
        raid 1 0                 left / the team ended
        raid 2 -1                no leader any more

    `rdlst` only arrives while a team exists, so without this packet the
    Minigame tab kept blocking Play for the whole `rdlst` freshness window
    after the user had already left the raid.
    """
    args = packet.args
    if len(args) < 2:
        return
    try:
        kind = int(args[0])
        value = int(args[1])
    except ValueError:
        return
    if kind == 1:
        world.raid_active = value != 0
        if not world.raid_active:
            # The team ended: the last list is no longer membership.
            world.raid_members.clear()
            world.raid_at = 0.0
            world.raid_leader = -1
    elif kind == 2:
        world.raid_leader = value


def _on_family_index(world: WorldState, packet: Packet) -> None:
    # gidx 1 <entityId> <familyId>.<rank> <familyName> ...
    # The server sends it for players already on the map and when membership
    # changes. A -1 family token and "-" name mean no family.
    args = packet.args
    if len(args) < 4:
        return
    try:
        kind = int(args[0])
        entity_id = int(args[1])
    except ValueError:
        return
    if kind != 1:
        return
    entity = world.entities.get(entity_id)
    if entity is None or entity.kind != 1:
        return
    entity.family_id = _parse_family_id(args[2])
    entity.family_name = "" if args[3] in ("-", "") else args[3]
    entity.family_known = True


def _on_fairy_info(world: WorldState, packet: Packet) -> None:
    # pairy <type> <entityId> <moveType> <element> <elementRate> <morphVNum>
    # Sent for players accompanied by a fairy; the element is 1..4.
    args = packet.args
    if len(args) < 4:
        return
    try:
        kind = int(args[0])
        entity_id = int(args[1])
        element = int(args[3])
    except ValueError:
        return
    if kind != 1:
        return
    entity = world.entities.get(entity_id)
    if entity is None or entity.kind != 1:
        return
    entity.fairy_element = element
    entity.fairy_known = True


def _on_despawn(world: WorldState, packet: Packet) -> None:
    values = packet.ints(2)  # <type> <id>
    if values:
        world.entities.pop(values[1], None)


def _on_cancel(world: WorldState, packet: Packet) -> None:
    # cancel 0 0 -1 -- the server dropped the current cast. The bot can try
    # again at once with a live target instead of waiting out the cooldown.
    world.cancel_count += 1


def _on_skill_ready(world: WorldState, packet: Packet) -> None:
    # sr <castingId> -- the server's cooldown for this skill ended. Only the
    # plain form is a readiness signal; `sr -10 <vnum> <duration> <elapsed>`
    # (this server's cooldown-start form) must not clear a gate.
    values = packet.ints(1)
    if not values or values[0] < 0:
        return
    # Monotonic, like the GUI's bot clock. `Packet.timestamp` is wall time
    # and cannot be compared with the tick's `time.monotonic()`.
    world.skill_ready_at[values[0]] = time.monotonic()


def _on_status(world: WorldState, packet: Packet) -> None:
    # st <type> <id> <level> <unknown> <state> <unknown> <hp> <unknown>
    #    <hpMax> ... -- live monster status (2026-11-07, map 3):
    #   alive: st 3 3481 14 0 100 100 481 108 481 108 0
    #   dead:  st 3 3481 14 0 1   100 0   108 481 108 0
    # The server does not send `out` for a killed monster; the zero current
    # HP here is its death notice, so drop the entity at once instead of
    # letting the bot select the corpse (the client object is already gone).
    args = packet.args
    if len(args) < 9:
        return
    try:
        kind = int(args[0])
        entity_id = int(args[1])
        hp = int(args[6])
        hp_max = int(args[8])
    except (ValueError, IndexError):
        return
    if kind in (2, 3):
        if hp == 0:
            world.entities.pop(entity_id, None)
            return
        entity = world.entities.get(entity_id)
        if entity is not None:
            entity.hp = hp
            entity.hp_max = hp_max


def _on_system_message(world: WorldState, packet: Packet) -> None:
    # msgi <type> <id> ... -- a Game18N message key (2614 taming successful,
    # 1887 max pet slots, 2631 taming failed, ...). The client shows the
    # localized text; the key is what a feature can key off.
    values = packet.ints(2)
    if not values:
        return
    world.last_message_id = values[1]
    world.message_seq += 1


def _on_pet(world: WorldState, packet: Packet) -> None:
    # sc_p <slot> <vnum> <entityId> ... -- one pet/partner slot (live
    # 2026-11-27: slots 0-7, the tamed Chicken vnum 333 included).
    args = packet.args
    if len(args) < 2:
        return
    try:
        slot = int(args[0])
        vnum = int(args[1])
    except ValueError:
        return
    if slot < 0:
        return
    world.pets[slot] = vnum


def _on_pet_clear(world: WorldState, packet: Packet) -> None:
    # p_clear -- the server sends it before repopulating the pet panel.
    world.pets.clear()


def _on_teleport(world: WorldState, packet: Packet) -> None:
    values = packet.ints(4)  # <type> <id> <x> <y>
    if not values:
        return
    kind, entity_id, x, y = values
    entity = world.entities.setdefault(entity_id, Entity(entity_id, kind))
    entity.x = x
    entity.y = y
    if entity_id == world.self_id:
        world.x, world.y = x, y


def _on_rest(world: WorldState, packet: Packet) -> None:
    # rest <entityType> <entityId> <state>; observed live: 1 = sitting,
    # 0 = standing. Movement is rejected by the server while sitting.
    values = packet.ints(3)
    if not values:
        return
    kind, entity_id, state = values
    # `rest` is also sent for other players (live 2026-11-07: `rest 1
    # 11361583 0` for Quest22). Once self_id is known, ignore theirs: taking
    # it would corrupt self_id and the sit/stand state the bot relies on.
    if world.self_id is not None and entity_id != world.self_id:
        return
    world.self_id = entity_id
    world.resting = state == 1
    entity = world.entities.get(entity_id)
    if entity is not None:
        entity.kind = kind


def _on_condition(world: WorldState, packet: Packet) -> None:
    # cond <entityType> <entityId> <unknown> <unknown> <speed>
    values = packet.ints(5)
    if not values:
        return
    kind, entity_id, _unknown1, _unknown2, speed = values
    # The live client sends `cond` only for the local player (every observed
    # line in the 2026-11-06 log carried the owner's charId and no other), so
    # it is also the earliest cheap way to learn self_id before the first
    # `rest` or `tp`.
    if kind == 1 and world.self_id is None:
        world.self_id = entity_id
    entity = world.entities.get(entity_id)
    if entity is not None:
        entity.kind = kind
        entity.speed = speed


def _on_select(world: WorldState, packet: Packet) -> None:
    # ncif <entityType> <entityId> -- target selection (SEND).
    values = packet.ints(2)
    if values:
        world.target_id = values[1]


def _on_gate(world: WorldState, packet: Packet) -> None:
    # gp <x> <y> <toMapId> <unknown> <index> <unknown> -- the map's portals,
    # sent after `at`/`c_map` on entry. Live 2026-11-07 on map 3:
    #   gp 10 208 6 -1 0 0
    #   gp 19 7 7 -1 1 0
    #   gp 45 12 103 -1 2 0
    #   gp 156 2 4 -1 3 0
    #   gp 178 189 2 -1 4 0
    values = packet.ints(3)
    if not values or world.map_id is None:
        return
    x, y, destination = values
    if x >= 0 and y >= 0 and destination > 0:
        world.gates[(world.map_id, x, y)] = destination


def _on_waypoint(world: WorldState, packet: Packet) -> None:
    # wp <x> <y> <tsId> <flag> <minLevel> <maxLevel> -- the map's Time-Space
    # entrances. Like `gp` it carries no map id; it follows the entering
    # c_map/at. Live 2026-11-07 on map 4:
    #   wp 35 142 16 4 26 99
    #   wp 229 33 704 0 90 99
    values = packet.ints(6)
    if not values or world.map_id is None:
        return
    x, y, ts_id, _flag, min_level, max_level = values
    if x < 0 or y < 0 or ts_id <= 0:
        return
    world.timespaces[(world.map_id, x, y)] = TimeSpace(
        world.map_id, x, y, ts_id, min_level, max_level
    )


def _on_skill_list(world: WorldState, packet: Packet) -> None:
    # ski <page> <vnum> <vnum> ... ; duplicates are skill ranks/variants.
    # The client's u_s slot is the index in the deduplicated, page-ordered
    # skill list, so 866..876 map to slots 0..10 for the live specialist.
    values = packet.ints(len(packet.args))
    if not values:
        return
    page = values[0]
    world.skill_pages[page] = [value for value in values[1:] if value > 0]
    ordered: list[int] = []
    for page_number in sorted(world.skill_pages):
        for vnum in world.skill_pages[page_number]:
            if vnum not in ordered:
                ordered.append(vnum)
    world.skill_list = ordered
    if ordered and world.basic_skill is None:
        # Slot 0 is the card's basic attack; live `u_s 0` is answered with it
        # (866 on the 2026-11-06 specialist, 260 on the 2026-11-07 mage).
        world.basic_skill = ordered[0]


def _on_skill_send(world: WorldState, packet: Packet) -> None:
    # SEND: the client's own `u_s <slot> <targetType> <targetId>`.
    if packet.direction is not Kind.SEND:
        return
    values = packet.ints(1)
    if not values:
        return
    world.pending_skill_slot = values[0]
    world.pending_skill_at = packet.timestamp


def _on_skill_used(world: WorldState, packet: Packet) -> None:
    # su <attackerType> <attackerId> <targetType> <targetId> <skillVnum> ...
    # Live example:
    #   su 1 14877915 3 5180 866 9 11 4401 0 0 0 0 6507 0 0 0 4140
    # The damage/HP fields after the first five are not decoded yet, so the
    # event keeps the raw arguments as well.
    values = packet.ints(5)
    if not values:
        return
    attacker_type, attacker_id, target_type, target_id, skill = values
    world.last_skill = skill
    if (
        attacker_type == 1
        and world.self_id is not None
        and attacker_id == world.self_id
    ):
        # Our own `u_s <slot>` was answered: this is the slot -> vnum mapping
        # the skill list would have given us. Slot 0 is the basic attack.
        slot = world.pending_skill_slot
        if (
            slot is not None
            and packet.timestamp - world.pending_skill_at <= 5.0
        ):
            world.skill_learned[slot] = skill
            if slot == 0:
                world.basic_skill = skill
        # A stale pending slot must not attach to a later hit.
        world.pending_skill_slot = None
    if target_type == 1 and target_id == world.self_id:
        # Something is hitting us; remember it so the bot can retaliate.
        world.last_attacker_id = attacker_id
        world.last_attacker_at = packet.timestamp
        world.self_attackers[attacker_id] = packet.timestamp
        if len(world.self_attackers) > 32:
            cutoff = packet.timestamp - 60.0
            world.self_attackers = {
                entity_id: when
                for entity_id, when in world.self_attackers.items()
                if when >= cutoff
            }
    if attacker_type == 1 and attacker_id == world.self_id:
        world.self_hit_count += 1
    # Live 2026-11-23 format: args[11] target HP %, args[12] damage,
    # args[15]/args[16] target current/max HP. Older captures do not have
    # this tail, so the values stay None for a short packet.
    decoded = packet.ints(17)
    world.combat.append(
        {
            "attacker_type": attacker_type,
            "attacker_id": attacker_id,
            "target_type": target_type,
            "target_id": target_id,
            "skill": skill,
            "damage": decoded[12] if decoded else None,
            "hp_percent": decoded[11] if decoded else None,
            "target_hp": decoded[15] if decoded else None,
            "target_hp_max": decoded[16] if decoded else None,
            "args": tuple(packet.args),
            "text": packet.text,
        }
    )


def _on_stats(world: WorldState, packet: Packet) -> None:
    values = packet.ints(4)  # <hp> <hpMax> <mp> <mpMax>
    if values:
        world.stats.hp, world.stats.hp_max, world.stats.mp, world.stats.mp_max = values
        if world.stats.hp_max > 0:
            world.dead = world.stats.hp <= 0


def _on_revive(world: WorldState, packet: Packet) -> None:
    # revive 1 <charId> 0 -- the server brought the character back to life.
    values = packet.ints(2)
    if values and world.self_id is not None and values[1] == world.self_id:
        world.dead = False
        world.revival_offered = False


def _on_dialog(world: WorldState, packet: Packet) -> None:
    # dlgi #revival^0 #revival^1 571 0 0 -- the in-place revival offer.
    if "#revival" in packet.text:
        world.revival_offered = True


def _on_chat(world: WorldState, packet: Packet) -> None:
    parts = packet.text.split(" ", 3)  # say <type> <id> <message>
    if len(parts) != 4:
        return
    try:
        kind = int(parts[1])
        entity_id = int(parts[2])
    except ValueError:
        return
    world.chat.append((kind, entity_id, parts[3]))


class Engine:
    """Consumes packets and keeps a :class:`WorldState` up to date."""

    def __init__(self, world: WorldState | None = None) -> None:
        self.world = world if world is not None else WorldState()
        self._handlers: dict[str, Handler] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self._handlers.update(
            {
                "walk": _on_walk,       # SEND: our own movement
                "c_map": _on_map,       # RECV: map change
                "mapout": _on_mapout,   # RECV: leave the current map
                "at": _on_position,     # RECV: position after map load
                "mv": _on_move,         # RECV: entity movement
                "drop": _on_drop,       # RECV: item dropped on the ground
                "get": _on_get,         # RECV: pickup confirmation
                "inv": _on_inventory,   # RECV: whole bag contents
                "ivn": _on_inventory_update,  # RECV: one bag slot
                "gold": _on_gold,       # RECV: own gold
                "mlo_info": _on_minigame_info,  # RECV: miniland object
                "mlo_mg": _on_minigame_result,  # RECV: minigame result
                "mlo_st": _on_minigame_status,  # RECV: minigame start/step
                "mlo_lv": _on_minigame_level,  # RECV: reward level
                "mlo_rw": _on_minigame_reward,  # RECV: reward item
                "mlpt": _on_miniland_points,  # RECV: production points
                "rc_blist": _on_bazar_response,  # RECV: bazaar search answer
                "rc_reg": _on_bazar_register_ack,  # RECV: c_reg accepted
                "rc_slist": _on_bazar_sellers,  # RECV: our seller listings
                "wopen": _on_window_open,  # RECV: a server window opened
                "npc_req": _on_npc_req,  # SEND/RECV: NPC dialog
                "n_run": _on_npc_run,   # SEND: NPC dialog option
                "c_blist": _on_bazar_client_open,  # SEND: bazaar listing
                "c_slist": _on_bazar_client_open,  # SEND: bazaar sellers
                "c_skill": _on_client_skill,  # SEND: client shortcut (opens
                #                                the bazaar on FrosTale)
                "in": _on_spawn,        # RECV: entity spawned
                "rdlst": _on_party_list,  # RECV: party/raid list (names)
                "raid": _on_raid,       # RECV: joined/left a raid team
                "msgi": _on_system_message,  # RECV: Game18N message key
                "sc_p": _on_pet,        # RECV: one pet/partner slot
                "p_clear": _on_pet_clear,  # RECV: pet panel repopulation
                "gidx": _on_family_index,  # RECV: player family id/name
                "pairy": _on_fairy_info,  # RECV: player fairy element
                "out": _on_despawn,     # RECV: entity despawned
                "st": _on_status,       # RECV: entity HP/status (death)
                "tp": _on_teleport,     # RECV: entity teleported / server position
                "rest": _on_rest,       # RECV: sit/stand state
                "cond": _on_condition,  # RECV: entity speed/state
                "ncif": _on_select,     # SEND: target selection
                "u_s": _on_skill_send,  # SEND: skill used (slot, not vnum)
                "gp": _on_gate,         # RECV: the map's portal list
                "wp": _on_waypoint,     # RECV: the map's Time-Space list
                "ski": _on_skill_list,  # RECV: learned skill list
                "su": _on_skill_used,   # RECV: a skill hit
                "sr": _on_skill_ready,  # RECV: a skill's cooldown ended
                "cancel": _on_cancel,   # RECV: the cast was cancelled
                "stat": _on_stats,      # RECV: own hp/mp
                "revive": _on_revive,   # RECV: the character was revived
                "dlgi": _on_dialog,     # RECV: dialog (revival offer)
                "say": _on_chat,        # RECV and SEND: chat
            }
        )

    def on(self, opcode: str, handler: Handler) -> "Engine":
        """Register or replace the handler for one opcode."""
        self._handlers[opcode] = handler
        return self

    def feed(self, packet: Packet) -> WorldState:
        """Apply one packet. Never raises on malformed fields."""
        self.world.last_packet = packet

        handler = self._handlers.get(packet.opcode)
        if handler is not None:
            try:
                handler(self.world, packet)
            except (ValueError, IndexError, KeyError):
                # A packet the wiki documents differently than the live client
                # sends is not a reason to kill the monitor.
                pass

        return self.world

    def feed_text(self, direction: str, text: str) -> WorldState:
        """Convenience for tests and replay files."""
        from .protocol import Kind

        return self.feed(Packet(Kind(direction.upper()), text, 0.0))
