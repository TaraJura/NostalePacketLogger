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
linux/scripts/host.sh walk 25 117             # send a walk, wait for server tp/mv
linux/scripts/host.sh cast 866 --target-id 5180 # use a skill (experimental u_s)
linux/scripts/host.sh send "rest 1 1 14881134" # sit/stand toggle
linux/scripts/host.sh status
linux/scripts/host.sh replay capture.log      # resend the SEND lines
linux/scripts/host.sh gui                     # GTK4 window

# reverse-engineering aids (loopback only)
linux/scripts/host.sh scan 34 73              # find a coordinate pair in memory
linux/scripts/host.sh find 14881134           # find a charId
linux/scripts/host.sh read 0x07B96FA4 32      # dump memory
```

`ntpl walk X Y` sends `walk X Y 0 <speed>` and waits for the server's
`tp`/`mv` at that position. The character must be **standing** first: while
sitting, the server ignores movement. The sit/stand toggle is
`send "rest 1 1 <charId>"`; the server's authoritative state comes back as
`rest <type> <charId> <state>` (0 = standing, 1 = sitting).

Inside `monitor`, stdin accepts:

| Command | Effect |
|---|---|
| `/send <packet>` | inject a packet without leaving the monitor |
| `/filter <text>` | change the display filter |
| `/status` | ask the DLL for its status line |
| `/engine` | print the current `WorldState` |
| `/quit` | disconnect and exit |

The GUI has the same pieces: a filter box, a Follow toggle, a packet table with
RECV/SEND colours, a send entry and a status bar.

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
| `NTPL_LOG` | DLL | `C:\NosTalePacketLog.txt` |
| `NTPL_QUIET` | DLL | unset; `1` disables the file log |
| `NTPL_WINE` | scripts | `wine` from `PATH` |
| `NTPL_WINEPREFIX` | `inject.sh` | `$WINEPREFIX`, then `~/.wine-nostale` |

`NTPL_PORT` must be set in the environment of the **game** process (the DLL
reads it at startup) and of the host. The simplest way is to export it before
both.

## Tests

```bash
make host-test          # 37 stdlib unit tests: protocol, engine, TCP client
make gui-test           # headless GTK4 window against a fake packet stream
make all test-host
./scripts/dev-test-wine.sh 25
```

`gui-test` uses `xvfb-run` to open the real window with a fake server on
loopback, then checks that both packets became table rows and that the STATUS
line reached the status bar. Install `xvfb` if it is missing.

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
- `walk 25 117 0 10` made the server answer `tp 1 14881134 25 117 0`; the
  authoritative position moved from `(25,118)` to `(25,117)`. This is the
  first confirmed packet-driven movement.
- The client does not echo its own `mv`; `tp` is the confirmation. Some targets
  are ignored (blocked tile, or the client/server still settling), so confirm
  each move before sending the next one.
- The character must be standing; sitting (`rest` state 1) blocks movement.

## Honest status

- The three signatures match the current live client. Both hooks are ACTIVE
  and no packets are dropped in steady state.
- `Injector.exe --all` works end-to-end against both the fake target and the
  live client.
- The x86 stubs pass `HookStubTest.exe`, which calls the real `hooks.S` with a
  known EDX and verifies the packet pointer, the original-call replay and the
  send trampoline.
- GTK4 4.22 and PyGObject 3.56 are installed; the real window passes the
  headless Xvfb smoke test (`make gui-test`). It has not been opened against the
  real game.
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
