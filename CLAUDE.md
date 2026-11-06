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

Last checked 2026-11-06. **After a reboot, start with
[`docs/handoff-2026-11-06.md`](docs/handoff-2026-11-06.md)**: it has the
current state and the exact reinjection/auto-attack test procedure. The game
process is not running (it exited at 15:32:29 after the first skill-hook
build); the fixed build is in the repo, and the client must be restarted.

| What | State |
|---|---|
| `windows/` | Original MSVC/C# implementation, moved unchanged. Builds with Visual Studio; not exercised on this machine. |
| `linux/` build | `make -C linux all test-host` is warning-free; all three outputs are PE32 i386. |
| Linux host tests | 84/84 pass (`make -C linux host-test`). |
| Map data | `NStcData.NOS` parsed (779 grids, one byte per cell). Live map is **20** (230x260); 100 % of 173,503 `mv` samples land on walkable `0x00` cells. `astar()` is 8-direction with sqrt(2) diagonals and no corner cutting; the path is drawn as a continuous polyline. |
| Monster names | `NSlangData_UK.NOS` / `_code_uk_monster.txt` parsed by `monsternames.py` (nibble codec + archive reader): 2,795 `vnum → name` entries. The live `in 3 206 5122 ...` spawn is entity 5122, Angry Queen Bee (vnum 206); map tooltips use the name. `spawnlog.py` backfills entities that spawned before the host attached from the DLL's `C:\NosTalePacketLog.txt` (`[RECV] in ...` lines). |
| Spell catalog | `NSgtdData.NOS` → `Skill.dat` parsed by `spells.py`: 1,964 records, 791 active (`TYPE[0]==1`), with name from the `NAME` zts key, MP cost, range, cast time, cooldown and target type. Live examples: 866 Holy Attack (0 MP/0.9 s), 869 Mana Shield (self, 300 MP/60 s), 876 Holy Hammer (145 MP/90 s). The GUI **Add spell…** picker uses it; the bot enforces MP/range/cooldown/cast time. |
| Map UI / bot | GTK4 Map/Bot tab: responsive layout (map + sidebar, reflows under ~1050 px), automatic whole-map (contain-fit) view with no manual map/scale/center controls, **immediate map switch** from `c_map`/`at` (the map id is the second `c_map` field), structured sidebar (Character HP/MP bars + recovery banner, Path, Combat with green/red toggles, Targets with an unknown-vnum option, Spells, Loot, Recovery, Configuration), drops + 8-direction A* path + loop + auto-attack/Retaliate + pickup + low-HP sit/recover + **Move first** (attack while progressing; off = clear the area), monster target filters (all/only/except by vnum), ordered spell rules from the scanned Skill.dat catalog (Always/my HP/my MP, cooldown, MP/range/cast-time checks, monster/self), JSON save/load of the whole bot config, bigger own dot + facing arrow, and hover tooltips for players/monsters/NPCs/drops. `make gui-test` and `make bot-test` (6 smokes) pass. |
| x86 hook stubs | `HookStubTest.exe` (Wine) calls the real `hooks.S` detours and verifies the EDX packet pointer at `20(%ebp)`, the original-call replay and the send trampoline. Passes. |
| GUI | Headless GTK4 smoke test passes (`make -C linux gui-test`, Xvfb): fake stream → 2 rows + STATUS in the status bar. |
| Injector | Verified under Wine end-to-end against both the fake target and the live client (PID 1216): remote `LoadLibraryA` succeeds and the DLL starts. |
| Wine integration | Verified with Wine 11 + the fake target: DLL loads, listens on `127.0.0.1:47821`, host gets `STATUS`, `SEND` command path answers. Hooks correctly reported `NOT-FOUND` because the fake target is not the game. |
| Real client | **Verified live 2026-11-06.** Injected into PID 1216 (Wine 11): both hooks ACTIVE (`recv=0x004DBB4D send=0x004EB5C4 tnt=0x0055AA84`), drops 0. `rest` controls sit/stand. Movement uses the DLL's `MOVE\|x y`, which calls the client's own MoveTo (`0x565E88`): the client pathfinds, animates and sends its own `walk`; `POS` reads its local position. The GUI bot walked `(26,133)→(23,130)` as six client walk packets (no injected walks or teleports). Raw injected walks need the checksum `1 if (x+y)%3 == 1` and are not animated. |
| Skill hook | Captures the client's own `u_s` builder at the `0x72D5B2` call site (packet in ECX); status shows `skill=ACTIVE`. Normal attack is `u_s 0 3 <id>`; the server answers with `su ... 866/868 ...`. `u_s 866` itself is answered with `cancel 0 866 -1`. Live auto-attack verified. |
| GUI | PyGObject 3.56 / GTK 4.22 present; `ntpl.gui` imports. Not opened on a display in this environment. |

## Commands

Every Bash tool call is a fresh shell. Use absolute paths or `cd` in the same
command.

```bash
cd ~/games/gameforge/NostalePacketLogger

# Linux build (MinGW-w64 i686; installed via apt)
make -C linux all test-host
make -C linux check                 # file(1) on the three PE32 outputs
make -C linux host-test             # 84 stdlib unittest cases
make -C linux gui-test              # headless GTK4 + map surface smoke
make -C linux bot-test              # 6 smokes: combat, rules/config, spell order, out-of-range, rejection, recovery
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
linux/scripts/gui.sh                           # GTK4 UI: packets + map/bot
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
  `TYPE|PAYLOAD` lines over TCP `127.0.0.1:47821`. `MOVE|x y` calls the
  client's own MoveTo (`0x565E88`, packed x/y), so movement is pathfound and
  animated by the game; `POS` reads the local player object.
- **Injector** (`linux/src/injector/`): expands `VirtualAllocEx` +
  `WriteProcessMemory` + `CreateRemoteThread(LoadLibraryA)`, with a real
  `WaitForSingleObject` / `GetExitCodeThread` check. Enumerates Windows PIDs by
  executable name, so it works under Wine without Linux PID mapping.
- **Host** (`linux/host/ntpl/`): `protocol.py` (parse/encode), `client.py`
  (TCP, retry, reader thread), `engine.py` (`WorldState`; parses `mv`, `tp`,
  `rest`, `cond`, `ncif`, `su`, `stat`, spawns), `mapdata.py`
  (`NStcData.NOS` grids, A*, map ranking), `monsternames.py`
  (`NSlangData_*.NOS` language archive, nibble text codec, `vnum → monster
  name`), `spells.py` (`NSgtdData.NOS` → `Skill.dat` catalog: MP, range, cast
  time, cooldown, target), `spawnlog.py` (entity id → vnum/player name from
  the DLL session log, for spawns that happened before the host connected),
  `botconfig.py`
  (JSON bot configuration: target filters, spell rules, validation and atomic
  save/load), `cli.py` (`walk` waits for the server's `tp`/`mv`; `cast` waits
  for `su`), `gui.py` (packet table + Map/Bot tab). Standard library only
  outside the GUI (PyGObject + pycairo for the window).
- **Sit/stand:** `rest 1 1 <charId>` toggles; the server answers
  `rest <type> <charId> <state>` with 0 = standing, 1 = sitting. Sitting
  regenerates HP/MP but blocks movement, so stand before `walk`. The client
  does not echo its own `mv`; wait for the server's `tp` after a walk.
- **Combat:** the server sends `su <attackerType> <attackerId> <targetType>
  <targetId> <skillVnum> ...` for every hit; the engine records the first five
  fields. `ntpl cast <slot> --target-id <id>` sends `ncif` then a default
  `u_s <skillSlot> <targetType> <targetId>` and waits for `su`. The first
  field is a zero-based **skill slot** from `ski`, not the Skill.dat vnum
  (`u_s 0` = normal attack, `u_s 10` → `su ... 876`); later fields are
  unknown. Use `--packet` for a literal
  captured packet when experimenting.
- **Map/bot:** `mapdata.py` reads `NostaleData/NStcData.NOS` (16-byte header,
  count, id/offset index, zlib blocks; `width:int16`, `height:int16`,
  `width*height` terrain bytes; `0x00` walkable). The live map id is **20**,
  230x260. The Map/Bot tab draws the grid, takes click waypoints, computes an
  A* path, and the bot recovers below the HP threshold (sit, wait for full,
  stand), stands (`rest`), picks up own/unowned drops (`get 1 <charId>
  <dropId>`), auto-attacks with Retaliate priority (`ncif` + `u_s`; skill 0 =
  normal attack) and walks in three-cell legs through the client's own MoveTo
  (`MOVE|x y`), pacing each leg by distance so the animation stays continuous.
  **Move first** (default on) attacks while a path is active and walks on
  between attacks; with it off the bot clears nearby monsters before moving.
  **Targets** filters by monster template vnum (all / only selected / all
  except selected); **Spells** uses the scanned Skill.dat catalog and ordered
  rules (Always / my HP% / MP%; per-rule cooldown; monster or self target;
  MP/range/cast-time checks) before falling back to the Skill field. Always +
  cooldowns is a rotation; rule order is explicit (1., 2., up/down). The whole configuration is JSON
  save/load (see [docs/bot-config.md](docs/bot-config.md)).
  With no path the bot runs stationary combat/pickup/recovery. Map changes come from
  `c_map <type> <mapId> <state>` (`mapId` is the **second** int) plus `at`;
  the GUI switches immediately and clears stale entities/drops. The own marker
  is a bigger dot + a facing arrow from the last `walk` delta (the protocol has
  no own-facing packet).
- **Docs**: `docs/architecture.md`, `docs/engine.md`, `docs/map.md`,
  `docs/bot-config.md`, `docs/live-session-2026-11-06.md`, `docs/hooking.md`,
  `docs/ipc.md`,
  `docs/linux.md`, `docs/windows.md`, `docs/development.md`,
  `docs/roadmap.md`, `docs/troubleshooting.md`; packet wiki in
  `docs/protocol/`.

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
- **Wine prefixes:** use a dedicated NosTale prefix. Do not inject into
  `~/.wine` (WarCraft III/Battle.net) or the Gameforge client prefix in
  `~/games/gameforge/prefix`. Do not kill a client the user is using.
- **Delphi string ownership:** `send_packet` frees its buffer after the call,
  mirroring the original. If crashes appear right after a manual send,
  investigate this first (`docs/hooking.md`).
- **Movement is client-side.** `MOVE` calls the client's MoveTo (`0x565E88`)
  from the DLL worker and `POS` reads the local player object; both addresses
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
4. The engine parses the resulting `tp`/`rest`/`cond`/`stat` stream.
5. The `NStcData.NOS` map parser identified map **20** (230x260); the Map/Bot
   tab renders it, draws 8-direction A* paths and runs the loop/auto-attack.
   The bot smokes send `ncif`/`u_s`/`MOVE` and pass.

Next:

1. Fully done: live path loop, auto-attack, pickup, map switching and the
   skill hook; normal attack is `u_s 0 3 <id>` (the server reports skill
   866/868 in `su`).
2. ~~Capture the client's own `walk`/`u_s` SEND packets~~ — done: the send
   hook sees `walk`, and the skill hook captures `u_s` at `0x72D5B2`.
3. Map switching is live-verified for the `c_map` 20↔85 portal capture; more
   maps will confirm the parser and 8-direction A* on other grids.
4. Self-target shape (`u_s <slot> 1 <charId>`) is live-captured
   (`u_s 7 1 14881134` → `su ... 873`); no remaining protocol guess.
5. Unknown-vnum monsters that existed before DLL injection remain the main
   target-filter gap; the UI has an explicit option and docs describe relog.
4. Reconcile `docs/protocol/` with more live captures, then extend the loop
   with path validation and combat state.
