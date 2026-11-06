# Development

## Layout

```
docs/                 architecture, engine, hooks, IPC, live session, platform
                      guides, packet wiki
linux/                the Linux port (MinGW DLL, injector, Python host, tests)
windows/              original MSVC/C# implementation, unchanged
CLAUDE.md             working context for Claude Code
README.md             entry point
```

## Build matrix

| Target | Command | Output |
|---|---|---|
| Linux DLL | `make -C linux dll` | `linux/build/PacketLogger.dll` |
| Linux injector | `make -C linux injector` | `linux/build/Injector.exe` |
| Fake game (test) | `make -C linux test-host` | `linux/build/FakeGame.exe` |
| Everything | `make -C linux all test-host` | all three |
| Windows | `msbuild windows/PacketLogger.sln /p:Configuration=Release /p:Platform=x86` | `windows/Release/...` |
| Python host | no build; `pip install -e linux/host` optional | package `ntpl` |

The MinGW compiler is `i686-w64-mingw32-g++`. Override with
`make MINGW_CXX=...`.

## Tests

```bash
make -C linux host-test        # 84 stdlib unittest cases
make -C linux gui-test         # headless GTK4, needs xvfb
make -C linux bot-test         # 6 smokes: combat, rules/config, spell order, out-of-range, rejection, recovery
make -C linux all test-host
linux/scripts/dev-test-wine.sh 25
```

The unit tests cover the wire protocol, the TCP client (including split-line
reassembly and peer close), the engine's world-state handlers, the map
parser/A* (synthetic NOS, bad headers, walls, unreachable targets, ranking,
8-direction diagonals and no corner cutting), bot-config validation/clamping,
spawn-log parsing (spawns and the `ski` skill list), Skill.dat spell metadata,
rule-order persistence, and the monster/skill text tables. The GUI test opens the
real window under Xvfb and checks rows, status, map switch, tooltips, toggle
colors and the recovery banner. The bot smokes cover combat + path movement,
target filters + spell rules + config save/load, the scan-backed spell picker
dialog, scanned-spell rotation in list order, rejected walks and recovery.
The combat smoke asserts the `get`/`ncif`/`u_s`/`MOVE` sequence (Retaliate
picks the monster that hit us) and that the fake client's own `walk` confirms
the move. The rules smoke filters two monsters by vnum, checks that HP 40 %
triggers a spell rule (`u_s 7 3 500`), and round-trips the JSON configuration.
The reject smoke makes the server silent and asserts the bot stops after three
rejected moves. The recovery smoke starts at 20/100 HP and asserts the bot
sits, waits for full HP and then stands. The Wine script covers the x86 stubs
(`HookStubTest.exe`), the DLL lifecycle and the command path with a fake game.

Nothing in this repository can test the real detours against the real client;
that requires the live game. The procedure is:

```bash
export WINEPREFIX="$HOME/.wine-nostale"   # or the prefix the game uses
linux/scripts/inject.sh
linux/scripts/host.sh status              # both hooks ACTIVE, addresses non-zero
linux/scripts/host.sh send "rest 1 1 <charId>"   # ensure standing
linux/scripts/host.sh walk X Y            # client MoveTo; natural walk
linux/scripts/host.sh cast <skill> --target-id <id>   # waits for su
linux/scripts/gui.sh                      # packet view + Map/Bot tab
```

In the Map/Bot tab, load the map (auto-detected/default 20), click waypoints,
and Start the loop with auto-attack enabled. The bot stands first if needed,
then walks one cell at a time through the client's own MoveTo (`MOVE|x y`).
See [map.md](map.md).

The file log is `<prefix>/drive_c/NosTalePacketLog.txt`. Confirm each action
from the server (`tp`/`mv`, `su`, `rest`); the client does not reliably echo its
own actions. A `QUIT` removes the hooks but leaves the module and trampoline
mapped until the game exits, so re-injection after `QUIT` needs a new DLL
filename or a client restart. See [live-session-2026-11-06.md](live-session-2026-11-06.md).

When adding a Python feature, add a test in `linux/host/tests/`. Keep the core
(`protocol`, `client`, `engine`) free of third-party dependencies so the tests
run on any Python 3.10+.

## Conventions

### C++ (DLL and injector)

- C++17, `-fno-exceptions -fno-rtti`, no iostreams in the DLL.
- Windows API only; the DLL must not depend on libwinpthread. Use
  `CRITICAL_SECTION` or the existing `PacketQueue`, not `std::mutex`.
- No allocation, logging, or socket calls inside a hook. The hook may only call
  `PacketQueue::push`.
- Bounded everything: queue slots, line length, socket timeouts.
- Restore every byte you patch. `initialize()` must stay side-effect free.
- Lowercase MinGW headers (`<windows.h>`).
- Assembly goes in `hooks.S`, not inline `__asm`, so MinGW and clang both work.

### Python

- Standard library for `protocol`, `client`, `engine`, `mapdata`, `cli`;
  PyGObject only in `gui.py`, imported lazily by the CLI. Map rendering in the
  CLI uses zlib and a hand-written PNG writer; the GUI uses pycairo.
- Type hints on public functions and dataclasses.
- No GTK calls outside the main loop; the reader thread only fills a queue.
- Parse packets tolerantly: a short or non-numeric packet is ignored, not fatal.
- CLI exit codes: 0 success, 1 no result (e.g. no status), 2 connection or
  usage failure.

### Documentation

- English, current state, no changelog prose. Fix a line that stopped being
  true instead of adding a new one.
- Packet facts go in `docs/protocol/`; implementation facts go in the guide for
  that platform.
- Mark anything not verified against the live client as such.

## Adding a packet

1. **Observe.** Run `ntpl monitor --log capture.log`, perform the action in the
   game, find the new lines.
2. **Document.** Add the opcode to the right file in `docs/protocol/` with
   direction, fields, an example and the behaviour. If you do not know the
   fields yet, put it in `unknown.md`.
3. **Parse.** Add a handler to `ntpl.engine.Engine` only when the format is
   known well enough to be useful. Handlers are tolerant by contract.
4. **Test.** Add a case to `linux/host/tests/test_engine.py` with a real
   captured line.
5. **Use.** Read the field from `WorldState` in the CLI/GUI or in the next
   automation step.

## Updating signatures

See [hooking.md](hooking.md#maintaining-the-signatures-after-a-client-patch).
Keep the Linux and Windows pattern strings in sync until the Windows tree is
retired.

## Release checklist

- [ ] `make -C linux clean all test-host` is warning-free.
- [ ] `make -C linux host-test gui-test bot-test` passes.
- [ ] `linux/scripts/dev-test-wine.sh` reports the expected `NOT-FOUND` status
      and the `send failed` reply.
- [ ] `git status` contains only intended files; `build/`, `__pycache__/`, logs
      and capture files are ignored.
- [ ] Docs updated if behaviour, paths, commands or env vars changed.
- [ ] No credentials, session data or `usercache-*`/`webcache-*` content in any
      file.

## Git

This repository has its own `.git` and remote
(`github.com/TaraJura/NostalePacketLogger`). The home directory on this machine
also contains a stray `~/.git`; do not commit to that one and do not run `gc` or
`clean` in it. Commit from this directory only.
