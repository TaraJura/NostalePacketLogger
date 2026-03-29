# NosTale Reverse Engineering Research

Compiled from Phoenix Bot docs, hatz2's repos, stradiveri's repos, NosCore, NosSmooth, and community resources.

## Key Resources

| Resource | Type | URL | Value |
|----------|------|-----|-------|
| NosCore.Packets | C# packet defs | github.com/NosCoreIO/NosCore.Packets | 872 commits, typed defs for ALL packets |
| NosSmooth | C# bot framework | github.com/Rutherther/NosSmooth | A* pathfinding, game state, combat |
| PhoenixAPI | Python API client | github.com/hatz2/PhoenixAPI | Complete game state model |
| nostale-dmg-calculator | JS | github.com/hatz2/nostale-dmg-calculator | monster.json, skill.json, item.json, damage formulas |
| NosCrypto | Python | github.com/morsisko/NosCrypto | Login + World packet encryption |
| GflessClient | C++ | github.com/hatz2/GflessClient | Gameforge auth bypass, NostaleString |
| go-noskit | Go bot | github.com/Gilgames000/go-noskit | Clientless bot with pathfinder |
| OpenNos | C# server | github.com/OpenNos/OpenNos | Reference server emulator |
| Recv packet list | Gist | gist.github.com/morsisko/7f9aa9f0f... | 400+ recv packet headers |

## Entity Types (Raw Packets vs Phoenix API)

**Raw packet `in` types** (what our DLL sees):
- 1 = Player
- 2 = NPC / Pet / Partner
- 3 = Monster / Map NPC

**Phoenix API types** (abstraction layer, different numbering):
- 1 = Player, 2 = Monster, 3 = NPC, 4 = Item

**Our parser must use the raw packet types (1/2/3).**

## Complete Packet Reference

### Movement (SEND)

| Opcode | Format | Description |
|--------|--------|-------------|
| `walk` | `walk {x} {y} {checksum} {speed}` | Move character |
| `dir` | `dir {type} {id} {direction}` | Change facing direction |
| `sit` / `rest` | `rest {toggle} {type} {id}` | Sit/stand toggle |
| `preq` | `preq` | Request portal entry |
| `pulse` | `pulse {time} {flag}` | Heartbeat/keepalive |

### Movement (RECV)

| Opcode | Format | Description |
|--------|--------|-------------|
| `mv` | `mv {entityType} {entityId} {x} {y} {speed}` | Entity moved |
| `tp` | `tp {type} {entityType} {entityId} {?} {?} {?}` | Teleport |
| `at` | `at {charId} {mapId} {x} {y} {dir} ...` | Position on map load |
| `c_map` | `c_map {?} {mapId} {?}` | Map change |

### Combat (SEND)

| Opcode | Format | Description |
|--------|--------|-------------|
| `u_s` | `u_s {castId} {targetType} {targetId}` | Use skill on target |
| `u_s` | `u_s {skillVnum} 1 {playerId}` | Use skill on self |
| `ncif` | `ncif {entityType} {entityId}` | Select/target entity |

### Combat (RECV)

| Opcode | Format | Description |
|--------|--------|-------------|
| `su` | `su {atkType} {atkId} {tgtType} {tgtId} {skillVnum} {cooldown} {atkAnim} {skillEffect} {posX} {posY} {isAlive} {hpPercent} {damage} {hitMode} {skillType}` | Skill result (15 fields) |
| `sr` | `sr {skillSlotId}` | Skill cooldown reset |
| `eff` | `eff {entityType} {entityId} {effectId}` | Visual effect (effectId 8 = all cooldowns reset) |
| `die` | `die {entityType} {entityId} ...` | Entity death |
| `revive` | TBD | Entity revive |

### Entities (RECV)

| Opcode | Format | Description |
|--------|--------|-------------|
| `in 1` | `in 1 {name} - {charId} {x} {y} {dir} ... {equipSlots} {hp%} {mp%} ...` | Player spawn |
| `in 2` | `in 2 {vnum} {entityId} {x} {y} {dir} {hp%} {mp%} ... {name}` | NPC/Pet spawn |
| `in 3` | `in 3 {vnum} {entityId} {x} {y} {dir} {hp%} {mp%} ...` | Monster spawn |
| `out` | `out {entityType} {entityId}` | Entity despawn |
| `cond` | `cond {entityType} {entityId} {?} {?} {speed}` | Condition update |

### Stats (RECV)

| Opcode | Format | Description |
|--------|--------|-------------|
| `stat` | `stat {hp} {hpMax} {mp} {mpMax} {?} {xp}` | HP/MP/XP update |
| `lev` | `lev {level} {jobLevel} ...` | Level info |
| `st` | stats update (frequent, filtered as spam) | |

### Chat

| Opcode | Dir | Format | Description |
|--------|-----|--------|-------------|
| `say` | RECV | `say {entityType} {entityId} {msgType} {message}` | Chat message |
| `say` | SEND | `say {message}` | Send chat |
| `sayi` | RECV | `sayi {type} {entityId} {?} {?} {msgCode}` | System message (2497=no bait) |
| `spk` | RECV | `spk {type} {?} {id} {name} {message}` | Speaker message |
| `msgi` | RECV | `msgi ...` | System notification |

### NPC Interaction (SEND)

| Opcode | Format | Description |
|--------|--------|-------------|
| `npc_req` | `npc_req {entityType} {entityId}` | Talk to NPC |
| `n_run` | `n_run {actionId} [{subId}] [{?}] [{entityId}]` | NPC dialog action |
| `shop_end` | `shop_end 1` | Close shop window |
| `buy` | TBD | Buy item |
| `sell` | TBD | Sell item |

### Inventory (SEND)

| Opcode | Format | Description |
|--------|--------|-------------|
| `u_i` | `u_i {invTab} {playerId} {bagType} {slot} 0 0` | Use item (invTab: 1=main, 2=etc) |
| `mvi` | TBD | Move item |
| `get` | `get 1 0 {entityId}` | Pick up ground item |

### Crafting (SEND)

| Opcode | Format | Description |
|--------|--------|-------------|
| `pdtse` | `pdtse 1 {recipeVnum}` | Start crafting |
| `guri` | `guri 2` | Begin craft minigame |
| `guri` | `guri 5 1 {playerId} {progress} -2` | Craft progress (0,20,40,60,80,100) |
| `pdtse` | `pdtse 0 {vnum} -1 -1 0` | Finish crafting |

### Revival

| Opcode | Dir | Format | Description |
|--------|-----|--------|-------------|
| `dlgi` | RECV | `dlgi #revival` | Death dialog |
| `#revival^8` | SEND | `#revival^8` | Accept revival |

### Fishing Events (RECV)

| Opcode | Format | Description |
|--------|--------|-------------|
| `guri 6 1 {pid} 30` | | Normal fish on line |
| `guri 6 1 {pid} 31` | | Legendary fish on line |
| `guri 6 1 {pid} 0` | | Fish missed |

### Portal (RECV)

| Opcode | Format | Description |
|--------|--------|-------------|
| `gp` | `gp {srcX} {srcY} {mapId} {portalType} {portalId}` | Portal info |

### Special

| Opcode | Dir | Format | Description |
|--------|-----|--------|-------------|
| `guri 1000` | SEND | `guri 1000 {mapId} {x} {y}` | Teleport to coords |
| `#arena^0^1` | SEND | | Enter arena/event |
| `sl` | SEND | | SP card equip/unequip |

## Packet Serialization Rules (from NosCore)

- Fields separated by spaces
- `^` = sub-property separator
- `#` = discriminator prefix
- `.` = list element separator (e.g. equipment: `0.4952.4964.4955`)
- `-1` = null/empty value
- Booleans: `1`/`0`/`-1`

## Map System

- Grid-based: cells are WALKABLE(0), OBSTACLE(1), OBSTACLE(2)
- Coordinates: X increases right, Y increases down
- (0,0) = top-left of map
- Pathfinding: A* on the walkability grid (implemented in NosSmooth and go-noskit)

## Damage Formula

```
Physical = (TotalAttack - TotalDefense) * CritMultiplier
Elemental = (ElementPower + (TotalAttack + 100) * FairyLevel) * Resistance * ElementBonus
Morale = CharLevel + MoraleBonus - MonsterLevel
Total = (Physical + Elemental + Morale) * PercentDamageIncrease
```

### Element Weakness Table (multiplier)

| Attacker \ Target | None | Fire | Water | Light | Shadow |
|---|---|---|---|---|---|
| None | 0.3 | 0 | 0 | 0 | 0 |
| Fire | 0.3 | 0.0 | 1.0 | 0.0 | 0.5 |
| Water | 0.3 | 1.0 | 0.0 | 0.5 | 0.0 |
| Light | 0.3 | 0.5 | 0.0 | 0.0 | 2.0 |
| Shadow | 0.3 | 0.0 | 0.5 | 2.0 | 0.0 |

### Upgrade Bonus Table

+0=0%, +1=10%, +2=15%, +3=22%, +4=32%, +5=43%, +6=54%, +7=65%, +8=90%, +9=120%, +10=200%

## Equipment Slots (18 total)

0=Main Weapon, 1=Armor, 2=Hat, 3=Gloves, 4=Boots, 5=Secondary Weapon, 6=Necklace, 7=Ring, 8=Bracelet, 9=Mask, 10=Fairy, 11=Amulet, 12=SP Card, 13=Body Costume, 14=Hat Costume, 15=Weapon Costume, 16=Wings Costume, 17=Minipet

## Inventory Tabs

EQUIP=0, MAIN=1, ETC=2

## Character Classes

Adventurer=0, Swordsman=1, Archer=2, Mage=3, Fighter/MartialArtist=4

## Directions (8-way)

UP=0, RIGHT=1, DOWN=2, LEFT=3, UP_LEFT=4, UP_RIGHT=5, DOWN_RIGHT=6, DOWN_LEFT=7

## Skill System

- **SkillType**: DAMAGE=0, DEBUFF=1, BUFF=2
- **TargetType**: TARGET=0, SELF=1, SELF_OR_TARGET=2, NO_TARGET=3
- Skills have: vnum, castId, range, area, cast_time, cool_time, mana_cost, element
- Track `sr` packets for cooldown resets
- Track `is_ready` state per skill

## Game Data Files

Available as JSON from hatz2/nostale-dmg-calculator:
- `monster.json` — All monsters with vnum, level, race, element, resistances, defense
- `skill.json` — All skills with vnum, type, class, attack_type, element, targeting, buffs
- `item.json` — All items
- `bcard.json` — Buff card definitions

Can also be extracted from `.NOS` game files using OnexExplorer or NosSmooth.Data CLI.

## Spam Packets to Filter

**SEND spam**: `ncif`, `ptctl`, `pulse`
**RECV spam**: `mv`, `eff`, `pst`, `st`, `cond`
