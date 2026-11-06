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
| State model | `ntpl.engine.WorldState` | Phase 3 seed: packets become a world, not just a list |

See `../docs/architecture.md`, `../docs/ipc.md`, `../docs/hooking.md` and
`../docs/linux.md` for the full picture.

## Layout

```
linux/
├── Makefile                 cross-build for the DLL, injector and test host
├── src/dll/                 injected 32-bit DLL
│   ├── hooks.S              x86 detours (replaces MSVC inline asm)
│   ├── packetlogger.*       signatures, hook install/remove, packet injection
│   ├── packet_queue.h       fixed-capacity multi-producer ring
│   ├── tcp_server.*         loopback server
│   ├── memory.*             pattern scan, VirtualProtect patching
│   ├── nostale_string.h     Delphi string layout
│   ├── log.*, config.*      file log and environment config
│   └── dllmain.cpp          worker thread, command loop, shutdown
├── src/injector/            32-bit injector (Windows PIDs, works under Wine)
├── host/ntpl/               Python package (protocol, client, engine, cli, gui)
├── host/tests/              stdlib unittest suite
├── scripts/                 inject.sh, host.sh, dev-test-wine.sh
└── test/fake_game.cpp       LoadLibraryA fixture for the Wine test
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
make -C linux host-test          # protocol, TCP client, engine
make -C linux gui-test           # headless GTK4 window + fake packet stream
./linux/scripts/dev-test-wine.sh 25
```

`dev-test-wine.sh` creates a temporary Wine prefix and runs two things:

1. `HookStubTest.exe` calls the real `hooks.S` detours with a known EDX and
   checks that the packet pointer, the original-call replay and the trampoline
   continuation are all correct. This is the regression test for the x86 ABI.
2. a copy of `FakeGame.exe` runs as `NostaleClientX.exe` with `--no-load`; the
   **real** `Injector.exe --all` finds it by name and injects the DLL; the
   Python host then connects over TCP. The DLL reports `NOT-FOUND` for the game
   signatures (correct: the fake target is not the game) and the test proves the
   injector path, the DLL lifecycle, the TCP server and the command path.

The prefix is deleted afterwards. `gui-test` needs `xvfb` and GTK4, but no
game and no real display.

## Run with the game

```bash
# 1. a dedicated prefix -- do not reuse ~/.wine or the Gameforge client prefix
export WINEPREFIX="$HOME/.wine-nostale"
wineboot -u
# 2. install and start NosTale inside that prefix (out of scope here)
# 3. inject into every running client in the prefix
linux/scripts/inject.sh
# 4. talk to it
linux/scripts/host.sh status
linux/scripts/host.sh monitor
linux/scripts/host.sh monitor --engine
linux/scripts/host.sh gui
```

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
| `NTPL_LOG` | DLL | log path, default `C:\NosTalePacketLog.txt` |
| `NTPL_QUIET` | DLL | `1` disables the file log |
| `NTPL_WINE` | scripts | wine binary |
| `NTPL_WINEPREFIX` | `inject.sh` | prefix to inject into |
| `NTPL_TEST_PREFIX` | test script | reuse a prefix instead of a temp one |

## Honest status

- The three signatures are carried over from the Windows build. They have
  **not** been re-verified against the live client on this machine, because the
  game is not installed in a Wine prefix yet. Run `host.sh status` after the
  first injection: `recv=... send=...` must be non-zero and both hooks must say
  `ACTIVE`. If they say `NOT-FOUND`, see `../docs/hooking.md`.
- The real injector has been exercised end-to-end under Wine against a fake
  target named `NostaleClientX.exe`. It has not been run against the real
  client.
- The real detours cannot be exercised without the game. `HookStubTest.exe`
  covers the stubs' ABI; the Wine test covers the injector, DLL lifecycle, TCP
  and command path.
- Gameforge's terms of service prohibit automation; using this on a live account
  is the account owner's decision and risk.
