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

Last checked 2026-11-06.

| What | State |
|---|---|
| `windows/` | Original MSVC/C# implementation, moved unchanged. Builds with Visual Studio; not exercised on this machine. |
| `linux/` build | `make -C linux all test-host` is warning-free; all three outputs are PE32 i386. |
| Linux host tests | 29/29 pass (`make -C linux host-test`). |
| x86 hook stubs | `HookStubTest.exe` (Wine) calls the real `hooks.S` detours and verifies the EDX packet pointer at `20(%ebp)`, the original-call replay and the send trampoline. Passes. |
| GUI | Headless GTK4 smoke test passes (`make -C linux gui-test`, Xvfb): fake stream → 2 rows + STATUS in the status bar. |
| Injector | Verified under Wine end-to-end: `Injector.exe --all` discovers a fake target named `NostaleClientX.exe`, remote `LoadLibraryA` succeeds and the DLL starts. Not yet run against the real client. |
| Wine integration | Verified with Wine 11 + the fake target: DLL loads, listens on `127.0.0.1:47821`, host gets `STATUS`, `SEND` command path answers. Hooks correctly reported `NOT-FOUND` because the fake target is not the game. |
| Real client | **Not tested.** NosTale is not installed in a Wine prefix here. The three signatures are inherited from the Windows build and have not been re-verified on the current client. |
| GUI | PyGObject 3.56 / GTK 4.22 present; `ntpl.gui` imports. Not opened on a display in this environment. |

## Commands

Every Bash tool call is a fresh shell. Use absolute paths or `cd` in the same
command.

```bash
cd ~/games/gameforge/NostalePacketLogger

# Linux build (MinGW-w64 i686; installed via apt)
make -C linux all test-host
make -C linux check                 # file(1) on the three PE32 outputs
make -C linux host-test             # 29 stdlib unittest cases
make -C linux gui-test              # headless GTK4 smoke test (xvfb)
make -C linux clean

# End-to-end without the game (temporary Wine prefix, deletes itself)
./linux/scripts/dev-test-wine.sh 25

# With the game running in its own prefix
export WINEPREFIX="$HOME/.wine-nostale"       # never the Gameforge prefix
linux/scripts/inject.sh
linux/scripts/host.sh status
linux/scripts/host.sh monitor [--engine] [--filter TEXT] [--log FILE]
linux/scripts/host.sh send "walk 19 146 0 10"
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
  `TYPE|PAYLOAD` lines over TCP `127.0.0.1:47821`.
- **Injector** (`linux/src/injector/`): expands `VirtualAllocEx` +
  `WriteProcessMemory` + `CreateRemoteThread(LoadLibraryA)`, with a real
  `WaitForSingleObject` / `GetExitCodeThread` check. Enumerates Windows PIDs by
  executable name, so it works under Wine without Linux PID mapping.
- **Host** (`linux/host/ntpl/`): `protocol.py` (parse/encode), `client.py`
  (TCP, retry, reader thread), `engine.py` (`WorldState`), `cli.py`, `gui.py`.
  Standard library only outside the GUI.
- **Docs**: `docs/architecture.md`, `docs/hooking.md`, `docs/ipc.md`,
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
- **Git:** commit from this directory only. The home directory has a stray
  `~/.git`; never commit there and never run `gc`/`clean` on it.
- **Privacy:** never read, copy or write credentials, session data, or the
  contents of `usercache-*` / `webcache-*`.
- **Terms of service:** Gameforge prohibits automation. The owner accepts the
  account risk; do not add detection-evasion features.

## Not verified / next

1. Install NosTale in a dedicated Wine prefix and run the injector.
2. `host.sh status`: both addresses non-zero, both hooks `ACTIVE`.
3. Capture one session, reconcile `docs/protocol/` and the engine handlers.
4. Fix whatever the real client exposes (calling convention, packet formats,
   Wine quirks), then update this file and the docs in the same session.
