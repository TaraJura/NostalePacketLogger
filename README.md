<img width="1914" height="1080" alt="artwork (2)" src="https://github.com/user-attachments/assets/00d546c1-047a-4378-afc7-8c024cc9a556" />

# NosTale Packet Logger

A packet logger and automation engine for the MMORPG **NosTale**. A small DLL
inside the game reads the packets the client has already decrypted, and can
send packets back through the game's own send function. A host outside the game
displays them, logs them, and turns them into a world model.

Two implementations live here:

| Tree | What it is |
|---|---|
| [`linux/`](linux/README.md) | **Native Linux port.** The in-game DLL is still 32-bit Windows code (cross-built with MinGW) because NosTale is; everything around it is native: TCP loopback, Python client/CLI, GTK4 GUI, state engine. |
| [`windows/`](windows/README.md) | **Original Windows implementation**, unchanged. MSVC DLL, C++ injector, C# WinForms GUI, named pipes. |

The packet documentation in [`docs/protocol/`](docs/protocol/README.md) is
shared: the transport differs, the game's packets do not.

## Screenshots

![Map / Bot tab](docs/assets/gui-map.png)

| Live packet stream | One window per game client |
| :---: | :---: |
| ![Packet stream](docs/assets/gui-packets.png) | ![Multi-client picker](docs/assets/gui-picker.png) |

The **Map / Bot** tab draws the A\* route, monsters, portals and the bot
controls. The picker gives each running game client its own window; a client
already attached by another window is disabled. Each row shows the client's
DLL version read from its log, and the window title shows the host version
plus the attached client's DLL version.

## NosBazar

![NosBazar section tabs: Inventory, Relist, Monitor, Items, Configuration](docs/assets/gui-bazar.png)

![NosBazar Relist section: readiness list and compact vnum/tab/amount/floor editor](docs/assets/gui-relist.png)

The fourth tab turns the live packet stream into the player-auction-house
workflow of [`TaraJura/Nostale-NB`](https://github.com/TaraJura/Nostale-NB),
with a **second-level navbar**: **Inventory**, **Relist**, **Monitor**,
**Items** and **Configuration**.

- **Inventory** — lists every slot the client reports (Main/Etc, Equip, and
  the extra equipment bags 6/7) with the Item.dat name, bag/slot, vnum,
  quantity, equipment fields, NPC price and known ND cost. Search by name or
  vnum; **Set up relist…** adds one checked item with its exact vnum/tab/amount
  and opens Relist; **Add selected** adds checked items; **Add all** adds
  every distinct item; **Price all** also starts Monitor.
  Equipment quantity 0 counts as one; bound negative vnums are searched by
  their absolute template but cannot be relisted. If the server's login dump
  was missed, the list is empty or marked **partial** — relog once with the
  window open and it fills automatically; the last complete inventory is
  cached per character, so later attaches survive another log truncation.
- **Relist** — select an item (or arrive from Set up relist…), set its
  **Item vnum**, **Tab**, **Amount** and a positive **Min price**, and the
  list marks it **ready** / **needs floor** / **monitor only**. Fill
  **Character** with your own name, tick **Allow live re-listing**, then
  **Relist Start**. Each pass undercuts the cheapest other listing by one
  gold, never below the floor; the status reports `register sent (no ack)`
  because the server sends no confirmation for `c_reg`.
- **Monitor** — starts/stops searches, opens the Bibi Basar dialog through
  the client's own send path, learns the map/session NPC id and dialog option
  from your own opening (or the DLL log), and searches at the required 2.5 s
  pacing. Timed-out items get one retry; the next refresh follows the
  configured interval. Results show the cheapest listing, seller, price,
  profit (gold per NosMall ND or NPC-gold markup) and status, sorted **most
  profitable first**; thin rules keep each row aligned, and **Only priced**
  hides unanswered rows.
- **Items** — the full editor for one item: **Name**, **Search vnums**,
  **Raw packet**, **Item vnum**, **Tab**, **Amount**, **Min price**, **Cost**
  and the advanced `c_reg` fields. **Import NosMall…** adds catalog groups
  with a known ND cost (pets/fairies/partner supplies excluded), **New** and
  **Delete** edit the list.
- **Configuration** — **Bibi id**, **Refresh (s)**, the per-client JSON path,
  **Save config** and **Reload**.
- **Copy names** — right-click an item row or result row, or use **Copy** next
  to **Name**.
- **Per client** — each game window keeps its own configuration at
  `~/.config/ntpl/bazar-<winpid>.json`; selections are saved when the item
  list changes and on shutdown.

The packet formats and state machine are documented in
[docs/bazar.md](docs/bazar.md) and [docs/protocol/bazaar.md](docs/protocol/bazaar.md);
the catalog/database side is in [docs/database.md](docs/database.md).

## Quick start on Linux

```bash
sudo apt install g++-mingw-w64-i686          # once
make -C linux all test-host                  # DLL + injector + test fixtures
make -C linux host-test                      # 315 Python unit tests
make -C linux speed-test                     # DLL speed-boost parsing and walk clamp
make -C linux gui-test                       # headless GTK4 smokes (xvfb)
make -C linux bot-test                       # 41 fake-server bot smokes

# no game required: x86 hook stubs + DLL lifecycle + TCP + command path
linux/scripts/dev-test-wine.sh 25

# with the game running in its own prefix:
linux/scripts/inject.sh        # optional: the GUI can inject from its picker
linux/scripts/host.sh status
linux/scripts/host.sh monitor
linux/scripts/gui.sh           # one window per client; run again for another

# agent access (MCP over stdio; owns the DLL link, GUI joins on 47900)
PYTHONPATH=linux/host python3 -m ntpl mcp
linux/scripts/gui.sh --port 47900
```

## Quick start on Windows

```powershell
msbuild windows\PacketLogger.sln /p:Configuration=Release /p:Platform=x86
```

Run `Injector.exe` as administrator, then open `PacketLoggerGUI`.

## Documentation

Start with [docs/architecture.md](docs/architecture.md), then:

- [docs/engine.md](docs/engine.md) — the Python host API, state model, CLI
  commands and action confirmations
- [docs/map.md](docs/map.md) — `NStcData` maps, walkability, 8-direction A*,
  the Map/Bot tab, path loop, auto-attack and tooltips
- [docs/bot-config.md](docs/bot-config.md) — monster target filters,
  conditional spell rules and JSON export/import
- [docs/family.md](docs/family.md) — the **Family** tab: `%Familyinvite`
  packet, server confirmation and live test accounts
- [docs/bazar.md](docs/bazar.md) — the **NosBazar** tab: inventory scan,
  cheapest-listing monitor with gold/ND and NPC markup, plus floor-protected
  relisting (ported from `TaraJura/Nostale-NB`); packet formats in
  [docs/protocol/bazaar.md](docs/protocol/bazaar.md) and
  [docs/protocol/inventory.md](docs/protocol/inventory.md)
- [docs/database.md](docs/database.md) — local SQLite **game + packet
  database** built from the client archives and public NosTale projects;
  includes the full NosMall catalog and an import picker in the tab
- [docs/mcp.md](docs/mcp.md) — MCP server for agents: state, movement,
  combat, raw packets, and the local broker that lets the GUI watch too
- [docs/handoff-2026-11-06.md](docs/handoff-2026-11-06.md) — current state and
  the exact post-reboot reinjection + auto-attack test procedure
- [docs/live-session-2026-11-06.md](docs/live-session-2026-11-06.md) — what was
  verified on the running client, the bugs fixed and the open items
- [docs/feature-review-2026-11-06.md](docs/feature-review-2026-11-06.md) —
  feature inventory, test coverage and live combination results
- [docs/hooking.md](docs/hooking.md) — how the detours, patterns and assembly
  stubs work, and how to repair them after a client patch
- [docs/ipc.md](docs/ipc.md) — the TCP message protocol
- [docs/linux.md](docs/linux.md) — Wine, MinGW, host, GUI, tests
- [docs/windows.md](docs/windows.md) — the original MSVC/C# tree
- [docs/development.md](docs/development.md) — layout, conventions, adding packets
- [docs/roadmap.md](docs/roadmap.md) — phases and next tasks
- [docs/troubleshooting.md](docs/troubleshooting.md) — symptom → cause → fix
- [docs/reusable-projects.md](docs/reusable-projects.md) — MIT-licensed upstream
  Gameforge/NosTale projects we can reuse (clientless login, launcher
  emulation, protocol crypto, widget layouts)

## Status

The Linux port builds, its 315 unit tests pass, the five GTK4 window smokes
and 41 bot smoke tests pass, and the real injector has been run against
the **live NosTale client** under Wine 11:

- both hooks `ACTIVE` (`recv=0x004DBB4D send=0x004EB5C4 tnt=0x0055AA84`),
  `drops recv=0`;
- `rest 1 1 14881134` stood the character up; the server answered
  `rest 1 14881134 0`;
- movement calls the client's own MoveTo through the DLL's `MOVE|x y`; the
  client pathfinds, animates and sends its own `walk` packet. `POS` reads its
  local position, char id and sit flag (so a host attaching while sitting
  stands up before walking). Injecting a raw `walk` (checksum `1 if (x+y)%3 == 1`) moves
  only the server, and the correction teleports the client;
- the live GUI bot walked `(26,133)→(23,130)` as six client `[SEND] walk`
  packets, with no injected walks, probes or teleports;
- auto-attack selects the monster in the client (`TARGET|id`, so the client
  highlights it and sends its own `ncif 3 <id>`) and then sends
  `u_s 0 3 <id>`; the server answers with `su ... 866/868 ...`, and
  pickup/ground drops are confirmed with `get`. Live 2026-11-07:
  `TARGET|8684` → client `[SEND] ncif 3 8684` → `su ... 3 8684 855 ...`;
- the skill hook captures the client's own `u_s` packets (`skill=ACTIVE`).

`linux/scripts/host.sh walk X Y` uses `MOVE` and waits for the client's own
`walk` packet. The character must be **standing**: sitting blocks movement and
is controlled by the `rest` packet. Some targets are still ignored (blocked
tile/entity); the bot stops after three rejected steps.

The **NosBazar** tab (fourth page) is described in
[NosBazar](#nosbazar) above: its second-level navbar splits Inventory scan
(with Set up relist…), Relist setup, Monitor results, the full Items editor
and Configuration. Each client keeps its own
`~/.config/ntpl/bazar-<pid>.json`; see [docs/bazar.md](docs/bazar.md).

The **Family** tab (fifth page) invites members with the client's own
`%Familyinvite <name>` command, mirrors the same command when it is typed in
game, and shows the server's `infoi2 234` confirmation. **Auto-invite** scans
map players whose family state is known and empty, invites each name once
(nearest first), waits out the server's 15 s invite cooldown with a visible
countdown, and keeps an editable persisted list so a decline is never retried.
An **Only players with a fairy** filter excludes fresh alt accounts; the map
list labels each candidate fairy / no fairy / fairy unknown.
Live-tested on `redenkor` and `sjkrimer`; see
[docs/family.md](docs/family.md).

The client's own map grids are parsed from `NostaleData/NStcData.NOS`; live
maps so far include **2, 3, 4, 20, 98, 103** and **104**, and every observed
entity position is on walkable cells.
The GTK4 **Map / Bot** tab renders the map (automatic map switch on `c_map`),
takes click waypoints, computes an **8-direction A\*** path (diagonals, no
corner cutting, continuous polyline), and can loop it while auto-attacking
monsters (Retaliate picks the one hitting you), picking up drops, and sitting
to recover below configurable HP/MP % thresholds (after finishing the current kill);
it walks to drops and picks them up before returning to the path. It shows live HP/MP bars plus a red
recovery banner, green/red active toggles, hover tooltips with monster names
from the client's language archive, server-sent portals (`gp`) and Time-Space
entrances (`wp`, with the nosapki stone icon and the client's own "Required
Level N" wording), and an explicit unknown-vnum option for monsters that
spawned before injection. **Block portals** (off by default) drops the
client's own `preq` portal request in the DLL and keeps paths off the warp
tiles, so a farming loop cannot enter another map even from a tile it is
already standing on. **Speed +** (**Malicious** tab, default 0 = normal) adds a
flat bonus to the character's walking speed, the way a NosTale speed buff
works (+1, +2, +3...). The DLL reads the server's `cond` speed, calls the
client's own entity speed setter on the local player actor (the field its
walk builder reads), and clamps the outgoing `walk` speed back to the
server's `cond` value — the server sees a normal walk while the client moves
faster. Live 2026-11-20: base 11, +3 applied (actor 14), every walk packet
stayed 11, no server correction. Scroll the spin as far as you like: on the
first value above the client's own 59 limit the DLL raises that check and
extends the client's speed table, and values past the byte's 255 are emulated
through its top entry. It applies immediately without the bot. If the server
corrects the player, it settles at +1 instead of dropping to 0, or turns off
if even +1 is rejected. **Cast speed** (same tab, a plain on/off switch) works with or without
the bot. Press a skill once and it is repeated every time the server
says its cooldown ended, so the character keeps casting at the
server's fastest rate; press another skill to switch, or turn the
switch off. With the bot running, its spells are sent immediately and
overlap the current cast, and a skill is re-cast on its cooldown-end
packet. The server's own cast time cannot be shortened; same-skill
sends faster than the cooldown are refused.
This is a client hack: the game rules prohibit client manipulation, and bot
vendors themselves label speed hacks high ban risk. A third top-level **Bot log** tab keeps a
timestamped, selectable record of every action (attacks, TARGET selections and
refusals, walks, pickups, recovery, DLL errors) for debugging, and mirrors it
to `~/.config/ntpl/bot-<winpid>.log` once a client is attached (`bot.log`
before that; `NTPL_BOT_LOG` overrides) so it can be tailed
outside the GUI. Out-of-range targets are chased on foot and attacked once
in range, like clicking a monster in game. Targets can be filtered by monster
vnum; ordered spell rules cast by HP/MP conditions; the whole bot configuration
is saved/loaded as JSON. `map` commands inspect/render the grids; `make
bot-test` exercises the loop, rules, spell order, rejection and recovery against a fake
server. See [docs/map.md](docs/map.md) and [docs/bot-config.md](docs/bot-config.md).

Gameforge's terms of service prohibit automation. This is a tool for the
account owner's own client; using it on a live account is their decision and
risk.
