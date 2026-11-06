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
- Track cooldowns and target selection (`ncif`, `u_s`).
- Add a `events` stream (entity appeared, entity died, map changed) so the
  future agent can react instead of polling.
- Persist a session: packets + derived events + timestamps.

## Phase 3.5 — Map, path and auto-attack (implemented, live test pending)

The client's `NStcData.NOS` is parsed into per-map walkability grids (779 maps;
one byte per cell; `0x00` walkable). The live map is **20** (230x260) and 100 %
of the observed `mv` positions are on walkable cells.

- `ntpl map info/render/best` inspect the grids from the CLI.
- The GTK4 **Map / Bot** tab renders the grid, overlays live entities, takes
  click waypoints, computes an A* path, and can **loop** it.
- Auto-attack targets the nearest monster in range (`ncif` + a configurable
  `u_s` template) on a cooldown.
- The bot stands first (`rest`), walks one cell at a time and waits for the
  server's `tp`/`mv` before the next.

`make gui-test` and `make bot-test` pass headlessly. The loop has **not** been
run against the live client yet; the live run and the exact `u_s` field order
are the next steps. See [map.md](map.md).

## Phase 4 — Autonomous agent

The first reversible loop is now possible and runs live: `MOVE|x y` calls the
client's own MoveTo, so the character pathfinds and animates normally; `POS`
reads its local position; `rest` controls sit/stand; the Map/Bot tab runs a
confirmed path loop with auto-attack. It is not autonomous yet: a move is
confirmed before the next one, some tiles are rejected, and the `u_s` skill
layout is still unverified.

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
4. **Run the loop live** — movement passed on 2026-11-06 (a six-cell path as
   client `walk` packets). Still to do: enable auto-attack on a real target and
   lock the `u_s` field order from a live cast.
5. ~~Capture the client's own `walk` SEND packets~~ — done; the send hook sees
   them and the checksum is `1 if (x+y)%3 == 1`. The skill packets still need
   the same treatment.
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
