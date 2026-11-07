<img width="1914" height="1080" alt="artwork (2)" src="https://github.com/user-attachments/assets/00d546c1-047a-4378-afc7-8c024cc9a556" />

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
make -C linux host-test                      # 119 Python unit tests
make -C linux gui-test                       # headless GTK4 + map smoke (xvfb)
make -C linux bot-test                       # 6 fake-server bot smokes

# no game required: x86 hook stubs + DLL lifecycle + TCP + command path
linux/scripts/dev-test-wine.sh 25

# with the game running in its own prefix:
linux/scripts/inject.sh
linux/scripts/host.sh status
linux/scripts/host.sh monitor
linux/scripts/gui.sh

# agent access (MCP over stdio; owns the DLL link, GUI joins on 47822)
PYTHONPATH=linux/host python3 -m ntpl mcp
linux/scripts/gui.sh --port 47822
```

## Quick start on Windows

```powershell
msbuild windows\PacketLogger.sln /p:Configuration=Release /p:Platform=x86
```

Run `Injector.exe` as administrator, then open `PacketLoggerGUI`.

## Documentation

Start with [docs/architecture.md](docs/architecture.md), then:

- [docs/engine.md](docs/engine.md) — the Python host API, state model, CLI
  commands and action confirmations
- [docs/map.md](docs/map.md) — `NStcData` maps, walkability, 8-direction A*,
  the Map/Bot tab, path loop, auto-attack and tooltips
- [docs/bot-config.md](docs/bot-config.md) — monster target filters,
  conditional spell rules and JSON export/import
- [docs/mcp.md](docs/mcp.md) — MCP server for agents: state, movement,
  combat, raw packets, and the local broker that lets the GUI watch too
- [docs/handoff-2026-11-06.md](docs/handoff-2026-11-06.md) — current state and
  the exact post-reboot reinjection + auto-attack test procedure
- [docs/live-session-2026-11-06.md](docs/live-session-2026-11-06.md) — what was
  verified on the running client, the bugs fixed and the open items
- [docs/feature-review-2026-11-06.md](docs/feature-review-2026-11-06.md) —
  feature inventory, test coverage and live combination results
- [docs/hooking.md](docs/hooking.md) — how the detours, patterns and assembly
  stubs work, and how to repair them after a client patch
- [docs/ipc.md](docs/ipc.md) — the TCP message protocol
- [docs/linux.md](docs/linux.md) — Wine, MinGW, host, GUI, tests
- [docs/windows.md](docs/windows.md) — the original MSVC/C# tree
- [docs/development.md](docs/development.md) — layout, conventions, adding packets
- [docs/roadmap.md](docs/roadmap.md) — phases and next tasks
- [docs/troubleshooting.md](docs/troubleshooting.md) — symptom → cause → fix

## Status

The Linux port builds, its 119 unit tests pass, the GTK4 window and six bot
smoke tests pass, and the real injector has been run against the **live
NosTale client** under Wine 11:

- both hooks `ACTIVE` (`recv=0x004DBB4D send=0x004EB5C4 tnt=0x0055AA84`),
  `drops recv=0`;
- `rest 1 1 14881134` stood the character up; the server answered
  `rest 1 14881134 0`;
- movement calls the client's own MoveTo through the DLL's `MOVE|x y`; the
  client pathfinds, animates and sends its own `walk` packet. `POS` reads its
  local position. Injecting a raw `walk` (checksum `1 if (x+y)%3 == 1`) moves
  only the server, and the correction teleports the client;
- the live GUI bot walked `(26,133)→(23,130)` as six client `[SEND] walk`
  packets, with no injected walks, probes or teleports;
- auto-attack selects the monster in the client (`TARGET|id`, so the client
  highlights it and sends its own `ncif 3 <id>`) and then sends
  `u_s 0 3 <id>`; the server answers with `su ... 866/868 ...`, and
  pickup/ground drops are confirmed with `get`. Live 2026-11-07:
  `TARGET|8684` → client `[SEND] ncif 3 8684` → `su ... 3 8684 855 ...`;
- the skill hook captures the client's own `u_s` packets (`skill=ACTIVE`).

`linux/scripts/host.sh walk X Y` uses `MOVE` and waits for the client's own
`walk` packet. The character must be **standing**: sitting blocks movement and
is controlled by the `rest` packet. Some targets are still ignored (blocked
tile/entity); the bot stops after three rejected steps.

The client's own map grids are parsed from `NostaleData/NStcData.NOS`; live
maps so far include **2, 3, 4, 20, 98, 103** and **104**, and every observed
entity position is on walkable cells.
The GTK4 **Map / Bot** tab renders the map (automatic map switch on `c_map`),
takes click waypoints, computes an **8-direction A\*** path (diagonals, no
corner cutting, continuous polyline), and can loop it while auto-attacking
monsters (Retaliate picks the one hitting you), picking up drops, and sitting
to recover below a configurable HP %. It shows live HP/MP bars plus a red
recovery banner, green/red active toggles, hover tooltips with monster names
from the client's language archive, server-sent portals (`gp`) and Time-Space
entrances (`wp`, with the nosapki stone icon and the client's own "Required
Level N" wording), and an explicit unknown-vnum option for monsters that
spawned before injection. A third top-level **Bot log** tab keeps a
timestamped, selectable record of every action (attacks, TARGET selections and
refusals, walks, pickups, recovery, DLL errors) for debugging. Targets can be filtered by monster
vnum; ordered spell rules cast by HP/MP conditions; the whole bot configuration
is saved/loaded as JSON. `map` commands inspect/render the grids; `make
bot-test` exercises the loop, rules, spell order, rejection and recovery against a fake
server. See [docs/map.md](docs/map.md) and [docs/bot-config.md](docs/bot-config.md).

Gameforge's terms of service prohibit automation. This is a tool for the
account owner's own client; using it on a live account is their decision and
risk.
