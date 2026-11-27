# Documentation

| Document | Read it when |
|---|---|
| [architecture.md](architecture.md) | you want the whole picture: components, data flow, why the DLL stays Windows code, Windows vs Linux |
| [engine.md](engine.md) | you are writing a host, bot or automation: Python API, `WorldState`, handlers, CLI commands, confirmations |
| [map.md](map.md) | maps, walkability, A*, the Map/Bot tab, path loop and auto-attack |
| [bot-config.md](bot-config.md) | monster target filters, conditional spell rules, JSON export/import |
| [bazar.md](bazar.md) | the NosBazar tab: the full Item.dat Items catalog with Monitor/Relist ticks and filters, two-view (NosMall / NPC resale) cheapest-listing monitor, gold/ND profit and markup, floor-protected relisting, per-client JSON |
| [monitoring-config.md](monitoring-config.md) | the monitoring set as a bazar config: JSON fields, add/replace import, the prepared 17-item profitable-NosMall profile, the full 1,184-group export, regeneration and verification |
| [minigame.md](minigame.md) | the Minigame tab: Sheep Farm key spam and player-only focus, the `KEY\|` DLL command, and Miniland production (Great Quarry `mg` result/claim, reward tiers, production points) |
| [taming.md](taming.md) | the Map/Bot Pet tamer: the Pet Trainer card's Whip + Tame loop, the live taming flow (`st` HP, `msgi` 2614/1887/2631/2623), pet slots and the `sc_p` panel |
| [stats.md](stats.md) | the Stats tab: player DPS meter built from the decoded `su` damage, windows, totals and limits |
| [database.md](database.md) | local SQLite game/packet database: client Item/monster/skill/map/NosMall data, protocol wiki, public GitHub packet projects, CLI queries and NosMall export |
| [context.md](context.md) | quick map of the current state, user config/database paths and next likely work |
| [mcp.md](mcp.md) | expose the engine to an AI agent over MCP: tools, the DLL broker, Claude Code config, security |
| [autonomous-play.md](autonomous-play.md) | the policy for driving a live character: observe → decide → act → survive, resync, log entity seeding, target/HP/survival thresholds, handover, failure modes |
| [handoff-2026-11-27.md](handoff-2026-11-27.md) | **latest handoff**: the Pet Trainer tamer bot mode (Chicken 333, Whip then Tame, pet slots), the live taming protocol, the TARGET re-derivation for client 0.9.3.3262 (DLL 0.3.3) and the host 0.9.13 test pass |
| [handoff-2026-11-26.md](handoff-2026-11-26.md) | client 0.9.3.3262/DLL 0.3.2, the NosBazar Items catalog (all 7,805 DB rows, Monitor/Relist ticks, filters), config import, name-aware pricing, relist by owner id, the 17-item monitoring profile, the host 0.8.0 retest (packet-pump fix, UX standardization, GTK4 mixin split), the Great Quarry production minigame (live protocol, Stop/Storno, 40 s Play loop; host 0.9.10), the Speed + burst-only settle, and the raid-leave guard fix (`raid 1 0` unblocks Play; host 0.9.11) |
| [handoff-2026-11-19.md](handoff-2026-11-19.md) | previous session: multi-client logs and window numbering, follow mode (loot/attack), strict focus, recovery priority and configured thresholds, route resume/click behavior, the 96 MB log freeze fix |
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
| [reusable-projects.md](reusable-projects.md) | you want to reuse the MIT-licensed Gameforge/NosTale projects cloned next to this repo (clientless login, launcher emulation, protocol crypto, widget layouts) |
| [protocol/](protocol/README.md) | you need the meaning of a specific packet opcode |

The packet wiki in `protocol/` is shared by both platforms: the transport and
the host differ, the game's packets do not.

## Status snapshot

- **Windows tree (`windows/`)** — the original working implementation. Injector
  + DLL + WinForms GUI, named pipes.
- **Linux tree (`linux/`)** — MinGW DLL + TCP loopback + Python CLI + GTK4 GUI
  + state engine. Builds cleanly, **389 host tests pass**, the GTK4 window
  passes six headless smokes (packet window, client picker, multi-client
  locks, Family, Family auto-invite, Minigame), and the DLL has
  been verified against the **live NosTale client** under Wine 11: both hooks
  ACTIVE, drops 0, sit/stand via `rest`, and natural movement through the
  client's own MoveTo (`MOVE|x y` + `POS`). Several clients can run side by
  side: each DLL takes the next free port and publishes it in
  `C:\ntpl-<pid>.port`, and the GUI's startup picker attaches or injects the
  chosen window. The live GUI bot walked a six-cell path as client walk
  packets, with no injected walks or teleport corrections. Spell support is
  live-verified: `u_s 0 3 <id>` is the normal attack (`su ... 866/868 ...`),
  the skill hook captures the client's own `u_s`, and pickup
  (`get 1 <charId> <dropId>` → server `get`) works. See
  [live-session-2026-11-06.md](live-session-2026-11-06.md).
- **Map / bot** — `NStcData.NOS` parser (779 grids); live maps so far include
  **2, 3, 4, 20, 98, 103** and **104**, and observed entity positions are on
  walkable cells. The GTK4 Map/Bot tab renders the whole map (auto-switches
  from `c_map`), draws **8-direction A\*** paths as a continuous polyline,
  runs a path loop and can auto-attack monsters (Retaliate prioritises the
  one hitting you), pick up drops, and sit to recover below configurable
  HP/MP % thresholds. Attacks first select the monster **in the client** (`TARGET|id`, which
  sends the client's own `ncif`), because the server validates `u_s` against
  that selection. Server-sent `gp` portals and `wp` Time-Space entrances are
  drawn on the map. A third **Bot log** tab records every action with a
  timestamp for debugging. The UI has live
  HP/MP bars with a recovery banner, green/red toggles, map tooltips with
  monster names, monster target filters and ordered conditional spell rules
  scanned from `NSgtdData.NOS` → `Skill.dat` (Always/HP/MP conditions, MP,
  range, cast time, cooldown); the whole configuration can be saved/loaded as
  JSON. The fourth **NosBazar** tab has an **Items** catalog of the whole
  Item.dat database with Monitor/Relist ticks, Monitored/Relisting filters
  and saved-config import, then monitors the player auction house with
  third-level **NosMall** and **NPC resale** views and can relist with a
  floor, ported from Nostale-NB. The seventh **Minigame** tab
  spams a key and keeps the nearest other player selected for the Sheep Farm
  raid. `make gui-test` and `make bot-test` pass (48 bot smokes, including the
  spell rotation, HP/MP recovery, finish-kill-before-recovery, loot approach,
  the NosBazar monitor and relist, the speed-boost control, and cross-client
  Follow packet mirroring). See [map.md](map.md),
  [bot-config.md](bot-config.md), [bazar.md](bazar.md),
  [monitoring-config.md](monitoring-config.md) and
  [minigame.md](minigame.md).
