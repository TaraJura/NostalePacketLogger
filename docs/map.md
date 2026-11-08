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
`c_map`/`at` packets (`WorldState.map_id`; the id is the second `c_map`
field). If the host attached after login and has not seen one yet, the UI
scores every grid against the live entity positions
(`choose_map_for_positions`). Several maps — often a pair sharing one layout —
can fit the same handful of positions perfectly, so the ranking prefers the
**tightest** perfect fit (the smallest grid that holds every point) and keeps
the current map while it is tied for best. All live entities count, not only
players: monsters and NPCs are on the current map too, and their wider point
cloud is what separates same-layout maps. Only the local player and one nearby
player fit maps 300 and 5613 equally well; with the monsters included the real
map 2 wins outright.

## Parser API

`linux/host/ntpl/mapdata.py` (standard library only):

| Function | Meaning |
|---|---|
| `load_nstc(path)` | parse the archive into `{map_id: MapGrid}` |
| `MapGrid.at(x, y)` | terrain byte or `None` |
| `MapGrid.is_walkable(x, y)` | `at() == 0x00` |
| `MapGrid.neighbours(x, y)` | walkable 8-direction neighbours; diagonals require both orthogonal cells to be walkable |
| `astar(grid, start, goal, blocked=None)` | shortest 8-direction path (diagonal cost sqrt 2), goal included; `blocked` adds temporary obstacles such as map portals |
| `find_map_for_positions(grids, points)` | rank maps for live coordinates |
| `choose_map_for_positions(grids, points, current_id)` | pick one map: tightest perfect fit, keep a tied current |
| `terrain_rgb(value)` | palette colour |
| `load_portals(path)` / `save_portals(portals, path)` | persisted learned portals (`(mapId, x, y) → destination`), `NTPL_PORTALS` overrides the default `~/.config/ntpl/portals.json` |

Override the archive path with `NTPL_NSTC` or `--file`.

## Monster names

The displayed name needs two client tables, and the record ids in them are
**not** the monster vnum.

`NSgtdData.NOS` in the same `NostaleData` directory contains `monster.dat`,
whose records map the template vnum to a language key:

```
VNUM	419
NAME	zts489e
```

`NSlangData_<lang>.NOS` holds the text tables. The monster table is
`_code_uk_monster.txt` (the language suffix follows the installed client) and
maps that key to the localized name:

```
zts489e	Violent Seedle
zts490e	Crazy Dander
zts491e	Violent Chicken
```

The key is its own id space: vnum 419 uses key `zts489e`, while the text
record `zts419e` is "Wheat". Reading the language table by vnum alone gives
wrong names — the live map 103 shows Violent Seedles, not Wheat.

The archive layout:

```
count       uint32
entry       count times:
              number       uint32
              name_size    uint32
              name         name_size bytes
              is_dat       uint32
              data_size    uint32
              data         data_size bytes, encoded
```

`.txt`/`.dat` entries use the client's nibble codec: a control byte carries a
count; when bit 7 is set the low/high half-bytes index a 16-character alphabet,
otherwise the following bytes are XOR `0x33`; `0xFF` emits a carriage return.
`monsternames.py` implements the archive readers and the codec, then joins
`monster.dat` (`{vnum: key}`) with the text table (`{key: name}`) into
`{vnum: name}` (2,679 entries in the current UK client). `NTPL_NSLANG` selects
an explicit language archive and `NTPL_NSGTD` the game-data archive; otherwise
both are looked up next to `NStcData.NOS` (`NTPL_NSTC`), preferring `UK`, then
any other language. A missing archive yields an empty table and the UI falls
back to the entity id.

Important: the `in 3 <vnum> <entityId> <x> <y> ...` spawn packet carries the
template vnum first and the per-session entity id second. The map's `ncif`,
`u_s` and `su` packets use the **entity id**; tooltips resolve it to the name
through the stored vnum. For example, live `in 3 206 5122 ...` is entity 5122,
a Mountain Turtle (vnum 206 → `zts200e`).

### Entities that spawned before the host attached

The host only receives packets after it connects. A monster that was already
alive is first seen as `mv`/`tp`, which carries no vnum, so its tooltip used to
fall back to the entity id. The DLL's packet log covers the whole client
session (it is truncated when the DLL is injected), so `spawnlog.py` scans its
`[RECV] in ...` lines at GUI startup and fills `entity id → vnum` plus player
names. Later log entries override earlier ones, which handles a reused entity
id.

`spawnlog.py` looks for `C:\NosTalePacketLog.txt` inside `$WINEPREFIX`, then
the dedicated prefix and the Gameforge prefix on this machine. Override it
with `NTPL_LOG=<path>` or `ntpl gui --log <path>`. Missing logs are harmless:
the GUI simply keeps the activity from the live packet stream.

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

`linux/scripts/gui.sh` opens the window. The **Map / Bot** tab is split into a
map on the left and a scrollable control sidebar on the right. The layout is
grouped by function:

- **Map bar** — only the live map info (id, size, walkable %). There is no Map
  ID/Load/Auto-detect/Scale/Center control: the map is selected automatically
  and uses a "contain" fit: the **whole map** is scaled to the largest size
  that fits the widget, with its aspect ratio fixed. The letterbox area is a
  dimmed copy of the same map, so it reads as part of the map instead of black
  bars. On a portal crossing the server sends `c_map 0 <oldMap> 0` →
  `mapout` → `at <charId> <newMap> …` → `c_map 0 <newMap> 1`; the GUI switches
  on the `c_map`/`at` pair as soon as the packet arrives (the 2 s auto-map
  tick remains only as a fallback). Note that the map id is the **second**
  `c_map` field — the first is a type. A GUI started mid-session seeds the map
  from the DLL log's last `at`. On entry the server sends the map's whole
  portal list as `gp <x> <y> <toMap> …`; those are drawn as rings with a
  "Portal → map N" tooltip and persisted in `~/.config/ntpl/portals.json`.
  **Block portals** (Path section, on by default) treats those cells as
  obstacles — A* routes around them, a click on one is refused and every
  `MOVE` is checked against them — and the DLL drops the client's own `preq`
  portal request, so a farming loop cannot change maps even from a tile it is
  already standing on. See
  [bot-config.md](bot-config.md#block-portals--stay-on-one-map).
  Right after `gp` the server sends the map's **Time-Space entrances** as
  `wp <x> <y> <tsId> <flag> <minLv> <maxLv>`; each is drawn as the nosapki
  Time-Space stone in a gold frame, with the client's own tooltip wording:
  "Time-Space Stone / Required Level 26".
  Live map 4 has all eight: TS 16, 17, 18, 19, 20, 21, 22 and 704; map 3 has
  TS 5, 6, 10, 11, 12, 13. A `gp` destination in the 2xxx range is a
  Time-Space **instance map** gate (e.g. map 4's `(224,103)→2110`,
  `(228,53)→2510`) and stays an amber ring. The icon is
  `linux/host/ntpl/assets/ts-1.png` (source:
  `nosapki.com/images/nt-images/ts-1.png`); if the asset is missing the
  marker falls back to an amber ring. A crossing is the fallback when a
  `gp` list was missed.
- **Character** — live HP and MP bars with current/max/missing values, plus
  map/position/char id. HP and MP are fed by the server's `stat` packets.
  While recovering, a red **RECOVERING** banner states the HP percentage and
  that attacks/movement are paused; all toggle buttons are green when active
  and red when off.
- **Path** — path summary, Start, Stop, Clear path, Loop and **Draw path**.
  With Draw path off (default), a left click makes the character **walk to
  that cell** along the A* path in short three-cell `MOVE` legs — the same
  legs the bot uses, because a single far MoveTo makes the client jump (and
  the server correct it). If sitting, it stands first. Stop, Clear path or a
  map change cancels the walk. With Draw path on, a click instead adds a
  waypoint and draws the A* route the bot will follow; Start then runs the
  bot along it. While the bot is running, clicks add waypoints regardless.
  The own marker follows the DLL's real player position (`POS`, polled twice
  a second), not the walk packet's destination, so the dot tracks the
  actual movement instead of jumping ahead.
- **Combat** — Auto-attack and Retaliate, plus one info line with the
  detected basic skill's Skill.dat range/cast/cooldown/MP (slot 0 follows the
  equipped SP card; the server reports its vnum for `u_s 0`). There are no
  Skill / Search / Min delay / template controls: the bot always uses slot 0,
  the skill's own range and cooldown, and acquires targets within 15 cells.
- **Targets** — monster filter: all monsters, only the selected templates, or
  all except the selected ones. Search the client's monster table by name or
  vnum, or add a vnum manually. **Also attack monsters with unknown vnum**
  covers monsters that spawned before the DLL was injected and therefore
  have no `in` packet.
- **Spells** — **Add spell…** opens a searchable catalog scanned from
  `NSgtdData.NOS` → `Skill.dat` (name, vnum, MP, target, cooldown). Rules are
  ordered 1., 2., … and can use **Always**, My HP % or My MP %; the first rule
  that is off cooldown, in range and affordable wins. Always + cooldowns forms
  a rotation. Otherwise the equipped SP card's basic attack (slot 0) is the
  fallback.
- **Loot** — Pickup. The bot walks to eligible drops up to 20 cells away,
  picks them up when adjacent, then resumes the path.
- **Recovery** — Auto-recover HP/MP and the low-HP/MP thresholds.
- **Configuration** — Save/Load the whole bot configuration as JSON.

See [bot-config.md](bot-config.md) for the target filter, spell-rule semantics
and the export/import schema.

The layout is responsive: wide windows show map + sidebar side by side; below
~1050 px the map moves above the controls. The map redraws are coalesced at
~80 ms instead of one redraw per packet. Right-click removes the last waypoint;
left-click walks there in short legs, or adds a waypoint when Draw path is on.
The own marker is driven by a 2 Hz `POS` poll of the client's real position.
The packet **Follow** toggle lives in the Packets tab, not in the window header.

| Control | Effect |
|---|---|
| Auto-attack | attack the nearest allowed monster (Retaliate priority) with the equipped SP card's basic skill (slot 0); no skill/range/delay knobs — Skill.dat drives the pace |
| Retaliate | attack the monster that most recently hit you first, if in range |
| Auto-recover HP | below the HP threshold, sit and wait for full HP, then stand (after finishing the current kill) |
| Below HP % | HP recovery threshold (default 70) |
| Auto-recover MP | below the MP threshold, sit and wait for full MP, then stand (after finishing the current kill) |
| Below MP % | MP recovery threshold (default 70) |
| Pickup | walk to your own/unowned drops up to 20 cells away, pick them up when adjacent, then resume the path |
| Loop | repeat the path when it finishes |
| Targets | all / only selected / all except selected monster templates (`vnum`); Retaliate respects the same filter |
| Spell rule | scanned spell + Always/My HP/My MP + below/above + cooldown + Monster/Self target; ordered top to bottom; static MP/range/cooldown checks from Skill.dat |
| Save/Load config | export/import the toggles, numbers, target list and spell rules as JSON |

The A* path is drawn as one continuous cyan polyline through cell centres.
Diagonal steps make it read as a direct line instead of a staircase, and
corner cutting is rejected so the line never crosses a blocked corner.

Hovering a map marker shows a tooltip: **You** with char id, position and map;
a **Player** with name, char id, position and distance; a **Monster/NPC** with
name, entity id, template vnum, position and distance (plus “attacking you” for
the last attacker); and a **drop** with item vnum, amount, drop id, owner and
distance. Monster names come from the client's own language data (see below);
when a vnum is not in that table the tooltip falls back to the entity id.

Overlays: cyan path, yellow waypoints, **gold diamonds for drops**, green
players, blue NPCs/pets, orange monsters. The own character is a **bigger red
dot with a white ring and a red facing arrow**. The arrow follows the last
`walk` delta (map coordinates: `+x` right, `+y` down), so it has eight
directions. The protocol has no own-facing packet: `in` carries a direction
only at spawn, and every observed character kept one constant spawn direction
regardless of movement. Deriving the arrow from movement is therefore the
honest source.

![GUI map tab](assets/gui-map.png)

## Bot loop

`_bot_tick` runs on the GTK main loop every 50 ms:

1. **Recovery** — if Auto-recover HP/MP is on and HP or MP is below its
   threshold, the bot first finishes the monster it is currently hitting (if
   the target is alive and still in range); only then does it stop the walk,
   sit (`rest 1 1 <charId>` unless already sitting) and wait. No attack,
   pickup or movement happens until every resource that triggered recovery is
   full; then the bot stands (`rest`) and resumes. `stat` is the source of
   HP/MP.
2. **Stand up** if `WorldState.resting` is true and the char id is known:
   `rest 1 1 <charId>` (the server toggles sit/stand; sitting blocks movement).
3. **Pick up** if enabled and a drop owned by you (or unowned) is within
   20 cells: walk to it with the same client MoveTo legs, send
   `get 1 <charId> <dropId>` once adjacent, then resume the path. The server
   rejects `get` from far away, so the walk is required. Each drop is tried
   at most three times; the server's `get` confirmation removes it from
   `WorldState.drops`.
4. **Auto-attack / clear before moving.** Combat takes priority over the
   path: while any allowed monster is within attack range the bot stays and
   keeps attacking (the skill's cast time and cooldown pace it); when none is
   left it takes the next path leg. With no path, Start runs stationary
   combat, pickup and recovery. Targets are acquired within 15 cells; a
   monster outside the skill's own range is chased on foot (one A* leg at a
   time) until it is in range — the game's click-a-monster behaviour.
   Target choice: with **Retaliate** on, first try the most recent `su`
   attacker that hit you (within 5 s); otherwise pick the nearest `kind == 3`
   entity within range. Both paths apply the **Targets** filter.
   A killed monster gets no `out` packet: the server's `st` status packet
   (current HP reaches 0) removes it from the entity list immediately, so the
   bot picks a live monster instead of attacking the corpse. A `TARGET`
   refusal is the fallback removal.
   Spell rules are evaluated before the basic attack in list order: the first
   enabled rule that is off its per-rule cooldown, has enough MP, has the
   target in spell range and whose condition (Always or HP/MP threshold) is
   true is cast on the monster or on yourself. **Always** plus cooldowns is a
   rotation. If none matches, the equipped SP card's basic attack (slot 0) is
   used. Monster casts first select the monster in the client
   (`TARGET|id`, which makes the client send its own `ncif 3 <id>`), then send
   `u_s` in the same tick: the DLL handles both commands in order, so the
   selection is applied first. A refusal arrives asynchronously and the
   entity is dropped from the world state. Self casts use target type 1 and
   the char id. The rule stores the Skill.dat
   vnum; the bot resolves it to the zero-based `u_s` slot from the learned
   `ski` list (e.g. 876 → slot 10) before sending. Cast time from Skill.dat
   extends the walk pause.
5. **Walk** a multi-cell leg (three cells from the A* path, plus the final
   cell on the 8-direction path) by calling the client's own MoveTo: the GUI
   sends `MOVE|x y` to the DLL. The client pathfinds,
   animates and sends its own `walk` packet with
   the leg destination; the server accepts it as a normal player move. The
   bot polls `POS` during the leg and hands the next leg to the client one
   cell before the current one ends (at least 0.35 s after the leg started),
   the same early-handoff rule as click-to-walk, so the character keeps
   walking instead of stopping at every three-cell boundary. The client's
   own `walk` packet arrives at the **start** of the animation, so the old
   `0.45 s`-per-Euclidean-cell wait remains only as the fallback when `POS`
   is unavailable.
   On a timeout it skips the leg (blocked path) and stops after three
   consecutive rejections.
6. Repeat/loop; Stop ends the timer.

The bot sends ordinary packets (`u_s`, `rest`, and `ncif` for non-monster
targets) through the existing `PacketClient` and uses the DLL's
`TARGET`/`MOVE`/`POS` commands for targeting and movement. It never
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
  another player's drops are skipped. The bot walks to drops up to 20 cells
  away, picks them up when adjacent, and then resumes its path.
- The verified `u_s` prefix is `<skill> <targetType> <targetId>`; later
  fields are unknown. `u_s 0 3 <id>` is the normal attack and skill 10 cast
  produced `su ... 876` live. Self-target rules use `u_s <slot> 1 <charId>`;
  live `u_s 7 1 14881134` → `su ... 873` (Group Healing). The template is
  editable.
- Auto-attack targets `kind == 3` only, within **Range**. The actual attack
  range is also capped by the skill's scanned Skill.dat range: the basic
  attack (866) has range 11, so a target farther away is **not attacked**
  even if the UI Range is larger. Out-of-range casts only play the client
  animation; the server rejects/self-targets them. The status bar explains
  the distance. Use a longer-range spell rule or walk closer. Skill **0** is
  the normal attack; the server reports the basic skill (866/868) in `su`.
  Skill 866 itself is not castable and gets `cancel 0 866 -1`.
- The Targets filter compares the monster **template vnum** (from `in`, with
  the DLL-log backfill). In "only selected" mode an entity whose vnum cannot
  be resolved is skipped; in "all except selected" it is allowed because it
  cannot be on the exclusion list.
- Spell rules require **Auto-attack** to be on. The recovery check runs first:
  below an Auto-recover HP/MP threshold the bot sits, so a heal rule with a
  lower threshold never fires while recovery is active. Self-target rules use the
  current char id and send `u_s <spell> 1 <charId>`.
- The spell catalog comes from `NSgtdData.NOS` → `Skill.dat`; names/descriptions
  come from `_code_<lang>_Skill.txt` through the record's `NAME` key. The
  `u_s` field is a zero-based slot in the learned skill list from `ski`, not
  the Skill.dat vnum: live `u_s 10` → `su ... 876`, `u_s 7` → `su ... 873`.
  The bot resolves vnums to slots using the live list or the DLL-log backfill. MP cost,
  range, cast time and cooldown are static game data and are enforced before a
  rule is selected; the rule's own cooldown can only make it stricter. A
  spell whose target type is self overrides the rule's Monster/Self choice.
- Skill.dat also contains monster/system records. The picker filters to active
  skills (`TYPE[0] == 1`) and hides empty/NotUse names; a manual vnum can still
  reference any id.
- The map id comes from `c_map`/`at`. If the host attached after login and has
  not seen either yet, the GUI ranks the map grids against **all** live entity
  positions every 2 s (`choose_map_for_positions`): the tightest perfect fit
  wins, and a current map tied for best is kept to avoid flipping. A map id the
  archive has no grid for (instance maps such as 20651) is reported as
  "map N has no grid" instead of showing a different map's grid.
- The server may rate-limit or reject moves after many attempts; the bot stops
  after three consecutive unconfirmed moves.

## Tests

```bash
make -C linux host-test   # 142 cases incl. synthetic NOS, 8-way A*, checksum, config
make -C linux gui-test    # GUI + map surface + tooltips + unknown-position guard
make -C linux bot-test    # 16 fake-server smokes: combat, rules, spell order, out-of-range, rejection, HP/MP + finish-kill recovery, loot approach, portal blocking, retaliate-only defence, far-goal leg chopping, death/revive dialog, ghost-target dropping, target focus, focus+retaliate
```

The bot test sends a fake player at (25,117) on map 20, a monster at
(25,118) and a drop at the player's feet; it asserts the engine sent
`get 1 14881134 999`, `TARGET|500`, `u_s 0 3 500` and `MOVE|26 119` (not an
injected walk), and the fake client answered with its own `walk`. A second
test makes the server silent and asserts the bot stops after three rejected
moves. The third test sits the bot at full HP to verify the sit/stand recovery
loop. The rules smoke presents two monsters (vnum 201 nearer, vnum 200
farther), allows only vnum 200, sets HP to 40 % and a spell rule (spell 7, my
HP below 50 %), then asserts `TARGET|500` + `u_s 7 3 500`, no traffic to 501,
no basic attack, and that save/load restores the config. The out-of-range
smoke sets UI Range 40 with a monster 35 cells away and asserts the bot
selects it (`TARGET|5087`), sends a `MOVE` toward it, sends no `ncif`/`u_s`
from where it stands, and reports “chasing monster 5087”. See
[bot-config.md](bot-config.md).

## Live status

- Live maps on 2026-11-07: **98** Comet Meadow (50x45), **103** Deep Cave
  (55x40) and **104** (17x15); earlier that day map 2/1, and map 20 on
  2026-11-06 (100 % of 173,503 `mv` samples on walkable cells there).
- Portals are server-sent: entering a map sends `gp <x> <y> <toMap> …` for
  every portal of that map. 2026-11-07: map 103 has `(20,20)→104` and
  `(42,32)→3`; map 3 (Mine Plains) has `(10,208)→6`, `(19,7)→7`,
  `(45,12)→103`, `(156,2)→4` and `(178,189)→2`; map 104 has `(11,9)→103`.
  The GUI stores, draws and persists the whole list (`WorldState.gates`);
  crossing learning remains the fallback when a `gp` list was missed.
- Time-Spaces are server-sent too: `wp <x> <y> <tsId> <flag> <minLv>
  <maxLv>` follows the `gp` list. Map 4 has the complete set (TS 16, 17, 18,
  19, 20, 21, 22, 704) at the golden-circle positions, matching nosapki's
  list and level ranges; map 3 has TS 5, 6, 10, 11, 12, 13. They are drawn
  with the nosapki stone icon; the tooltip uses the client's own wording,
  "Time-Space Stone / Required Level N", with no position or id. No guessing
  or manual entry is involved.
- The basic skill follows the equipped SP card: `u_s 0` was answered with
  `su ... 260 ...` (Energy Bolt, range 6/cast 0.8 s/CD 0.5 s/0 MP) and later
  with `su ... 855 ...` (Fire Bolt, 40 MP) after a card change; the Combat box
  follows the new Skill.dat record automatically.
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
- Map switching is live-verified from the portal capture 20 ↔ 85:
  `c_map 0 20 0` → `mapout` → `at ... 85 ...` → `c_map 0 85 1`, and back.
  The GUI follows the second `c_map` field immediately.
- Monster names are mapped live (corrected 2026-11-07 through `monster.dat`):
  e.g. entity 5032/vnum 200 (Wolf), 5068/201 (Brutal Wolf), 5122/206
  (Mountain Turtle), 5161/223 (Small Wasp). Tooltips show the name, entity id
  and vnum.
- Pathing is now 8-direction A* with diagonal steps and a continuous polyline.
  The headless tests cover the diagonal shortcut and corner-cut rejection;
  the client's MoveTo still performs the actual movement, so watch the first
  live diagonal run.
- Target filters, spell rules, JSON config and the recovery banner/toggle
  colours are covered by the headless GUI/rules smokes. The live client
  confirmed the reason “only selected” can look idle: recovery was sitting at
  24 % HP, and pre-injection wasps had no vnum in the log (the unknown-vnum
  option now covers them).
