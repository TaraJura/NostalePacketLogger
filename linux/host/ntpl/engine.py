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

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from .protocol import Packet


@dataclass
class Entity:
    entity_id: int
    kind: int = 0          # 1 = player, 2 = pet/partner/NPC, 3 = monster/NPC
    name: str = ""
    x: int = 0
    y: int = 0
    speed: int = 0


@dataclass
class CharacterStats:
    hp: int = 0
    hp_max: int = 0
    mp: int = 0
    mp_max: int = 0


@dataclass
class WorldState:
    map_id: int | None = None
    x: int | None = None
    y: int | None = None
    entities: dict[int, Entity] = field(default_factory=dict)
    stats: CharacterStats = field(default_factory=CharacterStats)
    chat: deque[tuple[int, int, str]] = field(default_factory=lambda: deque(maxlen=200))
    resting: bool | None = None
    self_id: int | None = None
    target_id: int | None = None
    last_skill: int | None = None
    combat: deque[dict] = field(default_factory=lambda: deque(maxlen=100))
    last_packet: Packet | None = None

    @property
    def position(self) -> tuple[int, int] | None:
        if self.x is None or self.y is None:
            return None
        return (self.x, self.y)


Handler = Callable[[WorldState, Packet], None]


def _on_walk(world: WorldState, packet: Packet) -> None:
    coords = packet.ints(2)
    if coords:
        world.x, world.y = coords


def _on_map(world: WorldState, packet: Packet) -> None:
    ids = packet.ints(1)
    if ids:
        world.map_id = ids[0]
        world.entities.clear()


def _on_position(world: WorldState, packet: Packet) -> None:
    # at <charId> <mapId> <x> <y> <direction> ...
    values = packet.ints(4)
    if values:
        world.map_id = values[1]
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
        entity.x = x
        entity.y = y


def _on_despawn(world: WorldState, packet: Packet) -> None:
    values = packet.ints(2)  # <type> <id>
    if values:
        world.entities.pop(values[1], None)


def _on_teleport(world: WorldState, packet: Packet) -> None:
    values = packet.ints(4)  # <type> <id> <x> <y>
    if not values:
        return
    kind, entity_id, x, y = values
    entity = world.entities.setdefault(entity_id, Entity(entity_id, kind))
    entity.x = x
    entity.y = y


def _on_rest(world: WorldState, packet: Packet) -> None:
    # rest <entityType> <entityId> <state>; observed live: 1 = sitting,
    # 0 = standing. Movement is rejected by the server while sitting.
    values = packet.ints(3)
    if not values:
        return
    kind, entity_id, state = values
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
    entity = world.entities.get(entity_id)
    if entity is not None:
        entity.kind = kind
        entity.speed = speed


def _on_select(world: WorldState, packet: Packet) -> None:
    # ncif <entityType> <entityId> -- target selection (SEND).
    values = packet.ints(2)
    if values:
        world.target_id = values[1]


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
                "at": _on_position,     # RECV: position after map load
                "mv": _on_move,         # RECV: entity movement
                "in": _on_spawn,        # RECV: entity spawned
                "out": _on_despawn,     # RECV: entity despawned
                "tp": _on_teleport,     # RECV: entity teleported / server position
                "rest": _on_rest,       # RECV: sit/stand state
                "cond": _on_condition,  # RECV: entity speed/state
                "ncif": _on_select,     # SEND: target selection
                "su": _on_skill_used,   # RECV: a skill hit
                "stat": _on_stats,      # RECV: own hp/mp
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
