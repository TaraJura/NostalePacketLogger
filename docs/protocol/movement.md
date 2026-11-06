# Movement & Positioning Packets

## `walk` (SEND)

Move character to a position on the current map.

**Format**: `walk <x> <y> <checksum> <speed>`

| Field | Type | Description |
|-------|------|-------------|
| x | int | Target X coordinate |
| y | int | Target Y coordinate |
| checksum | int | Movement validation (0 or 1 seen, exact algorithm unknown) |
| speed | int | Movement speed (10 = normal walking speed) |

**Examples**:
```
walk 19 147 1 10    — move to (19, 147) at normal speed
walk 20 146 0 10    — move to (20, 146) at normal speed
```

**Notes**:
- The game client sends this when the player clicks to move
- The checksum field alternates between 0 and 1 — needs more investigation
- Speed 10 appears to be the default walking speed
- Sending this packet via injection DOES move the character (confirmed working)

**Live verification (2026-11-06, Wine, charId 14881134):**

- The character must be **standing**. While sitting (`rest` state 1) the server
  ignores movement. Toggle with `rest 1 1 <charId>` first.
- Injecting `walk 25 117 0 10` made the server answer
  `tp 1 14881134 25 117 0`; the authoritative position moved from
  `(25,118)` to `(25,117)`. The server may repeat the `tp` while it settles.
- The client does **not** reliably send `mv` for its own movement back to
  itself, so `tp` is the confirmation to wait for.
- Some targets are ignored (blocked tile, or the client/server is still
  settling); confirm each move by waiting for the matching `tp`/`mv` before
  sending the next one. `ntpl walk X Y` does that.

---

## `mv` (RECV)

Server notifies that an entity has moved.

**Format**: `mv <entityType> <entityId> <x> <y> <speed>`

| Field | Type | Description |
|-------|------|-------------|
| entityType | int | 1 = player, 2 = NPC/pet, 3 = monster/map NPC |
| entityId | int | Unique entity ID |
| x | int | New X position |
| y | int | New Y position |
| speed | int | Movement speed |

**Examples**:
```
mv 3 5090 142 81 9     — monster 5090 moved to (142, 81) at speed 9
mv 2 5107 51 97 5      — NPC/pet 5107 moved to (51, 97) at speed 5
mv 1 12345 60 80 10    — player 12345 moved to (60, 80) at speed 10
```

**Notes**:
- These are broadcast constantly for all visible entities on the map
- High volume — most common packet type during idle gameplay
- Type 3 entities (monsters/NPCs) move at various speeds (4-9 common)

---

## `tp` (RECV)

Teleport an entity to a new position instantly (no walking animation). The
server also uses it to apply an injected `walk`: after `walk 25 117 0 10` it
sent `tp 1 14881134 25 117 0`.

**Format**: `tp <entityType> <entityId> <x> <y> <unknown>`

**Examples**:
```
tp 1 14881134 25 117 0    — player 14881134 to (25,117)
```

---

## Coordinate System

- **X axis**: increases going RIGHT
- **Y axis**: increases going DOWN
- Moving "up" on screen = decreasing Y
- Moving "right" on screen = increasing X
- Coordinate (0,0) is top-left of the map
