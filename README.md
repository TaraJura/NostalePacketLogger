<img width="1914" height="1080" alt="artwork (2)" src="https://github.com/user-attachments/assets/00d546c1-047a-4378-afc7-8c024cc9a556" />

# NosTale Packet Logger

A packet logger and automation engine for **NosTale**. A small DLL inside the
game reads the packets the client has already decrypted and can send packets
back through the game's own send function; a host outside the game shows them,
logs them and turns them into a live world model you can act on.

| Tree | What it is |
|---|---|
| [`linux/`](linux/README.md) | **Native Linux port**: the in-game DLL is still 32-bit Windows code (cross-built with MinGW); everything around it is native — TCP loopback, Python host/CLI, GTK4 GUI. |
| [`windows/`](windows/README.md) | **Original Windows implementation**, unchanged: MSVC DLL, C++ injector, C# WinForms GUI, named pipes. |

The packet documentation in [`docs/protocol/`](docs/protocol/README.md) is
shared: the transport differs, the game's packets do not.

## The app

The Linux GUI has eight tabs. Each one has a `?` next to its title with the
same explanation the section needs; the pictures below are generated from the
real window against a fake client with real map/item data
(`linux/scripts/readme-shots.sh`), so no account is shown.

### Packets

![Packets tab: live packet stream with filter, raw send and Follow](docs/assets/gui-packets.png)

Every decrypted packet with time, direction, opcode and raw text, newest
first. The filter matches opcode or text; **Send** writes a raw packet through
the DLL (the same path the bot uses); **Follow** keeps the newest row in view;
**Clear** empties only the view — the session log stays on disk.

### Map / Bot

![Map / Bot tab: map, A* route, monsters, portals and the bot sidebar](docs/assets/gui-map.png)

The client's own `NStcData` grids, drawn with the character, monsters, NPCs,
players, drops, portals (`gp`) and Time-Space entrances (`wp`). Click
waypoints and the tab builds an **8-direction A\*** route (no corner cutting);
**Start/Stop** walk it, **Loop** repeats it, **Block portals** keeps a farming
loop off warp tiles. **Follow player** and **Mirror movement & spells** drive a
second client from this one. **Auto-attack** and **Retaliate** pick fights
(target filter by vnum, optional unknown-vnum monsters), **Pickup** collects
drops on the way, and **Auto-recover HP/MP** sits down below the configured
percentages; the whole configuration saves and loads as JSON.

![Map / Bot sidebar: spell rules, loot, recovery and configuration](docs/assets/gui-spells.png)

Spell rules cast by HP/MP condition with their own cooldown and target,
ordered top to bottom; skill data (MP, cast time, range) is read from the
client's own `Skill.dat`.

### Bot log

![Bot log tab: timestamped actions, mirrored to a per-client file](docs/assets/gui-botlog.png)

Every action — target selections and refusals, attacks, walks, pickups,
recovery, bazaar steps, DLL errors — with a timestamp. The same lines are
mirrored to `~/.config/ntpl/bot-<winpid>.log` so they can be tailed outside
the GUI.

### NosBazar

The auction-house workflow of
[`TaraJura/Nostale-NB`](https://github.com/TaraJura/Nostale-NB), with a
**second-level navbar**: **Items**, **Relist**, **Monitor**,
**Configuration**. **Monitor** itself splits into **NosMall** (gold per ND)
and **NPC resale** (gold markup over the NPC price).

#### Items

![NosBazar Items: the Item.dat catalog with Monitor/Relist ticks and the editor](docs/assets/gui-bazar-items.png)

One searchable catalog of **every item in the local Item.dat database**
(about 7,805), plus configured rows the database does not know and
inventory-only vnums. A configured item is listed once, at its own vnum;
its other search vnums still match the search, so an alias cannot be
mistaken for a second item with independent ticks. Each row shows the real
name, vnum, where the item is
in your bags and its known cost, with a **Monitor** tick (search it) and a
**Relist** tick (put it in the relist set; a new row gets its vnum/tab/stack
and floor 1). Select a row to edit Name, Search vnums, raw `c_blist`, Item
vnum, Tab, Amount, Min price, the cost type (which chooses the Monitor
view) and the advanced `c_reg` constants. **Add to Monitor / Add to Relist**
apply to the Ctrl+click selection; the **All items / Monitored / Relisting**
filter under the list narrows the 8,000-row catalog to the items you care
about. **Import…** opens the NosMall catalog, the full Item.dat picker, or
imports a saved bazar config (add or replace); **⋯** holds Rescan/Price
all/Add whole inventory, the bulk Monitor/Relist switches and the selection
actions.

#### Relist

![NosBazar Relist: relist set, readiness badges and the floor editor](docs/assets/gui-relist.png)

The relist set, shown alone by default (**Only relisting** is on). Untick a
row to keep watching it without changing its listing. **Add from Items**
takes the Items selection; **Add…** holds whole inventory and Item
database…; **Actions…** holds select/unselect, remove from relisting, and
relist/stop for the shown or Ctrl+clicked rows. Set the exact
**Item vnum / Tab / Amount**
and a positive **Min price**; the badge
reads `ready` / `needs floor` / `needs vnum/tab`. The character name is
detected automatically (no field to fill) and own listings are matched by
character id, **Relist Start** opens the bazaar
itself (Bibi Basar, the portable NosBazar medal, or the client's own
shortcut), undercuts the cheapest other listing by one gold — never below the
floor — and charges the bazaar's 50-gold minimum listing fee. If you already
have a listing of the item with **nothing sold**, it reprices that listing
with the in-game **Change** action (`c_mod`) instead of adding another; only
when something from your listing has sold does it add a new one. The status
reads `price changed (c_mod)`, `register accepted (rc_reg)` or `register
ignored (no rc_reg)`.

#### Monitor

![NosBazar Monitor: NosMall view, most profitable first](docs/assets/gui-bazar.png)

![NosBazar Monitor: NPC resale markup view](docs/assets/gui-bazar-resale.png)

One search pass at the required 2.5 s pacing over every monitored item,
cheapest listing per item, sorted most profitable first; **Only priced**
hides empty rows and timed-out items get one retry. The left list holds the
monitored set (chosen with the Monitor tick in Items); **Add from Items**,
**Add…** and **Actions…** manage it. The **NosMall** view shows gold per
ND; **NPC resale** shows the gold markup over the NPC price for items bought
at an NPC (Pet Food, Bell of Sweet Home, …). A **search box** above the tabs
filters both views by item name, configured label or vnum. A row only reports
the listings of the item it displays: saved search groups that mix different
items (the partner skill tickets are one NosMall base vnum group) no longer
show a sibling's price. Every row has a **Copy** button for the real item
name (also selectable text / right-click), and the Relist and Items rows
have the same right-click copy.

The Map/Bot **Character** frame shows the detected character name in bold
above the HP/MP bars.

#### Configuration

![NosBazar Configuration: Bibi id, refresh and the per-client file](docs/assets/gui-bazar-config.png)

The Bibi entity id, the refresh interval and the per-client JSON file, with
**Save config**, **Reload** and **Reload names** (re-read item names after a
database rebuild).

Each game window keeps its own configuration at
`~/.config/ntpl/bazar-<winpid>.json`; prices, packet formats and the relist
state machine are documented in [docs/bazar.md](docs/bazar.md) and
[docs/protocol/bazaar.md](docs/protocol/bazaar.md). The monitoring set, the
add/replace config import and the prepared 17-item profitable-NosMall profile
are in [docs/monitoring-config.md](docs/monitoring-config.md).

### Family

![Family tab: invite candidates with family/fairy state and the invited list](docs/assets/gui-family.png)

Invites through the client's own `%Familyinvite <name>` command and mirrors
the same command when it is typed in game. **Auto-invite players with no
family** walks the map's candidates nearest first, waits out the server's
15 s cooldown with a countdown, and keeps a persisted invited list so a
decline is never retried; **Only players with a fairy** filters out fresh alt
accounts. See [docs/family.md](docs/family.md).

### Malicious

![Malicious tab: speed boost and cast speed](docs/assets/gui-malicious.png)

Client-side tweaks that work without the bot. **Speed +** adds flat points of
walking speed through the client's own entity setter while every outgoing
`walk` keeps the server's normal speed (the slip is client-side; the DLL
raises the client's own 59 cap and emulates past the byte's 255). **Cast
speed** repeats the last skill every time the server says its cooldown ended,
and with the bot running the next spell goes out immediately, overlapping the
current cast; the server's cast time and cooldown remain the floor. Both are
game-rule violations — high ban risk, the account owner's decision.

### Minigame

![Minigame tab: Sheep Farm key spam and player-only focus](docs/assets/gui-minigame.png)

Sheep Farm helper: **Start** spams a key press through the DLL at the chosen
interval (default `y`, the secondary attack; `z` on QWERTZ), and **Focus
nearby players** keeps the nearest other player selected through the client's
own selector — never an NPC, pet, monster or the local character, so the
spammed attack lands on a player instead of a sheep. Settings are saved per
client in `~/.config/ntpl/minigame-<winpid>.json`. Needs DLL 0.3.0.

The **Miniland production** frame plays a production minigame — **Great
Quarry** first — by sending the client's own result and claim packets.
**Play until** chooses the reward box (default 5x, the 16,000-point minimum),
the tab shows your production points from the game's own `mlo_info`, claims
with the server's `mlo_lv` reward level and shows the reward item from
`mlo_rw`. **Play** keeps going: it claims, clicks the client's **Stop**
(Storno), waits 40 s (the server's reward cooldown) and starts the next one
until the production points run out, retrying an ignored run and showing a
live **next claim in N s** countdown; **Stop** ends the loop. Open the Great
Quarry window in the game first. It is a packet shortcut, not a visual
play-through: high ban risk. See [docs/minigame.md](docs/minigame.md).

### Stats

![Stats tab: player DPS meter](docs/assets/gui-stats.png)

A player **DPS meter** fed by the decoded `su` damage: name, DPS, damage,
hits, share, last hit and session total, over a 5/10/30/60 s window or the
whole session, sorted by DPS. Partner/monster hits and zero-damage buffs are
ignored; **Reset** clears the totals. It refreshes twice a second while the
tab is visible and keeps collecting in the background. See
[docs/stats.md](docs/stats.md).

### Client picker

![Client picker: one window per game client, with its DLL version](docs/assets/gui-picker.png)

The picker gives each running game client its own window. A client already
attached by another window is disabled, an uninjected client can be injected
from here, and the title shows the host version plus the attached client's
DLL version.

## Quick start

```bash
sudo apt install g++-mingw-w64-i686 xvfb          # once
make -C linux all test-host                       # DLL + injector + 389 unit tests
make -C linux gui-test bot-test                   # 6 GTK4 smokes, 48 fake-server smokes

linux/scripts/inject.sh        # optional: the GUI picker can inject too
linux/scripts/gui.sh           # one window per client; run again for another

# agent access (MCP over stdio; owns the DLL link, the GUI joins on 47900)
PYTHONPATH=linux/host python3 -m ntpl mcp
linux/scripts/gui.sh --port 47900
```

The native `speed-test` and `key-test` check the DLL-side parsers;
`linux/scripts/dev-test-wine.sh 25` exercises the hook stubs and DLL lifecycle
without a game. On Windows, build `windows\PacketLogger.sln` (x86 Release),
run `Injector.exe` as administrator and open `PacketLoggerGUI`.

## Documentation

Start with [docs/architecture.md](docs/architecture.md), then:

- [docs/engine.md](docs/engine.md) — host API, world model, CLI, MCP
- [docs/map.md](docs/map.md) — maps, A\*, Map/Bot tab, path loop
- [docs/bot-config.md](docs/bot-config.md) — target filters, spell rules
- [docs/bazar.md](docs/bazar.md) — the NosBazar tab end to end
- [docs/family.md](docs/family.md) · [docs/minigame.md](docs/minigame.md) ·
  [docs/stats.md](docs/stats.md) · [docs/taming.md](docs/taming.md) — the
  other tabs and the Pet Trainer tamer
- [docs/database.md](docs/database.md) — the SQLite game/packet database and
  the NosMall catalog
- [docs/autonomous-play.md](docs/autonomous-play.md) — observe → decide → act
  → confirm → survive, GUI↔MCP handover
- [docs/hooking.md](docs/hooking.md) — detours, patterns, assembly stubs
- [docs/ipc.md](docs/ipc.md) — the TCP message protocol
- [docs/troubleshooting.md](docs/troubleshooting.md) — symptom → cause → fix
- [docs/linux.md](docs/linux.md) · [docs/windows.md](docs/windows.md) —
  per-tree setup
- [docs/development.md](docs/development.md) — layout, conventions, tests

## Status

Host **0.9.13** (Linux), DLL **0.3.3**: 389 unit tests, 6 GTK4 window smokes
and 48 fake-server bot smokes pass, and the real injector has been run against
the live NosTale client under Wine 11 — both hooks `ACTIVE`, movement through
the client's own MoveTo, auto-attack confirmed by the server's `su`, and the
bazaar open/relist/registration verified live (2026-11-23); the DPS meter
decodes the same live `su` damage. The Great Quarry minigame was verified
live on 2026-11-26: the natural start/play/claim flow, the automated claim
(a reward without playing) and the Play loop with its 40 s reward cooldown,
the Stop between runs and the points-exhaustion stop. Speed + keeps the
requested value through respawn/mount/resync position corrections (live
2026-11-26; only a correction burst settles it), leaving a raid team
unblocks the Minigame Play immediately (the server's `raid 1 0` state), and
the new **Pet tamer** (Map/Bot) automates the Pet Trainer card: it whips a
Chicken down and Tames it, waits out the 15 s cooldown and repeats until the
server's pet slots are full. The original Windows tree is unchanged.

Gameforge's terms of service prohibit automation and client manipulation.
This is a tool for the account owner's own client; using it on a live account
is their decision and risk. No detection-evasion features.
