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
  sit/stand, and `walk 25 117` moved the server's authoritative position to
  `(25,117)` (`tp 1 14881134 25 117 0`).

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

## Phase 4 — Autonomous agent

The first reversible loop is now possible: `ntpl walk X Y` sends a walk and
waits for the server's `tp`/`mv` confirmation, and `rest` controls sit/stand.
It is not autonomous yet: a move must be confirmed before the next one, some
tiles are rejected, and the client's own walk SEND packets are not captured.

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
3. **Build the closed movement loop**: `walk` → wait for `tp`/`mv` → next
   target, with a rejected-tile list and a rate limit.
4. **Capture the client's own `walk` SEND packets** (the current send hook sees
   `rest`/`sortopen`/`pulse` but not walks) to learn the exact checksum/path
   behaviour.
5. **Reconcile the protocol wiki** with the live session (`rest`, `tp`, `cond`,
   `stat`) and extend the engine handlers.
6. **Decide the fate of the Windows tree**: keep it as the reference, or port
   the same TCP transport there and retire the named pipes.

## Known risks

| Risk | Mitigation |
|---|---|
| Client patch breaks the signatures | non-fatal `NOT-FOUND`; documented re-derivation |
| Wine differs from Windows in hook behaviour | test on the real client first; keep the Windows tree as reference |
| Sending from the worker thread is not thread-safe in the game | keep manual sends infrequent; document the symptom |
| Double free of the Delphi string | instrument if it ever appears; see `docs/hooking.md` |
| Account ban for automation | owner's decision; no evasion features |
| Two game instances collide on the port | `NTPL_PORT` per instance; one host per DLL |
