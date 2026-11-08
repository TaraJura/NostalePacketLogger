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

in 3 206 5122 132 182 2 100 100 0 0 0 -1 0 0 -1 - 0 -1 0 0 0 0 0 0 0 0 0 0 0 1 0
    — Monster vnum 206 (entity 5122) at (132, 182); 206 → zts200e → "Mountain Turtle"

in 3 223 5164 103 169 2 100 100 0 0 0 -1 1 0 -1 - 0 -1 0 0 0 0 0 0 0 0 0 0 0 0 0
    — Monster vnum 223 (entity 5164) at (103, 169); 223 → zts217e → "Small Wasp"
```

**Notes**:
- Entity type is the second field (after `in`)
- Equipment slots for players are dot-separated vnum lists
- HP/MP are percentages (0-100)
- `vnum` = virtual number, the template ID of the NPC/monster/item
- **Monster packets carry the template vnum first and the per-session
  `entityId` second.** `ncif`, `u_s` and `su` use the `entityId`; the map
  tooltip and target filter need the vnum. The engine stores both.
- The client derives the displayed monster name from the vnum in two steps:
  `NSgtdData.NOS` → `monster.dat` maps the vnum to a text key (`VNUM 419` →
  `NAME zts489e`), and `NSlangData_<lang>.NOS` → `_code_<lang>_monster.txt`
  maps that key (`zts489e<TAB>Violent Seedle`). The key is **not** the vnum:
  vnum 419 is a Violent Seedle, while text record 419 is "Wheat".
  `monsternames.py` performs the join. The skill table uses the same
  `zts<id>e` key space (`Skill.dat` NAME).
- A monster that was already alive when the DLL was injected has no `in`
  packet in the log, so its vnum is unknown until it respawns/re-enters view.
  `spawnlog.py` recovers the ones whose `in` was logged before the GUI
  connected; the Targets UI has an explicit unknown-vnum option.

---

## `out` (RECV)

An entity has despawned / left the visible area.

**Format**: `out <entityType> <entityId>`

Live examples:

```
18:09:16.274 [RECV] out 2 3297053
18:09:33.431 [RECV] out 1 14879942
```

The engine removes `entityId` from `WorldState.entities`; it does not use the
vnum.

---

## Entity Types Reference

| Type | Description | Examples |
|------|-------------|----------|
| 1 | Player character | Other players on the map |
| 2 | NPC, Pet, Partner | Shop NPCs, player pets, quest NPCs |
| 3 | Monster, Map NPC | Mobs, static map NPCs |
