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

from .bazar import BazarState
from .protocol import Kind, Packet


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

    Bags: ``0`` Equip, ``1`` Main, ``2`` Etc.  The server sends the whole bag
    as ``inv <bag> <slot>.<vnum>.<quantity>...`` at login and updates a single
    slot with ``ivn <bag> <slot>.<vnum>.<quantity>.<unknown>``.
    """

    bag: int
    index: int
    vnum: int
    quantity: int


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
        """First occupied slot of ``vnum`` in ``bag``, lowest slot index first."""
        slots = self.inventory.get(bag, {})
        for index in sorted(slots):
            slot = slots[index]
            if slot.vnum == vnum and slot.quantity > 0:
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
    # inv 0 (Equip) has seven fields per slot, inv 1/2 three; only the first
    # three are the slot index, template vnum and quantity.
    args = packet.args
    if not args:
        return
    try:
        bag = int(args[0])
    except ValueError:
        return
    slots: dict[int, InventorySlot] = {}
    for token in args[1:]:
        fields = token.split(".")
        if len(fields) < 3:
            continue
        try:
            index, vnum, quantity = (
                int(fields[0]),
                int(fields[1]),
                int(fields[2]),
            )
        except ValueError:
            continue
        if index < 0 or vnum <= 0 or quantity <= 0:
            continue
        slots[index] = InventorySlot(bag, index, vnum, quantity)
    world.inventory[bag] = slots


def _on_inventory_update(world: WorldState, packet: Packet) -> None:
    # ivn <bag> <slot>.<vnum>.<quantity>.<unknown>[...]
    args = packet.args
    if len(args) < 2:
        return
    try:
        bag = int(args[0])
    except ValueError:
        return
    fields = args[1].split(".")
    if len(fields) < 3:
        return
    try:
        index, vnum, quantity = (
            int(fields[0]),
            int(fields[1]),
            int(fields[2]),
        )
    except ValueError:
        return
    slots = world.inventory.setdefault(bag, {})
    if index < 0:
        return
    if vnum <= 0 or quantity <= 0:
        slots.pop(index, None)
    else:
        slots[index] = InventorySlot(bag, index, vnum, quantity)


def _on_gold(world: WorldState, packet: Packet) -> None:
    values = packet.ints(1)
    if values and values[0] >= 0:
        world.gold = values[0]


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
    if packet.timestamp - state.npc_req_at > 15.0:
        return
    state.npc_id = state.npc_req_id
    if state.n_run is not None and packet.timestamp - state.n_run_at <= 15.0:
        option, page, entity_id = state.n_run
        if entity_id == state.npc_req_id:
            state.open_option = option
            state.open_page = page
            state.option_learned = True


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
    state.npc_id = None
    state.dialog_npc_id = None
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


def _on_despawn(world: WorldState, packet: Packet) -> None:
    values = packet.ints(2)  # <type> <id>
    if values:
        world.entities.pop(values[1], None)


def _on_cancel(world: WorldState, packet: Packet) -> None:
    # cancel 0 0 -1 -- the server dropped the current cast. The bot can try
    # again at once with a live target instead of waiting out the cooldown.
    world.cancel_count += 1


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
    except (ValueError, IndexError):
        return
    if kind in (2, 3) and hp == 0:
        world.entities.pop(entity_id, None)


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
    world.combat.append(
        {
            "attacker_type": attacker_type,
            "attacker_id": attacker_id,
            "target_type": target_type,
            "target_id": target_id,
            "skill": skill,
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
                "rc_blist": _on_bazar_response,  # RECV: bazaar search answer
                "npc_req": _on_npc_req,  # SEND/RECV: NPC dialog
                "n_run": _on_npc_run,   # SEND: NPC dialog option
                "c_blist": _on_bazar_client_open,  # SEND: bazaar listing
                "c_slist": _on_bazar_client_open,  # SEND: bazaar sellers
                "in": _on_spawn,        # RECV: entity spawned
                "gidx": _on_family_index,  # RECV: player family id/name
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
