# NosTale Packet Protocol Wiki

This is a living document built by observing live gameplay. Each packet type is
documented with its format, fields, examples, and behavior.

The packet semantics here are shared by the `windows/` and `linux/` builds. Only
the transport differs: named pipes on Windows, TCP loopback in the Linux port.
See [../ipc.md](../ipc.md).

**How this was built**: The user plays the game while Claude reads the packet log (`C:\NosTalePacketLog.txt`) and documents what each packet does.

## Packet Basics

- **Direction**: `SEND` = client → server, `RECV` = server → client
- **Format**: Space-separated fields. First field is the opcode (packet name).
- **Entity types**: 1 = player, 2 = NPC/pet/partner, 3 = monster/NPC on map

## Packet Index

### Movement & Positioning
| Opcode | Dir | Description | Doc |
|--------|-----|-------------|-----|
| `walk` | SEND | Move character to position | [movement.md](movement.md) |
| `mv` | RECV | Entity moved | [movement.md](movement.md) |
| `tp` | RECV | Teleport entity | [movement.md](movement.md) |

### Map & World
| Opcode | Dir | Description | Doc |
|--------|-----|-------------|-----|
| `c_map` | RECV | Map change / load map | [map.md](map.md) |
| `at` | RECV | Character position on map load | [map.md](map.md) |

### Entities
| Opcode | Dir | Description | Doc |
|--------|-----|-------------|-----|
| `in` | RECV | Entity spawned on map | [entities.md](entities.md) |
| `out` | RECV | Entity despawned | [entities.md](entities.md) |

### Stats & Status
| Opcode | Dir | Description | Doc |
|--------|-----|-------------|-----|
| `stat` | RECV | HP/MP/XP update | [stats.md](stats.md) |
| `cond` | RECV | Entity condition/state | [stats.md](stats.md) |
| `rest` | SEND/RECV | Sit/stand toggle and state; sitting blocks movement | [stats.md](stats.md) |
| `lev` | RECV | Level info | [stats.md](stats.md) |

### Combat
| Opcode | Dir | Description | Doc |
|--------|-----|-------------|-----|
| `su` | RECV | Skill used / damage dealt | [combat.md](combat.md) |
| `u_s` | SEND | Use skill | [combat.md](combat.md) |
| `ncif` | SEND | Select/target entity | [combat.md](combat.md) |

### Chat
| Opcode | Dir | Description | Doc |
|--------|-----|-------------|-----|
| `say` | RECV | Chat message | [chat.md](chat.md) |
| `say` | SEND | Send chat message | [chat.md](chat.md) |
| `msgi` | RECV | System message | [chat.md](chat.md) |

### NPC & Shops
| Opcode | Dir | Description | Doc |
|--------|-----|-------------|-----|
| `npc_req` | SEND/RECV | Talk to NPC / dialog id | [npc.md](npc.md) |
| `n_run` | SEND | NPC dialog action | [npc.md](npc.md) |
| `wopen` | RECV | A window opened (also warehouses; not bazaar-specific) | [bazaar.md](bazaar.md) |
| `shop` | RECV | Shop descriptor pushed with a merchant NPC spawn | [npc.md](npc.md) |

### NosBazar (player auction house)
| Opcode | Dir | Description | Doc |
|--------|-----|-------------|-----|
| `c_blist` | SEND | Bazaar listing search; count 0 = initial open | [bazaar.md](bazaar.md) |
| `rc_blist` | RECV | Search answer; cheapest visible listing(s), may be truncated | [bazaar.md](bazaar.md) |
| `c_slist` | SEND | Initialise the seller list | [bazaar.md](bazaar.md) |
| `rc_slist` | RECV | Seller list answer | [bazaar.md](bazaar.md) |
| `c_reg` | SEND | Register/relist an inventory item | [bazaar.md](bazaar.md) |

### Inventory
| Opcode | Dir | Description | Doc |
|--------|-----|-------------|-----|
| `inv` | RECV | Inventory contents (`bag slot.vnum.qty …`) | [inventory.md](inventory.md) |
| `ivn` | RECV | Inventory slot update/removal | [inventory.md](inventory.md) |
| `mvi` | SEND | Move item in inventory | [inventory.md](inventory.md) |
| `gold` | RECV | Own gold amount | [inventory.md](inventory.md) |

### Unknown / To Investigate
See [unknown.md](unknown.md) for packets we've seen but haven't fully documented yet.

---

*This wiki is actively being built. Run the game, perform actions, and let Claude document what happens.*
