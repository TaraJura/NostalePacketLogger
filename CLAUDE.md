# CLAUDE.md

Working context for Claude Code in this repository. Keep it current: fix a line
that stopped being true instead of appending a new one. Never write account
names, passwords, session data or anything from `usercache-*` / `webcache-*`
here.

## What this is

A packet logger and automation engine for the MMORPG NosTale. A 32-bit Windows
DLL is injected into the game, hooks the client's own recv/send functions,
copies plaintext packets into a bounded queue, and serves them to a host over
TCP loopback. The host can display, log, replay and inject packets, and feed a
world-state model. The long-term goal is a game engine that can play through
packets; the packet wiki is the foundation.

Location: `~/games/gameforge/NostalePacketLogger` (its own git repo,
`github.com/TaraJura/NostalePacketLogger`). It is not part of the Gameforge
client setup in the parent directory; use a separate Wine prefix.

## Status

Last checked 2026-11-07. **After a reboot, start with
[`docs/handoff-2026-11-06.md`](docs/handoff-2026-11-06.md)** for the exact
reinjection/auto-attack procedure. A game session is normally driven through
the Gameforge prefix (`WINEPREFIX=~/games/gameforge/prefix`), not
`~/.wine-nostale`; after any game restart the GUI's client picker injects into
the new window (or run `inject.sh` yourself).

| What | State |
|---|---|
| `windows/` | Original MSVC/C# implementation, moved unchanged. Builds with Visual Studio; not exercised on this machine. |
| `linux/` build | `make -C linux all test-host` is warning-free; all three outputs are PE32 i386. |
| Linux host tests | 165/165 pass (`make -C linux host-test`). |
| Headless client | `ntpl headless` ports NosBazar's login cipher, `NoS0577`/`NsTeST` and world handshake to Python. Live 2026-11-08: the login server at `79.110.84.75:4000` answers `failc 1` to an invalid token, proving the packet layout, client version/hash and login cipher; the world/character phase needs a valid Gameforge thin token. Docs: `docs/headless.md`. |
| MCP (agent access) | `python3 -m ntpl mcp` speaks MCP JSON-RPC over stdio and exposes 21 tools (state, entities, drops, map, portals, time-spaces, A* path, spells, move/travel, attack/cast, raw send, rest, pickup, wait, memory). It owns the DLL's single host connection and rebroadcasts it on `127.0.0.1:47900`; run the GUI with `--port 47900`. The DLL port is discovered from its `C:\ntpl-<pid>.port` file (the DLL may have fallen back from 47821 to 47822+). `.mcp.json` is committed; docs in `docs/mcp.md`. |
| Map data | `NStcData.NOS` parsed (779 grids, one byte per cell). Live 2026-11-07: map **2** NosVille Meadows (150x150), then map 1 NosVille (160x180) via `c_map`; 2026-11-06: map **20** Mountain Cave 1 (230x260). Map names come from `NSlangData_UK.NOS` → `_code_uk_MapIDData.txt` (`zts<id>e<TAB><name>`, 374 entries). `astar()` is 8-direction with sqrt(2) diagonals and no corner cutting; the path is drawn as a continuous polyline. |
| Monster names | Two-step join in `monsternames.py`: `NSgtdData.NOS` → `monster.dat` maps the template vnum to a language text key (`VNUM 419` → `NAME zts489e`), and `NSlangData_UK.NOS` → `_code_uk_monster.txt` maps that key to the localized name (2,679 entries; live map 103: vnum 419 = Violent Seedle, 420 = Crazy Dander, 421 = Violent Chicken). The text key is **not** the vnum (record `zts419e` is "Wheat"). Only `monster.dat` is decrypted from the 18 MB archive, so loading takes ~0.4 s. `spawnlog.py` backfills entities that spawned before the host attached from the DLL's `C:\NosTalePacketLog.txt` (`[RECV] in ...` lines). |
| Spell catalog | `NSgtdData.NOS` → `Skill.dat` parsed by `spells.py`: 1,964 records, 791 active (`TYPE[0]==1`), with name from the `NAME` zts key, MP cost, range, cast time, cooldown and target type. Live examples: 866 Holy Attack (0 MP/0.9 s), 869 Mana Shield (self, 300 MP/60 s), 876 Holy Hammer (145 MP/90 s). The GUI **Add spell…** picker uses it; the bot enforces MP/range/cooldown/cast time. |
| Map UI / bot | GTK4 window with a startup **client picker** (lists every running `NostaleClientX.exe` window through `Injector.exe --list`, pairs it with the DLL's `C:\ntpl-<pid>.port` file and injects the chosen client; **Client…** in the header reopens it; one already-injected client is attached silently) and three tabs: **Packets**, **Map / Bot** and **Bot log** (timestamped actions for debugging: attacks, TARGET selections/errors, walks, pickups, recovery, DLL errors; 4,000-line cap, Clear log; mirrored to `~/.config/ntpl/bot.log`, `NTPL_BOT_LOG` overrides, 4 MiB rotation). The Map/Bot page has a responsive layout (map + sidebar, reflows under ~1050 px), automatic whole-map (contain-fit) view with no manual map/scale/center controls, **immediate map switch** from `c_map`/`at` (the map id is the second `c_map` field), **attach-time auto-detect** when no map id was seen (all live entities, tightest perfect fit — same-layout maps tie on positions alone), structured sidebar (Character HP/MP bars + recovery banner, Path, Combat with green/red toggles, Targets with an unknown-vnum option, Spells, Loot, Recovery, Configuration), **Block portals** (Path section, on by default) keeps the bot on one map: the GUI sends `PORTALS|1` on every connect and the DLL drops the client's own `preq` portal request (logged as `SEND|preq`, server never sees it), A* treats the `gp`/learned portal cells as obstacles, a map click on one is refused, every `MOVE` is checked against them (straight leg included), and a drop/monster on a portal is approached from a neighbouring cell; the setting is saved as `block_portals` and the live state appears as `portalblock=BLOCKED`. Paths are replanned from the current position at Start, and any goal farther than 3 cells (a raw waypoint after a loop wrap, a stale route) is walked in <=3-cell MOVEs; a far `walk` is rejected by the server and leaves the character standing still. The bot also has drops + 8-direction A* path + loop + auto-attack/Retaliate + pickup (walks to drops up to 20 cells, then resumes the path) + low-HP/MP sit/recover (finishing the current kill first, and standing to fight back when hit) (the bot clears in-range monsters before taking the next path leg; with no path it chases an out-of-range target on foot), monster target filters (all/only/except by vnum), ordered spell rules from the scanned Skill.dat catalog (Always/my HP/my MP, cooldown, MP/range/cast-time checks, monster/self), JSON save/load of the whole bot config, including the drawn route (waypoints, path start, map id, Draw path state), bigger own dot + facing arrow, and hover tooltips for players/monsters/NPCs/drops. **Combat reads Skill.dat:** Skill `0` is the equipped SP card's basic attack, detected from `u_s 0` + `su` (Energy Bolt 260 on the live mage, not the old hardcoded 866), and its range/cast/cooldown/MP drive the attack. The Combat UI is only Auto-attack/Retaliate plus that info line — no Skill/Search/Min delay/template fields; the bot always uses slot 0, the skill's own range, a 15-cell acquisition radius and no extra delay. Focus fire: the current target is kept until it dies (a closer monster does not steal it; Retaliate overrides, and once it picks an attacker it keeps it while that monster is still attacking, so two attackers cannot make the bot ping-pong). Attacks require a clear line of sight over walkable map cells (no firing through town walls), and repeated server refusals pause combat instead of target-hopping. On death the bot stops immediately when **Revive on death** is off (default); when it is on, it answers the `dlgi #revival` dialog with the client's own Continue burst (10 Seeds of Power) and resumes on `revive`. A target whose every hit is answered with `cancel` (a client-only ghost, common after dying) is dropped after 2 refusals **only if it never landed a hit**; a target that has been hit is kept, and a refused cast waits out the skill's own cooldown instead of retrying at 0.1 s. Retaliate ignores the target filter and works on its own (the monster that hit you is attacked back, and only that one, even when its vnum is not selected); recovering never continues while the character has been hit recently (the bot stands and fights back, even when the attacker is not in the entity list), and loot pickups wait until the attacks stop; a server `cancel` after a landed `su` is the normal end of that cast (cancels are paired with the cast they end, so a fast next attack cannot turn it into a refusal), while a cancel with no hit clears the cooldown for an immediate retry. The **Targets** list shows only monster vnums spawned on the current map (selected ones not on it stay marked). At startup the GUI **seeds the map id, position and char id from the DLL log's last `at` + own walks**, so an attach-after-login shows the real map (the label marks a position-only guess `· auto`; with no data the canvas stays blank "waiting for the game…"). **Portals are server-sent**: on map entry the server sends `gp <x> <y> <toMap> …` for every portal of that map, stored in `WorldState.gates`, drawn as rings with a "Portal → map N" tooltip and persisted in `~/.config/ntpl/portals.json` (`NTPL_PORTALS` overrides); a crossing is only the fallback when a `gp` list was missed. Right after `gp` the server sends the map's **Time-Space entrances** as `wp <x> <y> <tsId> <flag> <minLv> <maxLv>`; they are stored in `WorldState.timespaces` and drawn with the nosapki stone icon in a gold frame, tooltip in the client's own wording: "Time-Space Stone / Required Level 26". Live map 4 has all eight (TS 16, 17, 18, 19, 20, 21, 22, 704); map 3 has TS 5, 6, 10, 11, 12, 13. A `gp` destination in the 2xxx range is a Time-Space **instance** gate (map 4: (224,103)→2110, (228,53)→2510) drawn as an amber ring. The icon asset is `linux/host/ntpl/assets/ts-1.png` from `nosapki.com/images/nt-images/ts-1.png`, with an amber-ring fallback when missing. The spell picker always lists all active spells and marks `slot N` / `not learned` (only when `ski` is known) / `slot unknown` (no list — use the spell once or relog); slots are learned from `u_s`/`su` pairs because `ski` is sent at login. `make gui-test` and `make bot-test` (20 smokes) pass. |
| x86 hook stubs | `HookStubTest.exe` (Wine) calls the real `hooks.S` detours and verifies the EDX packet pointer at `20(%ebp)`, the original-call replay, the send trampoline and the drop path (a 0 return skips the trampoline; read `-40(%esp)` after `popal`, not `-4(%ebp)`). Passes. |
| GUI | Two headless GTK4 smokes pass (`make -C linux gui-test`, Xvfb): the packet window (fake stream → 2 rows + STATUS in the status bar) and the client picker (two clients listed, Connect attaches to the chosen port, the uninjected row offers Inject & connect). |
| Injector | Verified under Wine end-to-end against both the fake target and the live client (PID 1216): remote `LoadLibraryA` succeeds and the DLL starts. |
| Wine integration | Verified with Wine 11 + the fake target: DLL loads, listens on `127.0.0.1:47821`, host gets `STATUS`, `SEND` command path answers. Hooks correctly reported `NOT-FOUND` because the fake target is not the game. |
| Real client | **Verified live 2026-11-06.** Injected into PID 1216 (Wine 11): both hooks ACTIVE (`recv=0x004DBB4D send=0x004EB5C4 tnt=0x0055AA84`), drops 0. `rest` controls sit/stand. Movement uses the DLL's `MOVE\|x y`, which calls the client's own MoveTo (`0x565E88`): the client pathfinds, animates and sends its own `walk`; `POS` reads its local position. The GUI bot walked `(26,133)→(23,130)` as six client walk packets (no injected walks or teleports). Raw injected walks need the checksum `1 if (x+y)%3 == 1` and are not animated. **TARGET verified live 2026-11-07** (v0.2.1, PID 1188): `TARGET|8684` → client `[SEND] ncif 3 8684` → `su ... 3 8684 855 ...`; the DLL runs the client's selector at `0x56964C`. The entity lookup validates every id occurrence during the scan, so a small id seen in thousands of tables is still found (live: six current monsters selected in ~0.5 s each). |
| Skill hook | Captures the client's own `u_s` builder at the `0x72D5B2` call site (packet in ECX); status shows `skill=ACTIVE`. The client also sends `preq` through this builder, so the same call site drops it for **Block portals** (see the send hook's return-value rule in `hooks.S`). Normal attack is `u_s 0 3 <id>`; the server answers with the card's basic skill (`su ... 260 ...` on the live mage 2026-11-07, `866/868` on the swordsman 2026-11-06). `u_s 866` itself is answered with `cancel 0 866 -1`. Live auto-attack verified. |
| GUI | PyGObject 3.56 / GTK 4.22 present; `ntpl.gui` imports. Not opened on a display in this environment. |

## Commands

Every Bash tool call is a fresh shell. Use absolute paths or `cd` in the same
command.

```bash
cd ~/games/gameforge/NostalePacketLogger

# Linux build (MinGW-w64 i686; installed via apt)
make -C linux all test-host
make -C linux check                 # file(1) on the three PE32 outputs
make -C linux host-test             # 165 stdlib unittest cases
PYTHONPATH=linux/host python3 -m ntpl mcp   # MCP stdio server + broker on 47900 (DLL port auto-discovered)
PYTHONPATH=linux/host python3 -m ntpl headless --token-file ~/.config/ntpl/token --game-dir <Nostale dir>  # clientless login
make -C linux gui-test              # headless GTK4 + map surface smoke
make -C linux bot-test              # 20 smokes: combat, rules/config, spell order, out-of-range, rejection, HP/MP + finish-kill recovery, loot approach, portal blocking, retaliate-only defence, far-goal leg chopping, death/revive dialog, ghost-target dropping, target focus, focus+retaliate, cancel pairing, refusal keep, defend first, start while sitting
make -C linux clean

# End-to-end without the game (temporary Wine prefix, deletes itself)
./linux/scripts/dev-test-wine.sh 25

# With the game running in its own prefix
export WINEPREFIX="$HOME/.wine-nostale"       # never the Gameforge prefix
linux/scripts/inject.sh
linux/scripts/host.sh status
linux/scripts/host.sh monitor [--engine] [--filter TEXT] [--log FILE]
linux/scripts/host.sh walk 25 118              # client MoveTo; natural walk
linux/scripts/host.sh cast 0 --target-id 5160   # normal attack (u_s 0 3 id)
linux/scripts/host.sh send "rest 1 1 14881134" # sit/stand toggle
linux/scripts/host.sh send "walk 19 146 0 10"  # raw packet
linux/scripts/host.sh scan 34 73               # memory debug: pair
linux/scripts/host.sh find 14881134            # memory debug: charId
linux/scripts/host.sh read 0x07B96FA4 32       # memory debug: dump
linux/scripts/host.sh map info 20 --at 25,117  # map size + walkability
linux/scripts/host.sh map render 20 /tmp/map.png --scale 3
linux/scripts/host.sh map best --log "$WINEPREFIX/drive_c/NosTalePacketLog.txt"
linux/scripts/gui.sh                           # GTK4 UI + client picker
linux/scripts/gui.sh --no-client-picker        # direct to --port (MCP broker)
linux/scripts/host.sh gui

# Windows (from Windows, or WSL with the full MSBuild path)
msbuild windows/PacketLogger.sln /p:Configuration=Release /p:Platform=x86
```

The host can be run without installation:

```bash
PYTHONPATH=linux/host python3 -m ntpl status
```

## Architecture

- **DLL** (`linux/src/dll/`): resolves three byte signatures in
  `NostaleClientX.exe`, installs two detours (5-byte call replay for recv,
  6-byte trampoline for send), copies packets into two `PacketQueue` rings
  (4096 slots of 2048 bytes each), logs to `C:\NosTalePacketLog.txt`, and serves
  `TYPE|PAYLOAD` lines over TCP `127.0.0.1:47821`. A second client (or a stale
  listener left by Wine) pushes each DLL to the next free port from
  `NTPL_PORT`; the port it bound is written to `C:\ntpl-<pid>.port` (Windows
  PID) so the host can find it without probing, which would steal the single
  host slot. `PORTALS|1` makes the send hooks drop the client's own
  `preq` portal request (still logged) so warp tiles cannot change maps.
  `MOVE|x y` calls the
  client's own MoveTo (`0x565E88`, packed x/y), so movement is pathfound and
  animated by the game; `POS` reads the local player object (x, y, char id and the rest flag at +0xA9, so a host that attaches while sitting stands up before walking); `TARGET|entityId`
  calls the client's own selector (`0x56964C`, controller `[[0x795200]]`), so
  the client highlights the monster and sends its own `ncif`.
- **Injector** (`linux/src/injector/`): expands `VirtualAllocEx` +
  `WriteProcessMemory` + `CreateRemoteThread(LoadLibraryA)`, with a real
  `WaitForSingleObject` / `GetExitCodeThread` check. Enumerates Windows PIDs by
  executable name, so it works under Wine without Linux PID mapping.
  `--list` shows the windows, `--pid <winpid>` injects one, `--all` every one.
- **Host** (`linux/host/ntpl/`): `protocol.py` (parse/encode), `client.py`
  (TCP, retry, reader thread), `clients.py` (client discovery: Wine contexts
  from `/proc`, `Injector.exe --list`, the DLL port files, injection of one
  pick), `engine.py` (`WorldState`; parses `mv`, `tp`,
  `rest`, `cond`, `ncif`, `su`, `stat`, `st`, spawns), `mapdata.py`
  (`NStcData.NOS` grids, A*, map ranking), `monsternames.py`
  (`NSgtdData.NOS` `monster.dat` vnum → text key joined with the
  `NSlangData_*.NOS` archive and its nibble text codec → `vnum → monster
  name`), `spells.py` (`NSgtdData.NOS` → `Skill.dat` catalog: MP, range, cast
  time, cooldown, target), `spawnlog.py` (entity id → vnum/player name from
  the DLL session log, for spawns that happened before the host connected),
  `noscrypto.py` (login/world cipher ported from NosBazar: packing tables,
  session XOR, first-packet case), `headless.py` (clientless login: NoS0577,
  NsTeST, world handshake, clist/select, keepalive; feeds the same `Engine`),
  `botconfig.py`
  (JSON bot configuration: target filters, spell rules, validation and atomic
  save/load), `cli.py` (`walk` waits for the server's `tp`/`mv`; `cast` waits
  for `su`), `gui.py` (packet table + Map/Bot tab), `mcp_server.py` (MCP
  stdio server + `Broker`: the broker owns the DLL's one host connection and
  fans out `RECV`/`SEND`/`STATUS` to the GUI/CLI on 47900; tools read
  `WorldState` and act through the same commands). Standard library only
  outside the GUI (PyGObject + pycairo for the window).
- **Sit/stand:** `rest 1 1 <charId>` toggles; the server answers
  `rest <type> <charId> <state>` with 0 = standing, 1 = sitting. Sitting
  regenerates HP/MP but blocks movement, so stand before `walk`. The client
  does not echo its own `mv`; wait for the server's `tp` after a walk.
- **Combat:** the server sends `su <attackerType> <attackerId> <targetType>
  <targetId> <skillVnum> ...` for every hit; the engine records the first five
  fields. `ntpl cast <slot> --target-id <id>` makes the client select the
  monster (`TARGET|id`, which sends the client's own `ncif`; a player target
  still uses an injected `ncif`), then sends a default
  `u_s <skillSlot> <targetType> <targetId>` and waits for `su`. The server
  validates the skill target against the client's selection, so an injected
  `ncif` alone does not redirect the attack. The first
  field is a zero-based **skill slot** from `ski`, not the Skill.dat vnum
  (`u_s 0` = normal attack, `u_s 10` → `su ... 876`); later fields are
  unknown. Use `--packet` for a literal
  captured packet when experimenting.
- **Map/bot:** `mapdata.py` reads `NostaleData/NStcData.NOS` (16-byte header,
  count, id/offset index, zlib blocks; `width:int16`, `height:int16`,
  `width*height` terrain bytes; `0x00` walkable). Live 2026-11-07 maps: **98**
  Comet Meadow (50x45), **103** Deep Cave (55x40), **104** (17x15), and the
  earlier **2**/1/20. The Map/Bot tab draws the grid; a left click walks the
  character there along the A* path in short three-cell MoveTo legs (a single
  far MOVE makes the client jump), or with **Draw path** on plans a route:
  the first click sets its starting point (cyan ring, not tied to the
  character), later clicks add waypoints, and Start first walks to that start
  point and then follows the route. The own marker follows the DLL's `POS`
  (polled 2 Hz),
  so it tracks the real position instead of the walk packet's destination.
  It computes an
  A* path, and the bot recovers below the HP threshold (sit, wait for full,
  stand), stands (`rest`), picks up own/unowned drops (`get 1 <charId>
  <dropId>`), auto-attacks with Retaliate priority (`TARGET|id` selects the
  monster in the client, then `u_s`; skill 0 = normal attack), chases a
  target that is outside attack range (select, walk one leg, attack once in
  range) and walks in three-cell legs through the client's own MoveTo
  (`MOVE|x y`), handing the next leg off one cell early so walking is
  continuous. A killed monster gets no `out`, so a TARGET refusal drops the
  entity from the world state and the bot picks a live one.
  Combat takes priority over the path: the bot clears all attackable
  monsters in range before taking the next leg; with no path it fights
  stationary and chases an out-of-range target on foot.
  **Targets** filters by monster template vnum (all / only selected / all
  except selected); **Spells** uses the scanned Skill.dat catalog and ordered
  rules (Always / my HP% / MP%; per-rule cooldown; monster or self target;
  MP/range/cast-time checks) before falling back to the Skill field. Always +
  cooldowns is a rotation; rule order is explicit (1., 2., up/down). The whole configuration is JSON
  save/load (see [docs/bot-config.md](docs/bot-config.md)).
  With no path the bot runs stationary combat/pickup/recovery. Map changes come from
  `c_map <type> <mapId> <state>` (`mapId` is the **second** int) plus `at`;
  the GUI switches immediately and clears stale entities/drops. When the host
  attaches after login and saw no map id, `choose_map_for_positions` ranks all
  779 grids against **every** live entity (players, monsters, NPCs), picks the
  tightest perfect fit, and keeps the current map only while it is tied for
  best; a server map id with no grid in the archive is reported instead of
  showing another map (do not "improve" this back to players-only — the
  NosVille Meadows map 2 is inseparable from 300/5613 with two points).
  A GUI attached after login now **seeds `map_id`/position/char id from the
  DLL log's last `at` and own walks** (`spawnlog.load_last_position`) before
  the position ranking runs, so the guess is only a last resort.
  **Portals are server-sent:** on map entry the server sends the map's whole
  portal list as `gp <x> <y> <toMap> <unknown> <index> <unknown>`
  (`WorldState.gates`; live maps 2026-11-07: 103 → (20,20)→104 and
  (42,32)→3; map 3 → (10,208)→6, (19,7)→7, (45,12)→103, (156,2)→4,
  (178,189)→2). The same entry sends the map's Time-Space list
  (`wp <x> <y> <tsId> <flag> <minLv> <maxLv>`) into
  `WorldState.timespaces`, drawn with the stone icon; live map 4 has all
  eight Time-Spaces (TS 16–22, 704). Crossing learning is the fallback: a
  leaving `c_map`/`mapout` remembers the cell held, and the following `at`
  gives the destination and arrival cell, recording both sides
  (`WorldState.portals`).
  `spawnlog.load_gates`/`load_portals` backfill from the DLL log;
  `set_gate` lets an exact gate replace a nearby crossing guess,
  `normalize_portals` averages leftovers, and the GUI persists to
  `~/.config/ntpl/portals.json`, drawing magenta rings with a "Portal → map
  N" tooltip after the own marker so a portal under the character stays
  visible.
  **Combat values come from the equipped SP card, not constants:** `u_s 0` is
  answered by `su ... <vnum> ...` (260 = Energy Bolt on the live mage), and
  the Skill.dat record for that vnum gives range (6), cast time (0.8 s),
  cooldown (0.5 s) and MP (0); the bot caps the Search radius by the skill
  range and paces actions by `max(Min delay, cast, cooldown)` (Min delay defaults to 0). `ski` is only
  sent at login, so slot→vnum mappings are also learned from paired
  `u_s <slot>` / `su ... <vnum> ...` (live in `WorldState.skill_learned`,
  backfilled from the DLL log by `spawnlog.load_skill_slots`); without a
  mapping a spell rule is skipped with a status message instead of sending a
  vnum as a slot. The own marker is a bigger dot + a facing arrow from the
  last `walk` delta (the protocol has no own-facing packet).
- **Docs**: `docs/architecture.md`, `docs/engine.md`, `docs/map.md`,
  `docs/bot-config.md`, `docs/live-session-2026-11-06.md`,
  `docs/feature-review-2026-11-06.md`, `docs/hooking.md`, `docs/ipc.md`,
  `docs/linux.md`, `docs/windows.md`, `docs/development.md`,
  `docs/roadmap.md`, `docs/troubleshooting.md`; packet wiki in
  `docs/protocol/`; **upstream reuse** in `docs/reusable-projects.md`.

## Rules

- **32-bit only.** The game is a 32-bit Delphi application. The DLL, the
  injector and the stubs are i386. Do not build x64.
- **Patterns are client-build-specific.** `NOT-FOUND` is a non-fatal state, not
  a crash. Re-derive with `docs/hooking.md`; never guess-patch addresses.
- **Nothing in a hook may allocate, log, lock for long, or call the socket.**
  The only allowed operation is `PacketQueue::push`. Drop and count on
  overflow; never stall the game thread.
- **MinGW headers on Linux are lowercase**: `<windows.h>`, `<psapi.h>`,
  `<tlhelp32.h>`, `<winsock2.h>`, `<ws2tcpip.h>`. Capitalised includes fail.
- **The DLL uses no libwinpthread.** Keep using `CRITICAL_SECTION` / the
  atomic queue, not `std::mutex` or `std::thread`.
- **Wine prefix:** the game runs under the Gameforge prefix
  (`~/games/gameforge/prefix`, its Wine 11). Inject only into
  `NostaleClientX.exe` (the window is `NosTale - <pid>`), never into
  `gfclient.exe`/`WebHelper.exe`, and never into `~/.wine`
  (WarCraft III/Battle.net). Do not kill a client the user is using.
- **Delphi string ownership:** `send_packet` frees its buffer after the call,
  mirroring the original. If crashes appear right after a manual send,
  investigate this first (`docs/hooking.md`).
- **Movement is client-side.** `MOVE` calls the client's MoveTo (`0x565E88`)
  from the DLL worker and `POS` reads the local player object (including the sit flag); both addresses
  are client-build-specific and prologue-validated. Do not "fix" a walk by
  injecting more `walk` packets: the server moves, the client teleports.
- **Git:** commit from this directory only. The home directory has a stray
  `~/.git`; never commit there and never run `gc`/`clean` on it.
- **Privacy:** never read, copy or write credentials, session data, or the
  contents of `usercache-*` / `webcache-*`.
- **Terms of service:** Gameforge prohibits automation. The owner accepts the
  account risk; do not add detection-evasion features.

## Achieved / next

First packet-driven game actions are confirmed on the live client:

1. The injector and both hooks work in the real client.
2. Sit/stand is controlled by `rest`.
3. The client's own MoveTo (`MOVE|x y`) walks and animates the character; it
   sends its own `walk` packets. `POS` reads its local position.
4. The client's own selector (`TARGET|id`) selects the monster and sends its
   own `ncif`; live 2026-11-07 the server then answered `u_s 0 3 8684` with
   `su ... 3 8684 ...` instead of the previously clicked id.
5. The engine parses the resulting `tp`/`rest`/`cond`/`stat` stream.
6. The `NStcData.NOS` map parser identified map **20** (230x260); the Map/Bot
   tab renders it, draws 8-direction A* paths and runs the loop/auto-attack.
   The bot smokes send `ncif`/`u_s`/`MOVE` and pass; `TARGET` is verified
   live (see 4).

Next:

1. Fully done: live path loop, auto-attack, pickup, map switching and the
   skill hook; normal attack is `u_s 0 3 <id>` (the server reports skill
   866/868 in `su`; 260/855 after the SP card changes).
2. **Agentic play:** the MCP server is live-ready (`ntpl mcp`); next is a
   live agent session against the game. The **Time-Space stone icon** from
   nosapki is integrated (`assets/ts-1.png`); the same source
   (`nosapki.com/images/nt-images/`, `images/map_previews/<map>.png`) can
   supply monster/map icons next.
3. ~~Capture the client's own `walk`/`u_s` SEND packets~~ — done: the send
   hook sees `walk`, and the skill hook captures `u_s` at `0x72D5B2`.
4. Map switching is live-verified for the `c_map` 20↔85 portal capture; more
   maps will confirm the parser and 8-direction A* on other grids.
5. Self-target shape (`u_s <slot> 1 <charId>`) is live-captured
   (`u_s 7 1 14881134` → `su ... 873`); no remaining protocol guess.
6. Unknown-vnum monsters that existed before DLL injection remain the main
   target-filter gap; the UI has an explicit option and docs describe relog.
7. Reconcile `docs/protocol/` with more live captures, then extend the loop
   with path validation and combat state.
8. **Reuse the upstream MIT projects** cloned in `~/games/gameforge/`
   (`nosbazar`, `GflessClient`, `NosTale-Gfless`, `NosTale-Auth`,
   `nostale-auth`): see `docs/reusable-projects.md`. Two paths: (a) launch the
   real client without the Gameforge launcher by emulating the
   `GameforgeClientJSONRPC` pipe and `NoS0577` auth; (b) clientless login with
   NosBazar's `noscrypto`/session code, which also delivers the login `ski`
   skill list (the current `slot unknown` cause) and complete entity
   lifecycles.
9. The learned skill list is only sent in `ski` at login; the log is
   truncated on every DLL reinjection, so the spell picker says `slot unknown`
   until a relog. Persist `ski` (sidecar file / append log) or receive it from
   a clientless login.
10. **Headless client:** `ntpl headless` does the clientless login (cipher,
    NoS0577/NsTeST/world handshake) and is live-validated through `failc 1`
    for a bad token. The Gameforge thin-token acquisition, a real-token
    world/character run, and a headless broker so the GUI/MCP bots can drive
    a clientless session remain. See [docs/headless.md](docs/headless.md).
