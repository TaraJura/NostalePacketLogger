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

## Quick start on Linux

```bash
sudo apt install g++-mingw-w64-i686          # once
make -C linux all test-host                  # DLL + injector + test fixtures
make -C linux host-test                      # 37 Python unit tests
make -C linux gui-test                       # headless GTK4 smoke test (xvfb)

# no game required: x86 hook stubs + DLL lifecycle + TCP + command path
linux/scripts/dev-test-wine.sh 25

# with the game running in its own prefix:
linux/scripts/inject.sh
linux/scripts/host.sh status
linux/scripts/host.sh monitor
linux/scripts/gui.sh
```

## Quick start on Windows

```powershell
msbuild windows\PacketLogger.sln /p:Configuration=Release /p:Platform=x86
```

Run `Injector.exe` as administrator, then open `PacketLoggerGUI`.

## Documentation

Start with [docs/architecture.md](docs/architecture.md), then:

- [docs/hooking.md](docs/hooking.md) — how the detours, patterns and assembly
  stubs work, and how to repair them after a client patch
- [docs/ipc.md](docs/ipc.md) — the TCP message protocol
- [docs/linux.md](docs/linux.md) — Wine, MinGW, host, GUI, tests
- [docs/windows.md](docs/windows.md) — the original MSVC/C# tree
- [docs/development.md](docs/development.md) — layout, conventions, adding packets
- [docs/roadmap.md](docs/roadmap.md) — phases and next tasks
- [docs/troubleshooting.md](docs/troubleshooting.md) — symptom → cause → fix

## Status

The Linux port builds, its 35 unit tests pass, the GTK4 window passes a
headless smoke test, and the real injector has been run against the **live
NosTale client** under Wine 11:

- both hooks `ACTIVE` (`recv=0x004DBB4D send=0x004EB5C4 tnt=0x0055AA84`),
  `drops recv=0`;
- `rest 1 1 14881134` stood the character up; the server answered
  `rest 1 14881134 0`;
- `walk 25 117 0 10` made the server answer `tp 1 14881134 25 117 0`, moving
  the authoritative position from `(25,118)` to `(25,117)`.

`linux/scripts/host.sh walk X Y` sends a walk and waits for the server's
`tp`/`mv` confirmation. The character must be **standing**: sitting blocks
movement and is controlled by the `rest` packet. Some targets are ignored
(blocked tile, or the client still settling), so confirm each move before
sending the next one.

Gameforge's terms of service prohibit automation. This is a tool for the
account owner's own client; using it on a live account is their decision and
risk.
