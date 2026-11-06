# Linux guide

## What runs where

```
Linux
├── Wine process:  NostaleClientX.exe (32-bit PE)
│   └── PacketLogger.dll (32-bit PE, MinGW-built) — hooks, ring, TCP server
└── native process: python3 -m ntpl — TCP client, CLI, GTK4 GUI, engine
```

The game is not a Linux binary. This guide assumes it is installed in a Wine
prefix; the logger does not install or launch it.

See also [engine.md](engine.md) for the Python host API and
[live-session-2026-11-06.md](live-session-2026-11-06.md) for what was verified
against the running client.

## Prerequisites

```bash
# 32-bit MinGW cross-compiler
sudo apt install g++-mingw-w64-i686

# Wine (any recent version; the Gameforge tree ships Wine 11)
wine --version

# Python 3.10+
python3 --version

# optional GTK4 GUI
sudo apt install python3-gi gir1.2-gtk-4.0
```

On this machine the Gameforge runner also works as the Wine binary:

```bash
export NTPL_WINE="$HOME/games/gameforge/runner/wine-stable-11.0/opt/wine-stable/bin/wine"
```

Do **not** inject into the Gameforge client prefix. Use a dedicated NosTale
prefix so the two clients never share a Wine environment.

## Build

```bash
cd linux
make all test-host
make check
```

Expected:

```
build/PacketLogger.dll: PE32 executable for MS Windows 4.00 (DLL), Intel i386
build/Injector.exe:     PE32 executable for MS Windows 4.00 (console), Intel i386
build/FakeGame.exe:     PE32 executable for MS Windows 4.00 (console), Intel i386
```

The DLL imports only `KERNEL32.dll`, `msvcrt.dll` and `WS2_32.dll`. libstdc++
and libgcc are linked statically; nothing else needs to be copied into the
prefix.

### Header case

MinGW-w64 headers on Linux are lowercase. `#include <Windows.h>` fails with
`fatal error: Windows.h: No such file or directory`; use `<windows.h>`,
`<psapi.h>`, `<tlhelp32.h>`, `<winsock2.h>`, `<ws2tcpip.h>`. All sources in
`linux/` already do.

## Create a prefix and install the game

```bash
export WINEPREFIX="$HOME/.wine-nostale"
wineboot -u
```

Install NosTale inside that prefix the same way you would on Windows. The
prefix is 64-bit WoW64 by default; the 32-bit client runs fine in it because
`WINEARCH` is not forced to win32. If you prefer a pure 32-bit prefix, create
it with `WINEARCH=win32 WINEPREFIX=... wineboot -u`, but the DLL works in both.

## Inject

Start the game, log in, reach the character or the world, then:

```bash
linux/scripts/inject.sh
```

or explicitly:

```bash
WINEPREFIX="$HOME/.wine-nostale" \
NTPL_WINE=/path/to/wine \
linux/scripts/inject.sh
```

The injector enumerates **Windows** PIDs by executable name
(`NostaleClientX.exe`, `NostaleX.dat`, `CustomClient.exe`) inside the prefix.
You do not need to translate Linux PIDs.

Expected output:

```
DLL: Z:\home\...\linux\build\PacketLogger.dll
Found 1 NosTale process(es):
  [1] PID 1234  NostaleClientX.exe  NosTale - ...
[+] injected into PID 1234 (NostaleClientX.exe)
```

Then check the hooks:

```bash
linux/scripts/host.sh status
```

```
ntpl 0.1.0 | recv=0x004A1B20 send=0x004A3C40 tnt=0x004B00A4 | hooks recv=ACTIVE send=ACTIVE | drops recv=0 send=0
```

If either address is `0x00000000` or a hook says `NOT-FOUND`, the client build
does not match the signatures. See [hooking.md](hooking.md#maintaining-the-signatures-after-a-client-patch).

## Use the host

```bash
linux/scripts/host.sh monitor                 # live stream
linux/scripts/host.sh monitor --engine        # also maintain WorldState
linux/scripts/host.sh monitor --filter walk   # only packets containing "walk"
linux/scripts/host.sh monitor --log capture.log
linux/scripts/host.sh send walk 19 146 0 10   # inject one packet
linux/scripts/host.sh walk 25 117             # client MoveTo; natural walk
linux/scripts/host.sh cast 0 --target-id 5160   # normal attack (u_s 0 3 id)
linux/scripts/host.sh send "rest 1 1 14881134" # sit/stand toggle
linux/scripts/host.sh status
linux/scripts/host.sh replay capture.log      # resend the SEND lines
linux/scripts/host.sh map info 20 --at 25,117 # map size + walkability
linux/scripts/host.sh map render 20 /tmp/map.png --scale 3
linux/scripts/host.sh map best                # rank maps by live entity positions
linux/scripts/gui.sh                          # GTK4 window (one-command launcher)
linux/scripts/host.sh gui                     # same, through the host wrapper

# reverse-engineering aids (loopback only)
linux/scripts/host.sh scan 34 73              # find a coordinate pair in memory
linux/scripts/host.sh find 14881134           # find a charId
linux/scripts/host.sh read 0x07B96FA4 32      # dump memory
```

`ntpl walk X Y` sends `MOVE|X Y` to the DLL, which calls the client's own
MoveTo: the client pathfinds, animates and sends its own `walk` packet, and the
command waits for that. The character must be **standing** first: while
sitting, the server ignores movement. The sit/stand toggle is
`send "rest 1 1 <charId>"`; the server's authoritative state comes back as
`rest <type> <charId> <state>` (0 = standing, 1 = sitting).

The GUI has a second tab, **Map / Bot**. It reads the client's own
`NostaleData/NStcData.NOS` (path configurable with `--map-data`), draws the
whole selected grid (auto-switch from `c_map`), overlays live entities with
hover tooltips, accepts waypoints by clicking, computes an **8-direction A\***
path, and can loop it while auto-attacking monsters. The sidebar has live
HP/MP bars, green/red toggles, a red recovery banner, target filters,
conditional spell rules and JSON save/load. `--log PATH` (or `NTPL_LOG`)
selects the DLL log used to backfill vnums for entities that spawned before
the GUI attached. See [map.md](map.md) and [bot-config.md](bot-config.md).

Inside `monitor`, stdin accepts:

| Command | Effect |
|---|---|
| `/send <packet>` | inject a packet without leaving the monitor |
| `/filter <text>` | change the display filter |
| `/status` | ask the DLL for its status line |
| `/engine` | print the current `WorldState` |
| `/quit` | disconnect and exit |

The GUI's packet tab has the same pieces: a filter box, a Follow toggle, a
packet table with RECV/SEND colours, a send entry and a status bar.

Without installing anything:

```bash
PYTHONPATH=linux/host python3 -m ntpl monitor
```

To install the CLI into a virtualenv:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e linux/host
ntpl monitor
```

The GUI extra is only useful on distributions where PyGObject can be built by
pip; on Ubuntu use the apt package.

## Logs

The DLL writes `C:\NosTalePacketLog.txt`:

```bash
tail -f "$WINEPREFIX/drive_c/NosTalePacketLog.txt"
```

Format:

```
10:19:19.123 [RECV] walk 19 146 0 10
10:19:19.456 [SEND] u_s 1 2
10:19:20.001 [MANUAL_SEND] walk 20 146 0 10
```

If `C:\` is not writable, the DLL falls back to
`%LOCALAPPDATA%\NosTalePacketLogger\dll.log` and then to `%TEMP%`. Set
`NTPL_LOG` to choose a path, or `NTPL_QUIET=1` to disable the file log.

## Environment variables

| Variable | Read by | Default |
|---|---|---|
| `NTPL_PORT` | DLL and host | `47821` |
| `NTPL_LOG` | DLL and GUI (spawn-log backfill) | `C:\NosTalePacketLog.txt` |
| `NTPL_QUIET` | DLL | unset; `1` disables the file log |
| `NTPL_NSTC` | host | `~/Desktop/nostake/Nostale/NostaleData/NStcData.NOS` |
| `NTPL_NSLANG` | host | `NSlangData_UK.NOS` next to `NStcData.NOS` (then any `NSlangData_*.NOS`) |
| `NTPL_WINE` | scripts | `wine` from `PATH` |
| `NTPL_WINEPREFIX` | `inject.sh` | `$WINEPREFIX`, then `~/.wine-nostale` |

`NTPL_PORT` must be set in the environment of the **game** process (the DLL
reads it at startup) and of the host. The simplest way is to export it before
both.

## Tests

```bash
make host-test          # 83 stdlib unit tests: protocol, engine, map, config, spells

make gui-test           # headless GTK4 window + map surface against a fake stream
make bot-test           # 5 fake-server smokes: combat, rules/config, spell order, rejection, recovery
make all test-host
./scripts/dev-test-wine.sh 25
```

`gui-test` uses `xvfb-run` to open the real window with a fake server on
loopback. It checks the packet rows and STATUS line, the map surface, the live
map switch (the 20→85→20 `c_map` sequence), map tooltips (including a vnum
backfilled from the DLL log), green/red toggle classes, the recovery banner,
and the unknown-position path guard. Install `xvfb` if it is missing.

`bot-test` drives the real GTK window against fake servers. The five smokes
cover auto-attack + path movement (`ncif`, `u_s`, `MOVE`), target filtering +
conditional spell rules + JSON config round-trip, scanned-spell rotation and
execution order, three rejected walks, and the sit/wait-for-full-HP recovery
loop. They need `xvfb`, but no game. `bot-test` uses the real map archive when
it is installed.

`dev-test-wine.sh` is the end-to-end test that does not need NosTale:

1. creates a temporary prefix;
2. runs `HookStubTest.exe`, which calls the real `hooks.S` detours with a known
   EDX and verifies the packet pointer, the original-call replay and the send
   trampoline continuation (the x86 ABI regression test);
3. starts `FakeGame.exe`, which `LoadLibraryA`s the real
   `build/PacketLogger.dll`;
4. waits for the DLL's TCP server;
5. runs `ntpl status` against it;
6. runs `ntpl send`, which exercises the host → DLL command path;
7. prints the DLL's own log and removes the prefix.

Because FakeGame is not the game, the expected status is
`recv=0x00000000 send=0x00000000 ... NOT-FOUND`. That is a pass: it proves the
DLL lifecycle, the transport, the status format and the command path. The hook
paths themselves are only exercised by the real client.

## Live verification (2026-11-06)

The real client was running in the Gameforge prefix (the user launched it
there); the injector targeted only `NostaleClientX.exe`, never `gfclient.exe`.
The recommended setup is still a dedicated prefix.

- Injected into the live client (PID 1216, Wine 11): both hooks `ACTIVE`,
  `recv=0x004DBB4D send=0x004EB5C4 tnt=0x0055AA84`, `drops recv=0`.
- The character was sitting. `rest 1 1 14881134` toggled it; the server
  answered `rest 1 14881134 0` (standing).
- The early injected walks only moved the server: the client did not animate
  them and snapped when a `tp` correction arrived. Raw injected walks need the
  checksum `1 if (x + y) % 3 == 1 else 0`, but that still does not animate.
- The DLL now calls the client's own MoveTo (`0x565E88`) for `MOVE|x y`; the
  client pathfinds, animates and sends its own `walk`. `POS` reads the local
  position/char id. The live GUI bot walked `(26,133)→(23,130)` as six client
  `[SEND] walk` packets, with no injected walks, probes or teleports.
- The character must be standing; sitting (`rest` state 1) blocks movement.
- The client's `NStcData.NOS` parsed into 779 grids. Ranking them against the
  live `mv` samples selected map **20** (230x260): 100 % of 173,503 samples
  (5,849 unique positions) land on `0x00` walkable cells. The Map/Bot tab
  renders it; `map render` produced `docs/assets/map20.png`.
- The GUI map and the bot pass the headless tests (`make gui-test`,
  `make bot-test`) and have now been run against the live client: the bot
  completed the six-cell path above.
- Live auto-attack was verified with `u_s 0 3 <id>`; the server answered
  `su ... 866/868 ...`. Pickup (`get 1 <charId> <dropId>`) and drop tracking
  are verified. The skill hook captures the client's own `u_s` packets.
- Map switching uses the **second** `c_map` field (`c_map <type> <mapId>
  <state>`) plus `at`; the GUI switches immediately and drops stale entities.
  Live captures cover map 20 ↔ 85.
- Monster names come from `NSlangData_UK.NOS` / `_code_uk_monster.txt`; the
  map tooltips use them. `spawnlog.py` backfills entity vnums and player names
  for spawns that happened before the host connected.

## Honest status

- The three signatures match the current live client. Both hooks are ACTIVE
  and no packets are dropped in steady state.
- `Injector.exe --all` works end-to-end against both the fake target and the
  live client.
- The x86 stubs pass `HookStubTest.exe`, which calls the real `hooks.S` with a
  known EDX and verifies the packet pointer, the original-call replay and the
  send trampoline.
- GTK4 4.22 and PyGObject 3.56 are installed; the real window passes the
  headless Xvfb smoke test (`make gui-test`) and has been run against the live
  game with the Map/Bot tab, map switching, tooltips and the bot.
- The map parser, A* and the bot loop pass their tests and have been run live:
  the bot completed client-animated paths, auto-attacked, picked up drops and
  switched maps. The self-target shape (`u_s <slot> 1 <charId>`) is also
  live-captured (`u_s 7 1 14881134` → `su ... 873` Group Healing). Watch the
  first live diagonal MoveTo run.
- The `SCAN`/`FIND`/`READ` commands are new reverse-engineering aids. `FIND`
  located the charId record, but the live position comes from the server's
  `tp`/`mv`, not from those addresses.
- The client's own `walk` SEND packets are not captured by the current send
  hook (it sees `rest`, `sortopen`, `pulse`). Injected walks are sent and the
  server answers with `tp`; that path is what the engine uses.

## Terms of service

Gameforge's rules prohibit bots and automation. This is a packet inspection and
automation tool for a live MMO; using it can get the account banned. It is
built for the account owner's own use, on their own client, and does not try to
evade detection. The decision and the risk are the owner's.
