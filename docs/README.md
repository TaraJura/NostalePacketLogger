# Documentation

| Document | Read it when |
|---|---|
| [architecture.md](architecture.md) | you want the whole picture: components, data flow, why the DLL stays Windows code, Windows vs Linux |
| [engine.md](engine.md) | you are writing a host, bot or automation: Python API, `WorldState`, handlers, CLI commands, confirmations |
| [map.md](map.md) | maps, walkability, A*, the Map/Bot tab, path loop and auto-attack |
| [bot-config.md](bot-config.md) | monster target filters, conditional spell rules, JSON export/import |
| [handoff-2026-11-06.md](handoff-2026-11-06.md) | **read after a reboot**: current state, why Gameforge shows "Ve hře", exact reinjection + auto-attack test procedure |
| [live-session-2026-11-06.md](live-session-2026-11-06.md) | what was verified on the running client, the bugs fixed, the mechanics learned, open items |
| [feature-review-2026-11-06.md](feature-review-2026-11-06.md) | feature inventory, automated coverage and the live combination-test matrix |
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
  + state engine. Builds cleanly, **84 host tests pass**, the GTK4 window
  passes a headless smoke test, and the DLL has been verified against the
  **live NosTale client** under Wine 11: both hooks ACTIVE, drops 0,
  sit/stand via `rest`, and natural movement through the client's own MoveTo
  (`MOVE|x y` + `POS`). The live GUI bot walked a six-cell path as client walk
  packets, with no injected walks or teleport corrections. Spell support is
  live-verified: `u_s 0 3 <id>` is the normal attack (`su ... 866/868 ...`),
  the skill hook captures the client's own `u_s`, and pickup
  (`get 1 <charId> <dropId>` → server `get`) works. See
  [live-session-2026-11-06.md](live-session-2026-11-06.md).
- **Map / bot** — `NStcData.NOS` parser (779 grids); the live map is **20**
  (230x260) and 100 % of the observed entity positions are on walkable cells.
  The GTK4 Map/Bot tab renders the whole map (auto-switches from `c_map`),
  draws **8-direction A\*** paths as a continuous polyline, runs a path loop
  and can auto-attack monsters (Retaliate prioritises the one hitting you),
  pick up drops, and sit to recover below a configurable HP %. The UI has live
  HP/MP bars with a recovery banner, green/red toggles, map tooltips with
  monster names, monster target filters and ordered conditional spell rules
  scanned from `NSgtdData.NOS` → `Skill.dat` (Always/HP/MP conditions, MP,
  range, cast time, cooldown); the whole configuration can be saved/loaded as
  JSON. `make gui-test` and `make
  bot-test` pass (6 bot smokes, including the spell rotation). See
  [map.md](map.md) and
  [bot-config.md](bot-config.md).
