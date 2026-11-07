# Host engine (Python)

The native side lives in `linux/host/ntpl/`. It talks to the injected DLL over
TCP, turns packets into a world state, and provides the action commands
(`walk`, `cast`, `send`) plus the GTK4 UI.

```
PacketClient ──► Message/Packet ──► Engine ──► WorldState
       │                                  │
       ├── CLI (monitor, walk, cast, …)   └── GUI (GTK4)
       └── replay / probes
```

## Layers

| Module | Responsibility |
|---|---|
| `protocol.py` | `TYPE\|PAYLOAD` line protocol, `Message`, `Packet`, `default_port()` |
| `client.py` | TCP connect with retries, line reassembly, send, reader thread |
| `engine.py` | `WorldState` and packet handlers |
| `mapdata.py` | `NStcData.NOS` parser, terrain palette, A*, map ranking |
| `cli.py` | command line actions and confirmations |
| `gui.py` | GTK4 window: packet table + Map/Bot tab |

## PacketClient

```python
from ntpl.client import PacketClient, ConnectionLost
from ntpl.protocol import Kind, Packet

client = PacketClient(host="127.0.0.1", port=47821)
client.connect(retries=30, delay=0.2)      # ~6 s of retries
client.send_packet("walk 25 117 1 10")     # host -> DLL, framed as SEND|
for message in client.messages():          # background reader + queue
    if message.kind in (Kind.RECV, Kind.SEND):
        print(Packet.from_message(message))
```

- `connect()` retries while the game/DLL is starting and raises
  `ConnectionLost` if it never appears.
- `send_raw()` sends one already-framed line; `send_packet()` adds `SEND|`.
- `recv(timeout)` is the single-threaded pull API; `messages()` starts the
  reader thread and yields until the connection closes.
- The DLL accepts **one host at a time**. Do not run `ntpl monitor` and
  `ntpl walk` simultaneously; use the monitor's `/send` command or close it
  first.

## Protocol types

- `Message(kind, payload, raw)` — one wire line. `Message.parse()` accepts
  `\r\n`, ignores blank lines, and raises `ProtocolError` for a malformed line.
- `Packet(direction, text, timestamp)` — a game packet. `.opcode` is the first
  token, `.args` the rest, `.ints(n)` returns the first `n` numeric args or
  `None` when the shape is wrong.
- `Kind`: `RECV`, `SEND`, `STATUS`, `QUIT`.

See [ipc.md](ipc.md) for the transport and message grammar.

## WorldState

| Field | Meaning |
|---|---|
| `map_id` | current map id from `c_map`/`at` |
| `x`, `y` | own position from `walk`/`at`; `position` property |
| `facing` | last movement direction `(dx, dy)`, each -1/0/1; drives the GUI arrow; `None` until the first walk |
| `entities` | `dict[int, Entity]` keyed by **entityId** (never vnum) |
| `drops` | `dict[int, GroundItem]` keyed by drop id: item vnum, x, y, amount, owner char id |
| `portals` | `(mapId, x, y) → destination mapId` learned from real crossings (the cell held when the leaving `c_map`/`mapout` arrived, plus the following `at`) |
| `stats` | `CharacterStats(hp, hp_max, mp, mp_max)` from `stat`; drives the bars and recovery |
| `last_attacker_id` / `last_attacker_at` | last entity that hit the local player and the packet timestamp; Retaliate uses it (5 s window) |
| `chat` | last 200 `(type, id, message)` tuples from `say` |
| `resting` | `True` sitting, `False` standing, `None` unknown; from `rest` |
| `self_id` | own char id from `at`, `cond` (the live client sends it only for the local player), `rest`, or a matched `tp` |
| `target_id` | last `ncif` target |
| `last_skill` | skill vnum from the last `su` |
| `skill_pages` / `skill_list` | learned skills from `ski <page> <vnum>...`; `skill_list` is page-ordered and deduplicated; `u_s` uses a zero-based index into it |
| `skill_learned` | slot → Skill.dat vnum seen in the client's own `u_s <slot>` and the server's answering `su ... <vnum> ...`; needed because `ski` is only sent at login |
| `basic_skill` | the vnum the server reports for `u_s 0` — the equipped SP card's basic attack (Energy Bolt 260 on the live mage) |
| `skill_slot(vnum)` / `skill_vnum(slot)` | map between Skill.dat vnums and `u_s` slots, from `ski` and the learned pairs |
| `combat` | last 100 combat events (`attacker`, `target`, `skill`, raw args) |
| `last_packet` | the most recently fed `Packet` |

`Entity` holds `entity_id`, `kind` (1 player, 2 pet/NPC, 3 monster), `vnum`
(the template id from `in`, 0 for players), `name`, `x`, `y`, `speed`.

## Handlers

| Opcode | Direction | State updated |
|---|---|---|
| `walk` | SEND | own `x,y` |
| `c_map` | RECV | `map_id` from the **second** int, clears entities and drops |
| `mapout` | RECV | clears entities and drops when leaving a map |
| `at` | RECV | `map_id`, own `x,y` (last two of the first four ints) |
| `mv` | RECV | entity `x,y`, kind |
| `drop` | RECV | `drops[dropId]` (item vnum, x, y, amount, owner) |
| `get` | RECV | removes `drops[dropId]` after the server confirms a pickup |
| `in` | RECV | player: name/charId/x/y; NPC/monster: entityId/x/y |
| `out` | RECV | removes the entity |
| `tp` | RECV | entity `x,y` (the server's authoritative move) |
| `rest` | RECV | `resting` |
| `cond` | RECV | entity `speed` |
| `ncif` | SEND | `target_id` |
| `u_s` | SEND | the last sent `u_s` slot; paired with the next `su` from self to fill `skill_learned` (slot 0 → `basic_skill`) |
| `ski` | RECV | learned skill pages and the ordered `skill_list` used by `u_s` slots; slot 0 is the basic attack |
| `su` | RECV | `combat`, `last_skill` (first five ints); if the target is self, `last_attacker_id`/`last_attacker_at` |
| `stat` | RECV | own HP/MP |
| `say` | RECV/SEND | `chat` |

Parsing is deliberately tolerant: a short packet, a non-numeric field, or an
unknown opcode is ignored, never fatal. The client sends undocumented packets
and will keep doing so after patches.

### Extending

```python
engine = Engine()
engine.on("my_new_opcode", lambda world, packet: ...)
```

A handler takes `(WorldState, Packet)` and may raise `ValueError`/`IndexError`;
`Engine.feed` swallows those so one bad packet cannot kill a monitor.

## Confirmations

Movement is client-authoritative. The host asks the DLL to run the **client's
own MoveTo function**; the client pathfinds, animates and sends its own `walk`
packets, and the engine sees those SEND packets. Packet injection alone cannot
animate a walk:

| Action | Host/DLL command | Confirmation |
|---|---|---|
| move | `MOVE|x y` (client pathfinding) | client's own `walk` SEND, i.e. `world.position` reaches the target; a server `tp`/`mv` also counts |
| position | `POS` | `STATUS\|POS x y charId` |
| raw move | `SEND\|walk x y checksum speed` | probe `walk -1 -1 0 10` → server `tp`; the client may visibly teleport |
| sit/stand | `rest 1 1 <charId>` (toggles) | `rest <type> <charId> <state>` (0 stand, 1 sit) |
| target | `ncif <type> <id>` | no direct reply; used before `u_s` |
| skill | `u_s <skillSlot> <targetType> <targetId>`; slot 0 = the SP card's basic attack; `ski` or paired `u_s`/`su` lines map slots to Skill.dat vnums | `su <attacker> <target> <skill> …` (the basic attack reports the card's skill: 260 Energy Bolt, 866 Holy Attack) |

The raw `walk` checksum is `walk_checksum(x, y)` = `1 if (x + y) % 3 == 1 else
0`; the client's own builder uses exactly this. The client's MoveTo sends a
correct packet automatically. `Engine` mirrors `tp`/`mv` for `self_id` into
`world.x/y`, learns `self_id` from `at`/`cond`/`rest`, and updates `facing`
from the client's `walk` SEND deltas.

The GUI bot and `ntpl walk` use `MOVE`; the bot's status advances when
`world.position` reaches the target. `ntpl send` still fires a raw packet.

## CLI commands

| Command | What it does |
|---|---|
| `monitor [--engine] [--filter] [--log]` | live stream; `/send`, `/filter`, `/status`, `/engine`, `/quit` |
| `walk X Y [--speed] [--timeout]` | `MOVE` through the client, wait for its own `walk` packet |
| `cast <slot> [--target-id] [--target-type] [--stand ID] [--packet]` | send `ncif` + `u_s`, wait for `su` |
| `send <packet…>` | raw packet; exits 1 if the DLL reports a failed send |
| `status` | hook addresses, hook state, drop counters |
| `scan X Y` | find a coordinate pair in game memory (debug) |
| `find VALUE` | find a 32-bit value such as a charId (debug) |
| `read ADDR LEN` | dump up to 256 bytes (debug) |
| `map info ID / render ID OUT / best` | inspect or render `NStcData.NOS` grids |
| `replay FILE` | resend the SEND lines of a capture |
| `gui` | GTK4 window (also `linux/scripts/gui.sh`) |

Exit codes: **0** success/confirmed, **1** no confirmation or a rejected send,
**2** connection/usage failure.

## Examples

Monitor and keep the state:

```bash
PYTHONPATH=linux/host python3 -m ntpl monitor --engine
```

Drive the character:

```bash
linux/scripts/host.sh walk 25 117
linux/scripts/host.sh send "rest 1 1 14881134"   # toggle sit/stand
linux/scripts/host.sh cast 0 --target-id 5160 --target-type 3
```

Engine in a script:

```python
from ntpl.client import PacketClient
from ntpl.engine import Engine
from ntpl.protocol import Kind, Packet

client = PacketClient()
client.connect()
engine = Engine()

for message in client.messages():
    if message.kind in (Kind.RECV, Kind.SEND):
        packet = Packet.from_message(message)
        engine.feed(packet)
        if packet.opcode == "rest":
            print("resting:", engine.world.resting)
```

## Map and bot

`mapdata.py` parses the client's `NStcData.NOS` into `MapGrid` objects
(width, height, one terrain byte per cell). `0x00` is walkable; `astar()` gives
an 8-direction path over walkable cells (diagonal cost sqrt 2, no corner
cutting); `find_map_for_positions()` ranks maps
against live entity coordinates (the 2026-11-06 session identified map **20**,
230x260, with 100 % of 173,503 `mv` samples on walkable cells).
`choose_map_for_positions()` turns that ranking into one answer for the
attach-after-login case: every live entity counts, the tightest perfect fit
wins, and the current map is kept while it is tied for best.

The GUI's **Map / Bot** tab renders the whole current map (automatically
switched from `c_map`/`at`), overlays entities with hover tooltips, accepts
waypoints by click, and draws the 8-direction path as a continuous polyline.
The **Combat** box shows the values the bot actually enforces for the chosen
skill — range, cast time, cooldown and MP from Skill.dat — and Skill `0`
follows the equipped SP card, whose vnum is learned from `u_s 0` + `su`.
**Search** is the target search radius, while the attack itself is capped by
the skill's own range. The **Targets** list shows the monster vnums currently
spawned on the loaded map (plus selected entries marked "not on this map"),
not the 2,795-entry catalog, so the list matches what is around the character.
On startup the GUI **seeds `map_id`, position and char id from the DLL log's
last `at` plus the client's own `walk` lines** (`spawnlog.load_last_position`),
so a window attached after login shows the real map instead of guessing; the
position ranking is only the fallback when the log has no `at`. With no data
the canvas stays blank and the info says "waiting for the game…".
**Portals are learned from crossings**, because the client has no static
portal table: when the leaving `c_map`/`mapout` arrives, the engine remembers
`(map, x, y)`; the following `at` names the destination and the pair is stored
in `WorldState.portals`. `spawnlog.load_portals` backfills the session's
crossings, and the GUI persists them to `~/.config/ntpl/portals.json`
(`NTPL_PORTALS` overrides), drawing them as magenta markers with a
"Portal → map N" tooltip.
The sidebar also owns the bot configuration:

- `monsternames.py` — `vnum → monster name` and skill descriptions from
  `NSlangData_<lang>.NOS`;
- `spells.py` — `NSgtdData.NOS` → `Skill.dat` catalog: active spell vnum, name,
  MP cost, range, cast time, cooldown and target type. The bot resolves the
  Skill.dat vnum to the `u_s` **slot** from `ski`; the GUI shows both;
- `spawnlog.py` — entity vnum/player-name backfill from the DLL session log
  for creatures that spawned before the host connected;
- `botconfig.py` — validated JSON export/import of targets, spell rules and
  combat settings.

The bot loop:

- stand up with `rest` when sitting;
- recover below the configured HP % (sit, wait for full, stand);
- pick up your own/unowned drops in the loot radius (`get 1 <charId>
  <dropId>`);
- evaluate ordered spell rules from the scanned Skill.dat catalog (Always or
  my HP/MP below/above threshold; per-rule cooldown, MP, spell range and cast
  time enforced; monster/self target), then auto-attack the nearest allowed
  monster (Retaliate first) using `ncif` + a configurable `u_s` template;
  skill 0 is the normal attack;
- walk three-cell legs via the DLL's `MOVE|x y`, which runs the client's own
  pathfinding and animation; confirmation is the client's own `walk` packet /
  `world.position`.

The Targets filter applies to both Retaliate and nearest-target selection. See
[map.md](map.md) for the data format and limits, and
[bot-config.md](bot-config.md) for the filter, spell-rule and JSON semantics.

## GUI

`python3 -m ntpl gui` (or `linux/scripts/gui.sh`) opens the GTK4 window:
a packet table with RECV/SEND colours, a filter, a Follow toggle, a raw send
entry, and the Map/Bot tab. Options:

- `--map-data PATH` — `NStcData.NOS` archive (or `NTPL_NSTC`);
- `--log PATH` — DLL packet log used to backfill entity vnums/player names
  (or `NTPL_LOG`; default is discovered inside the Wine prefix).

The Map/Bot tab shows the whole map (auto-switch from `c_map`/`at`), 8-direction
A* path, live entities with hover tooltips, green/red toggles, a recovery
banner, and the Targets/Spells/Configuration sections. The reader thread only
fills a queue; GLib drains it on the main loop, so no GTK call ever runs off
the main thread.

## Design rules

- Tolerant parsing; unknown/short packets are ignored.
- Confirm actions from the server, never assume.
- One host connection at a time.
- The GUI never touches sockets from the GTK thread.
- The core has no third-party dependencies; PyGObject is only for `gui.py`.
