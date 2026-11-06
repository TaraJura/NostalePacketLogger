# Documentation

| Document | Read it when |
|---|---|
| [architecture.md](architecture.md) | you want the whole picture: components, data flow, why the DLL stays Windows code, Windows vs Linux |
| [engine.md](engine.md) | you are writing a host, bot or automation: Python API, `WorldState`, handlers, CLI commands, confirmations |
| [map.md](map.md) | maps, walkability, A*, the Map/Bot tab, path loop and auto-attack |
| [live-session-2026-11-06.md](live-session-2026-11-06.md) | what was verified on the running client, the bugs fixed, the mechanics learned, open items |
| [hooking.md](hooking.md) | you need to understand or repair the in-game detours, signatures, assembly stubs, packet injection |
| [ipc.md](ipc.md) | you are writing a host, a bot, or another front end and need the wire protocol |
| [linux.md](linux.md) | you are building or running the Linux/Wine side |
| [windows.md](windows.md) | you are building or running the original MSVC/C# side |
| [development.md](development.md) | you are changing the code: layout, conventions, tests, adding packets |
| [roadmap.md](roadmap.md) | you want the phases and the next concrete tasks |
| [troubleshooting.md](troubleshooting.md) | something does not connect, does not appear, or the client crashes |
| [protocol/](protocol/README.md) | you need the meaning of a specific packet opcode |

The packet wiki in `protocol/` is shared by both platforms: the transport and
the host differ, the game's packets do not.

## Status snapshot

- **Windows tree (`windows/`)** — the original working implementation. Injector
  + DLL + WinForms GUI, named pipes.
- **Linux tree (`linux/`)** — MinGW DLL + TCP loopback + Python CLI + GTK4 GUI
  + state engine. Builds cleanly, **48 host tests pass**, the GTK4 window
  passes a headless smoke test, and the DLL has been verified against the
  **live NosTale client** under Wine 11: both hooks ACTIVE, drops 0,
  sit/stand via `rest`, and a `walk` moved the server's authoritative position
  to the requested tile (`tp 1 14881134 25 117 0`). Spell support
  (`su` parsing, `ntpl cast`) is in; the exact `u_s` field order still needs
  one live cast. See [live-session-2026-11-06.md](live-session-2026-11-06.md).
- **Map / bot** — `NStcData.NOS` parser (779 grids); the live map is **20**
  (230x260) and 100 % of the observed entity positions are on walkable cells.
  The GTK4 Map/Bot tab renders the grid, draws A* paths, runs a path loop and
  can auto-attack nearby monsters. `make gui-test` and `make bot-test` pass.
  See [map.md](map.md).
