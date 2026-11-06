# Maps, walkability, pathing and auto-attack

The map view in the UI is built from the client's own map grids, not from a
hand-drawn guess. This document covers the data format, the parser, the map tab
and the bot loop.

![Map 20 walkability](assets/map20.png)

The white cells are walkable ground (`0x00`); the gray area is blocked. This is
map **20** (230x260), the live map from the 2026-11-06 session.

## Data source

`NostaleData/NStcData.NOS` contains one grid per map.

```
header      16 bytes  "NT Data 05" + padding
count       uint32
separator   1 byte
index       count * (map_id:uint32, offset:uint32)
block       at offset:
              creation_date uint32
              data_size     uint32
              compressed    uint32
              is_compressed uint8
              payload       zlib stream when compressed
```

The decompressed payload:

```
width   int16
height  int16
cells   width * height bytes, one per cell, row-major
```

The terrain palette comes from the OnexExplorer/NosTale Research NSTC code:
`0x00` white (open ground), `0x01` gray, `0x02` green, `0x03` black,
`0x09` blue-gray, `0x0A` blue (water), `0x0B` red-brown, `0x0D` yellow-gray,
`0x10` purple, `0x11` green, `0x12` red, `0x13` yellow, `0xFF` purple
(unknown). The engine treats **`0x00` as walkable** and everything else as
blocked for pathfinding; the live data supports this: all 5,849 unique entity
positions observed in the session (173,503 `mv` samples) landed on `0x00`
cells in map 20.

The archive has 779 grids from 9x12 to 488x240. Map **20 (230x260, 37.3 %
walkable)** is the current live map. The map id comes from the server's
`c_map`/`at` packets when available (`WorldState.map_id`); otherwise the UI's
**Auto-detect** scores every grid by the live entity positions
(`find_map_for_positions`) and selects the best.

## Parser API

`linux/host/ntpl/mapdata.py` (standard library only):

| Function | Meaning |
|---|---|
| `load_nstc(path)` | parse the archive into `{map_id: MapGrid}` |
| `MapGrid.at(x, y)` | terrain byte or `None` |
| `MapGrid.is_walkable(x, y)` | `at() == 0x00` |
| `MapGrid.neighbours(x, y)` | walkable 4-direction neighbours |
| `astar(grid, start, goal)` | shortest walkable path, goal included |
| `find_map_for_positions(grids, points)` | rank maps for live coordinates |
| `terrain_rgb(value)` | palette colour |

Override the archive path with `NTPL_NSTC` or `--file`.

## CLI

```bash
# size, walkable percentage and a cell
linux/scripts/host.sh map info 20 --at 25,117

# render the whole map to PNG (PIL) or PPM (fallback)
linux/scripts/host.sh map render 20 /tmp/map20.png --scale 3

# rank maps against a live packet log (mv positions)
linux/scripts/host.sh map best --log "$WINEPREFIX/drive_c/NosTalePacketLog.txt"
```

## UI map tab

`linux/scripts/gui.sh` opens the window. The **Map / Bot** tab contains:

| Control | Effect |
|---|---|
| Map ID + Load | load a specific grid from `NStcData.NOS` |
| Auto-detect | rank maps by the live entity positions and select the best |
| Scale | pixels per cell (1-10) |
| left-click | add a walkable waypoint |
| right-click | remove the last waypoint |
| Clear path | drop all waypoints |
| Loop | repeat the path when it finishes |
| Start / Stop | run/stop the bot |
| Auto-attack | attack monsters in range before the next path step |
| Pickup | pick up your own/unowned drops within the loot radius |
| Loot | pickup radius in cells (default 3) |
| Skill | skill vnum (default **0** = normal/basic attack; the client sends `u_s 0 3 <monster>` and the server reports the basic skill, e.g. 866, in `su`) |
| Range | attack radius in cells |
| Cooldown | seconds between attacks |
| Char ID | own char id; auto-filled from a `rest` packet, or enter it manually |
| skill template | default `u_s {skill} {type} {target}`; use `{skill}`, `{type}`, `{target}` |

Overlays: cyan path, yellow waypoints, green players, blue NPCs/pets, orange
monsters. The own character is a **bigger red dot with a white ring and a red
facing arrow**. The arrow follows the last `walk` delta (map coordinates:
`+x` right, `+y` down), so it has eight directions. The protocol has no
own-facing packet: `in` carries a direction only at spawn, and every observed
character kept one constant spawn direction regardless of movement. Deriving
the arrow from movement is therefore the honest source.

![GUI map tab](assets/gui-map.png)

## Bot loop

`_bot_tick` runs on the GTK main loop every 200 ms:

1. **Stand up** if `WorldState.resting` is true and the char id is known:
   `rest 1 1 <charId>` (the server toggles sit/stand; sitting blocks movement).
2. **Pick up** if enabled and a drop owned by you (or unowned) is within the
   loot radius: send `get 1 <charId> <dropId>`. Each drop is tried at most
   three times; the server's `get` confirmation removes it from
   `WorldState.drops`.
3. **Auto-attack**, if enabled and the cooldown is over: pick the nearest
   `kind == 3` entity within the range from the own position, send
   `ncif 3 <id>` and the skill template, and set the cooldown.
4. **Walk** a multi-cell leg (three cells from the A* path, plus the final
   cell) by calling the client's own MoveTo: the GUI sends `MOVE|x y` to the
   DLL. The client pathfinds, animates and sends its own `walk` packet with
   the leg destination; the server accepts it as a normal player move. Because
   that packet arrives at the **start** of the animation, the bot waits
   `0.45 s` per cell (minimum 0.35 s) before sending the next leg, so the
   character walks continuously instead of stopping after every single cell.
   On a timeout it skips the leg (blocked path) and stops after three
   consecutive rejections.
5. Repeat/loop; Stop ends the timer.

The bot sends ordinary packets (`ncif`, `u_s`, `rest`) through the existing
`PacketClient` and uses the DLL's `MOVE`/`POS` commands for movement. It never
touches GTK from a worker thread. The engine feeds it the live `tp`, `mv`,
`rest`, `cond`, `su` and `stat` packets. On connect the GUI sends `POS` to
learn the client's local position, because a host that attaches after login
never sees the `at` packet.

### Limits

- A `0x00` cell can still contain an object or entity at the pixel level; if
  the client refuses the path or the server rejects the move, the bot skips the
  leg after the timeout.
- Leg size and pacing are constants in `gui.py` (three cells; `walk_confirm_
  delay = 0.45 s/cell`). A very long `MOVE` makes the client jump to the
  destination rather than animate, so keep legs short.
- `MOVE` calls a client-build-specific function (`0x565E88`). The DLL validates
  its prologue and reports `MOVE: client build mismatch` after a client patch
  instead of crashing. `POS` reads the client's local player object
  (`[[0x792468]]`): int16 x at +4, y at +6, char id at +0x24.
- Packet-only walking is still possible (`SEND|walk` with
  `walk_checksum(x, y)`), and the checksum is `1 if (x + y) % 3 == 1`; but the
  client does not animate such a walk, and the server's correction teleports
  the character. `MOVE` is the visible path.
- The facing arrow reflects the last **walk target**, not an independently
  turning character. The live protocol exposes no own-facing field; `in` spawn
  directions are constant per character. If the game turns in place, the arrow
  does not change until the next move.
- Pickup only considers drops owned by your char id or unowned (`owner_id` 0);
  another player's drops are skipped. The default loot radius is 3 cells; the
  bot does not walk to distant drops by itself yet.
- The exact `u_s` field order is still unverified beyond the verified
  `u_s <skill> <targetType> <targetId>` prefix. The template is editable;
  capture one live cast (`su 1 <charId> ...` in the log) and adjust it.
- Auto-attack targets `kind == 3` only, within **Range**. Skill **0** is the
  normal attack; the server reports the basic skill (866/868) in `su`. Skill
  866 itself is not castable and gets `cancel 0 866 -1`.
- The map id is not always known; use Auto-detect or enter the id from a
  `c_map` packet.
- The server may rate-limit or reject moves after many attempts; the bot stops
  after three consecutive unconfirmed moves.

## Tests

```bash
make -C linux host-test   # 58 cases incl. synthetic NOS, A*, checksum
make -C linux gui-test    # GUI + map surface + unknown-position guard
make -C linux bot-test    # fake server: auto-attack, pickup, MOVE, rejection
```

The bot test sends a fake player at (25,117) on map 20, a monster at
(25,118) and a drop at the player's feet; it asserts the engine sent
`get 1 14881134 999`, `ncif 3 500`, `u_s 0 3 500` and `MOVE|26 119` (not an
injected walk), and the fake client answered with its own `walk`. A second
test makes the server silent and asserts the bot stops after three rejected
moves.

## Live status

- Map **20** is current; 100 % of 173,503 `mv` samples are on walkable cells.
- Client-side walking was verified live on 2026-11-06. The GUI bot walked a
  six-cell A* path `(21,141)→(18,138)` via the checksum/probe method, then a
  second six-cell path `(26,133)→(23,130)` through the client's own MoveTo:
  the log contains only client `[SEND] walk` lines, no injected walks, no
  probes and no server `tp` teleports. The character animates normally.
- Live auto-attack is verified: the bot walked from `(45,184)` toward nearby
  monsters and landed multiple `su 1 14881134 3 <id> 866 ...` hits with
  `u_s 0 3 <id>` (2026-11-06 ~15:54). Movement and combat are both working.
- Live pickup is verified: a drop `drop 5193 13979116 49 169 1 0 14881134`
  produced `MANUAL_SEND get 1 14881134 13979116` and the server confirmed with
  `RECV get 1 14881134 13979116 0` (2026-11-06 ~15:57).
