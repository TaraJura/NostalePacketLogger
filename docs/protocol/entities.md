# Entity Packets

## `in` (RECV)

An entity has appeared/spawned on the current map. Very complex packet with different formats per entity type.

**Format (type 1 — Player)**:
`in 1 <name> - <charId> <x> <y> <direction> <unknown> <unknown> <genderRaceClass> <hairStyleColor> <unknown> <equipmentSlots> <hpPercent> <mpPercent> <unknown> <unknown> <unknown> <unknown> <unknown> <familyId> <familyName> <unknown> <unknown> <unknown> <unknown> ...`

**Format (type 2 — NPC/Pet)**:
`in 2 <vnum> <entityId> <x> <y> <direction> <hpPercent> <mpPercent> <unknown> <unknown> <ownerId> <unknown> <unknown> <unknown> <name> ...`

**Format (type 3 — Monster)**:
`in 3 <vnum> <entityId> <x> <y> <direction> <unknown> <unknown> <unknown> <unknown> ...`

**Examples**:
```
in 1 Guder - 4165102 105 23 2 0 1 2 42 1 0.4952.4964.4955.4610.4132.8680.0.8598.4443.-1 100 100 ...
    — Player "Guder" (ID 4165102) spawned at (105, 23)

in 2 2557 2230354 104 24 2 96 100 0 0 3 4165102 1 0 2379 Graham ...
    — NPC vnum 2557 (entity 2230354) at (104, 24), owned by player 4165102, named "Graham"

in 2 1488 2230353 105 24 2 100 109 0 0 3 4165102 1 0 -1 Panda-Leuchtii<3 ...
    — Pet vnum 1488 at (105, 24), named "Panda-Leuchtii<3"
```

**Notes**:
- Entity type is the second field (after `in`)
- Equipment slots for players are dot-separated vnum lists
- HP/MP are percentages (0-100)
- `vnum` = virtual number, the template ID of the NPC/monster/item

---

## `out` (RECV)

An entity has despawned / left the visible area.

**Format**: `out <entityType> <entityId>`

*TODO: Capture examples*

---

## Entity Types Reference

| Type | Description | Examples |
|------|-------------|----------|
| 1 | Player character | Other players on the map |
| 2 | NPC, Pet, Partner | Shop NPCs, player pets, quest NPCs |
| 3 | Monster, Map NPC | Mobs, static map NPCs |
