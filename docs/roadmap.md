# Roadmap

The original project's long-term goal was an AI-driven autonomous player. The
Linux port keeps that goal but makes the intermediate steps independent of
Windows.

## Phase 1 — Packet logger (done)

- Inject a DLL, hook the game's recv and send functions.
- Display packets in real time.
- Send packets back into the game.
- Windows: injector + named pipes + WinForms GUI.
- Linux: MinGW injector + DLL + TCP loopback + Python CLI + GTK4 GUI.
- **Live 2026-11-06:** both hooks ACTIVE in the real client, `rest` toggled
  sit/stand, and the GUI bot walked a six-cell A* path through the client's
  own MoveTo (`MOVE|x y`), with its normal animation and no injected walks.

## Phase 2 — Protocol wiki (in progress)

`docs/protocol/` already covers movement, map, entities, stats, combat, chat,
NPCs and inventory at the opcode level. It is built by observing live gameplay.

Next:

- Capture a full session on the current client and reconcile the existing
  entries with what the client actually sends today.
- Move `unknown.md` entries into their proper files as formats become clear.
- Add the exact field layout (types, widths, sentinel values) for the packets
  the engine already parses: `walk`, `mv`, `in`, `out`, `c_map`, `at`, `stat`.
- Add a short "capture recipe" for each opcode: what to do in the game to make
  it appear.

## Phase 3 — Game state model (seed exists)

`ntpl.engine` turns packets into a `WorldState`:

- own map, position, HP/MP;
- entities keyed by id with kind and position;
- a chat ring.

The handlers are deliberately tolerant. Next:

- Add inventory (`inv`, `ivn`) and shop (`shop`) parsing.
- ~~Track target selection (`ncif`, `u_s`)~~ — done; the last target/skill are
  in `WorldState`, and the bot has its own spell/attack cooldowns.
- Add an `events` stream (entity appeared, entity died, map changed) so the
  future agent can react instead of polling.
- Persist a session: packets + derived events + timestamps.

## Phase 3.5 — Map, path, combat and configuration (implemented and run live)

The client's `NStcData.NOS` is parsed into per-map walkability grids (779 maps;
one byte per cell; `0x00` walkable). Live maps so far include **2, 3, 4, 20,
98, 103** and **104**, and observed `mv` positions are on walkable cells.

- `ntpl map info/render/best` inspect the grids from the CLI.
- The GTK4 **Map / Bot** tab renders the whole map, switches it automatically
  from `c_map`/`at`, overlays live entities with hover tooltips, takes click
  waypoints and draws an **8-direction A\*** path as a continuous polyline.
- Auto-attack targets the nearest allowed monster: `TARGET|id` selects it in
  the client (target ring + the client's own `ncif`, which the server
  validates), then `u_s 0 3 <id>` on a cooldown; Retaliate prioritises the
  attacker; a refused target is dropped from the world state and skipped
  for a few seconds, and an out-of-range target is chased on foot until it
  is in range (the game's click-a-monster behaviour).
- The bot stands first (`rest`) and walks three-cell legs through the
  client's own MoveTo; while a leg runs it polls `POS` and hands the next leg
  off one cell early (the click-to-walk rule), so walking is continuous.
- Recovery sits below a configurable HP %, waits for full and resumes.
- Targets can be filtered by monster vnum (all/only/except, plus unknown-vnum
  handling). Spells come from the client's `NSgtdData.NOS` → `Skill.dat`
  catalog (name, MP, range, cast time, cooldown, target); ordered rules use
  Always/my HP/my MP and a rotation, with MP/range/cooldown enforcement. The
  whole bot configuration is JSON save/load.
- Monster names come from the client's `NSlangData_UK.NOS`; the spawn log
  backfills vnums for entities that predate the host connection.

`make gui-test` and the six `make bot-test` smokes pass. The live run verified
`MOVE`, client-side selection (`TARGET|id` → client `ncif` → `su` for the
selected id), pickup and map switching. Server-sent `gp` portals and `wp`
Time-Space entrances are drawn on the map, and a **Bot log** tab keeps a
timestamped action history. See [map.md](map.md) and
[bot-config.md](bot-config.md).

## Phase 4 — Autonomous agent

The first reversible loop is now possible and runs live: `MOVE|x y` calls the
client's own MoveTo, so the character pathfinds and animates normally; `POS`
reads its local position; `rest` controls sit/stand; the Map/Bot tab runs a
confirmed path loop with auto-attack, recovery, target filters and conditional
spell rules. It is not autonomous yet: some tiles are rejected and the
self-target spell shape is live-captured (`u_s 7 1 <charId>` → `su 873`).

When it starts:

- Use the confirmed walk loop as the first primitive; never spam targets.
- Stand up (`rest`) before moving; sitting is the regeneration state.
- Keep a human-visible log of every injected packet and the state that
  motivated it.
- Rate-limit and stop on unexpected state.
- Treat Gameforge's terms and the account risk as a first-class constraint.

## Near-term engineering tasks

1. ~~Install NosTale and run the injector against the real client~~ — done
   2026-11-06.
2. ~~Verify the signatures~~ — both hooks ACTIVE on the live client.
3. **Closed movement loop** — implemented and run live: `MOVE|x y` → client
   pathfinding/animation → wait for the client's own `walk` → next cell, A*
   over walkable map cells, path loop, auto-attack. Still to do: a
   rejected-tile cache from live attempts and a rate limit.
4. ~~Run the loop live~~ — movement, auto-attack and pickup passed on
   2026-11-06; `u_s 0 3 <id>` is the verified normal attack and
   `u_s 7 1 <charId>` → `su 873` verifies the self-target shape.
5. ~~Capture the client's own `walk`/`u_s` SEND packets~~ — done; the send hook
   sees `walk` and the skill hook captures `u_s` (checksum `1 if (x+y)%3 == 1`).
6. **Reconcile the protocol wiki** with the live session (`rest`, `tp`, `cond`,
   `stat`) and extend the engine handlers.
7. **Decide the fate of the Windows tree**: keep it as the reference, or port
   the same TCP transport there and retire the named pipes.

## Known risks

| Risk | Mitigation |
|---|---|
| Client patch breaks the signatures | non-fatal `NOT-FOUND`; documented re-derivation |
| Wine differs from Windows in hook behaviour | test on the real client first; keep the Windows tree as reference |
| Client patch moves `MoveTo`/player object | validate the prologue and pointer; `MOVE` reports a build mismatch instead of crashing |
| Sending from the worker thread is not thread-safe in the game | keep manual sends infrequent; document the symptom |
| Double free of the Delphi string | instrument if it ever appears; see `docs/hooking.md` |
| Account ban for automation | owner's decision; no evasion features |
| Two game instances collide on the port | `NTPL_PORT` per instance; one host per DLL |
