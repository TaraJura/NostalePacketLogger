# Movement & Positioning Packets

## `walk` (SEND)

Move character to a position on the current map.

**Format**: `walk <x> <y> <checksum> <speed>`

| Field | Type | Description |
|-------|------|-------------|
| x | int | Target X coordinate |
| y | int | Target Y coordinate |
| checksum | int | `1` when `(x + y) % 3 == 1`, else `0` |
| speed | int | Movement speed (10 = normal walking speed) |

**Checksum**: the client's own walk builder computes
`((x + y) % 3 == 1) ? 1 : 0` before formatting the packet. The server
validates it. This was confirmed live on 2026-11-06: `walk 19 138 1 10` moved
the character, while `walk 19 138 0 10` did not.

**Examples**:
```
walk 22 123 1 10    (22 + 123 = 145; 145 % 3 == 1 -> checksum 1)
walk 20 139 0 10    (20 + 139 = 159; 159 % 3 == 0 -> checksum 0)
```
(Use `walk_checksum(x, y)` rather than hand-computing.)

**Notes**:
- The game client sends this when the player clicks to move.
- Speed 10 appears to be the default walking speed.
- Injecting this packet with the right checksum moves the **server's**
  position, but the client does not animate the move. The server then sends a
  `tp` correction, which the client renders as a teleport. Packet-only walking
  is therefore not a substitute for the client's own movement.
- The character must be **standing**. While sitting (`rest` state 1) the server
  ignores movement. Toggle with `rest 1 1 <charId>` first.
- Accepted walks are **not** echoed to the client. Only a rejected or
  out-of-sync walk produces `tp <type> <id> <x> <y>`. If you need the server
  position with raw packets, an impossible probe (`walk -1 -1 0 10`) makes it
  answer with `tp` at the authoritative position.

### Client-side walking (what the bot uses)

The client's own pathfinding entry point is at VA `0x565E88` in this build:

```
__fastcall bool MoveTo(PlayerObject *this /*EAX*/,
                       uint32_t packedXY /*EDX*/,
                       int flags /*ECX*/,
                       int run /*stack*/)
```

`packedXY` is `x | (y << 16)`. The player object is `[[0x792468]]`; its int16
x is at +4, y at +6, and char id at +0x24. `MOVE|x y` in the DLL calls this
function, so the client computes a path, animates the movement and sends its
own `walk` packet with the correct checksum. `POS` reads the local position.
This is what makes the character actually walk instead of teleporting.

The DLL validates the function prologue before calling it; a client patch
returns `MOVE: client build mismatch` instead of crashing.

**Live verification (2026-11-06, Wine, charId 14881134):**

- Checksum: `walk 19 138 1 10` was accepted, `walk 19 138 0 10` was ignored.
- Raw probe method: a six-cell A* path `(21,141)→(18,138)` moved the server
  position, but each server `tp` snapped the client (teleporting).
- Client MoveTo method: `MOVE|25 133` made the client emit its own
  `walk 25 133 0 10`; the GUI bot then walked `(26,133)→(23,130)` as six
  client `[SEND] walk` packets, at ~1 s per cell, with no injected walks, no
  probes and no `tp` corrections. The character animated normally.
- The client's own `walk` SEND packets are captured by the send hook; the
  server does not echo their accepted moves.

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
