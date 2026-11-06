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
| Skill | skill vnum (default 866, the vnum seen in the live `su` log) |
| Range | attack radius in cells |
| Cooldown | seconds between attacks |
| Char ID | own char id; auto-filled from a `rest` packet, or enter it manually |
| skill template | default `u_s {skill} {type} {target}`; use `{skill}`, `{type}`, `{target}` |

Overlays: cyan path, yellow waypoints, red own character, green players, blue
NPCs/pets, orange monsters.

![GUI map tab](assets/gui-map.png)

## Bot loop

`_bot_tick` runs on the GTK main loop every 200 ms:

1. **Stand up** if `WorldState.resting` is true and the char id is known:
   `rest 1 1 <charId>` (the server toggles sit/stand; sitting blocks movement).
2. **Auto-attack**, if enabled and the cooldown is over: pick the nearest
   `kind == 3` entity within the range from the own position, send
   `ncif 3 <id>` and the skill template, and set the cooldown.
3. **Walk** the next A* cell: `walk x y 0 10`, then wait up to 3 s for the
   server's `tp`/`mv` for a player at that cell. On confirmation advance; on
   timeout skip the cell (blocked tile or a settling client).
4. Repeat/loop; Stop ends the timer.

The bot only sends packets through the existing `PacketClient`; it never
touches GTK from a worker thread. The engine feeds it the live `tp`, `mv`,
`rest`, `cond`, `su` and `stat` packets.

### Limits

- A `0x00` cell can still contain an object or entity at the pixel level; if
  the server rejects the walk, the bot skips the cell after the timeout.
- The exact `u_s` field order is still unverified. The template is editable;
  capture one live cast (`su 1 <charId> ...` in the log) and adjust it.
- Auto-attack targets `kind == 3` only; the skill vnum must be one the character
  actually has.
- The map id is not always known; use Auto-detect or enter the id from a
  `c_map` packet.
- The server may rate-limit or reject injected walks after many attempts; stop
  the bot and let the client settle.

## Tests

```bash
make -C linux host-test   # 48 cases incl. synthetic NOS, A*, ranking
make -C linux gui-test    # GUI + map surface smoke
make -C linux bot-test    # fake server: bot sends ncif/u_s/walk
```

The bot test sends a fake player at (25,117) on map 20 and a monster at
(25,118); it asserts the engine sent `ncif 3 500`, `u_s 866 3 500` and
`walk 25 118 0 10`, and the server answered each walk with a `tp`.

## Live status

- Map **20** is current; 100 % of 173,503 `mv` samples are on walkable cells.
- The character `14881134` was sitting at (25,117) at the end of the session.
- The map tab renders map 20; the path/A* and bot logic pass the fake-server
  test. The live path loop and auto-attack still need a session with the
  character standing and a valid skill vnum/`u_s` layout.
