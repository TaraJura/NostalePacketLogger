# Linux port

Native Linux host plus the MinGW-built DLL that still has to live inside the
game process.

NosTale is a 32-bit Windows binary, so under Wine the game is still a Windows
PE process and the hook must still be a 32-bit Windows DLL. That is the only
non-native piece. Everything outside the game is native Linux:

| Piece | Technology | Why |
|---|---|---|
| In-game hook | C++17 + GAS assembly, cross-built with MinGW-w64 | must run inside the 32-bit Windows game process |
| Transport | TCP on `127.0.0.1` | a Wine Winsock socket is an ordinary Linux socket; a native Python process can connect to it |
| Host library / CLI | Python 3.10+, standard library only | no dependencies, easy to extend into the engine |
| GUI | GTK4 via PyGObject | the native Linux toolkit, already present on Ubuntu |
| State model | `ntpl.engine.WorldState` | packets become a world, not just a list |
| Map / bot | `ntpl.mapdata` + `ntpl.spells` + GTK4 Map/Bot tab | client map grids, 8-direction A* paths, scanned spell catalog, ordered rules, target filters, auto-attack, JSON config |

See `../docs/architecture.md`, `../docs/engine.md`, `../docs/map.md`,
`../docs/ipc.md`, `../docs/hooking.md`, `../docs/linux.md` and
`../docs/live-session-2026-11-06.md` for the full picture.

## Layout

```
linux/
├── Makefile                 cross-build for the DLL, injector and test host
├── src/dll/                 injected 32-bit DLL
│   ├── hooks.S              x86 detours (replaces MSVC inline asm)
│   ├── packetlogger.*       signatures, hook install/remove, packet injection
│   ├── clientmove.*         MOVE/POS: client MoveTo call and local position
│   ├── packet_queue.h       fixed-capacity multi-producer ring
│   ├── tcp_server.*         loopback server
│   ├── memory.*             pattern scan, VirtualProtect patching
│   ├── nostale_string.h     Delphi string layout
│   ├── log.*, config.*      file log and environment config
│   └── dllmain.cpp          worker thread, command loop, shutdown
├── src/injector/            32-bit injector (Windows PIDs, works under Wine)
├── host/ntpl/               Python package (protocol, client, engine, mapdata,
│                            cli, gui)
├── host/tests/              stdlib unittest suite
├── scripts/                 inject.sh, host.sh, gui.sh, dev-test-wine.sh,
│                            dev-test-bot.sh
├── test/fake_game.cpp       LoadLibraryA fixture for the Wine test
└── test/gui_*.py            Xvfb smoke tests (packet window, map/bot tab)
```

## Build

```bash
sudo apt install g++-mingw-w64-i686        # one-time
make -C linux all test-host
make -C linux check                        # file(1) on the three PE outputs
```

Outputs: `linux/build/PacketLogger.dll`, `Injector.exe`, `FakeGame.exe` — all
`PE32 ... Intel i386`. The DLL imports only `KERNEL32`, `msvcrt` and `WS2_32`;
libstdc++ and libgcc are linked statically, so nothing else has to be shipped.

MinGW headers on Linux are lowercase (`windows.h`, not `Windows.h`). The source
uses lowercase includes for that reason.

## Test without the game

```bash
make -C linux all test-host
make -C linux host-test          # 165 cases: protocol, client, engine, mapdata, config, names, noscrypto, headless
make -C linux gui-test           # headless GTK4: map switch, tooltips, toggles,
                                 # banner, client picker
make -C linux bot-test           # 20 smokes: combat, rules/config, spell order, out-of-range, rejection, HP/MP + finish-kill recovery, loot approach, portal blocking, retaliate-only defence, far-goal leg chopping, death/revive dialog, ghost-target dropping, target focus, focus+retaliate, cancel pairing, refusal keep, defend first, start while sitting
./linux/scripts/dev-test-wine.sh 25
```

`dev-test-wine.sh` creates a temporary Wine prefix and runs two things:

1. `HookStubTest.exe` calls the real `hooks.S` detours with a known EDX and
   checks that the packet pointer, the original-call replay, the trampoline
   continuation and the packet-drop path (a 0 return skips the trampoline) are
   all correct. This is the regression test for the x86 ABI.
2. a copy of `FakeGame.exe` runs as `NostaleClientX.exe` with `--no-load`; the
   **real** `Injector.exe --all` finds it by name and injects the DLL; the
   Python host then connects over TCP. The DLL reports `NOT-FOUND` for the game
   signatures (correct: the fake target is not the game) and the test proves the
   injector path, the DLL lifecycle, the TCP server and the command path.

The prefix is deleted afterwards. `gui-test` and `bot-test` need `xvfb` and
GTK4, but no game and no real display. `bot-test` drives the real window
against a fake server and checks the client selection (`TARGET|id`) followed
by `u_s`, and the `walk`/`tp` confirmations.

## Run with the game

```bash
# 1. the prefix where the game actually runs (currently the Gameforge prefix);
#    never ~/.wine (WarCraft III/Battle.net)
export WINEPREFIX="$HOME/games/gameforge/prefix"
wineboot -u
# 2. install and start NosTale inside that prefix (out of scope here)
# 3. inject into every running client in the prefix
linux/scripts/inject.sh
# 4. talk to it
linux/scripts/host.sh status
linux/scripts/host.sh monitor
linux/scripts/host.sh monitor --engine
linux/scripts/host.sh walk 25 117        # send a walk, wait for tp/mv
linux/scripts/host.sh map info 20 --at 25,117
linux/scripts/host.sh map render 20 /tmp/map.png --scale 3
linux/scripts/gui.sh                     # GTK4 UI + startup client picker
linux/scripts/host.sh gui                # same, via the host wrapper
```

The sidebar's **Block portals** toggle (on by default) drops the client's `preq` portal request in the DLL and keeps paths off warp tiles, so a farming loop cannot change maps.

The GUI's **Map / Bot** tab loads the client's own map archive, draws the
whole walkable grid and the live entities with name tooltips, switches the map
immediately on `c_map`, and runs a click-drawn **8-direction A\*** path in a
loop with auto-attack, target filters, spell rules and recovery. The sidebar
has green/red toggles, a red recovery banner and JSON save/load. See
`../docs/map.md` and `../docs/bot-config.md`.

`scripts/inject.sh` uses `NTPL_WINEPREFIX` (default `$WINEPREFIX` or
`~/.wine-nostale`) and `NTPL_WINE` (default `wine` from `PATH`). The DLL listens
on `127.0.0.1:47821`; set `NTPL_PORT` in the environment of **both** the game
and the host to change it.

The DLL writes `C:\NosTalePacketLog.txt`, which is
`<prefix>/drive_c/NosTalePacketLog.txt` on Linux:

```bash
tail -f "$WINEPREFIX/drive_c/NosTalePacketLog.txt"
```

## Environment variables

| Variable | Read by | Meaning |
|---|---|---|
| `NTPL_PORT` | DLL + host | TCP port, default `47821` |
| `NTPL_LOG` | DLL + GUI | log path, default `C:\NosTalePacketLog.txt`; the GUI backfills spawns from it |
| `NTPL_QUIET` | DLL | `1` disables the file log |
| `NTPL_NSTC` | host | `NStcData.NOS` path |
| `NTPL_NSLANG` | host | `NSlangData_*.NOS` language archive |
| `NTPL_WINE` | scripts | wine binary |
| `NTPL_WINEPREFIX` | `inject.sh` | prefix to inject into |
| `NTPL_TEST_PREFIX` | test script | reuse a prefix instead of a temp one |

## Honest status

- The three signatures match the **live client** (verified 2026-11-06):
  `recv=0x004DBB4D send=0x004EB5C4 tnt=0x0055AA84`, both hooks `ACTIVE`,
  drops 0. `rest` toggles sit/stand; `MOVE|x y` calls the client's own MoveTo
  and walks naturally, `POS` reads its position. The GUI bot walked a six-cell
  path live as client walk packets; auto-attack (`u_s 0 3 <id>` → `su ...
  866/868 ...`), pickup and map switching are also live-verified. The skill
  hook captures the client's own `u_s`. See
  `../docs/live-session-2026-11-06.md`. If a patch breaks the signatures or
  MoveTo prologue, `host.sh status`/`MOVE` report it; see `../docs/hooking.md`.
- The real injector has been exercised end-to-end under Wine against both a
  fake target and the live client (PID 1216).
- The map parser identified the live map (20, 230x260) with every observed
  position on a walkable cell. The map UI and the bot loop pass their headless
  tests and the live six-cell path run.
- Gameforge's terms of service prohibit automation; using this on a live account
  is the account owner's decision and risk.
