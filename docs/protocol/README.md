# NosTale Packet Protocol Wiki

This is a living document built by observing live gameplay. Each packet type is documented with its format, fields, examples, and behavior.

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
| `npc_req` | SEND | Talk to NPC | [npc.md](npc.md) |
| `n_run` | SEND | NPC dialog action | [npc.md](npc.md) |
| `shop` | RECV | Shop opened | [npc.md](npc.md) |

### Inventory
| Opcode | Dir | Description | Doc |
|--------|-----|-------------|-----|
| `inv` | RECV | Inventory contents | [inventory.md](inventory.md) |
| `ivn` | RECV | Inventory slot update | [inventory.md](inventory.md) |
| `mvi` | SEND | Move item in inventory | [inventory.md](inventory.md) |

### Unknown / To Investigate
See [unknown.md](unknown.md) for packets we've seen but haven't fully documented yet.

---

*This wiki is actively being built. Run the game, perform actions, and let Claude document what happens.*
