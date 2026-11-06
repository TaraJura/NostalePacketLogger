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

Not started, and not the next thing to build. It needs a trustworthy state
model and a way to measure whether an action worked.

When it starts:

- Start with a single, reversible loop (e.g. walk to a coordinate and verify
  arrival), not combat.
- Keep a human-visible log of every injected packet and the state that
  motivated it.
- Rate-limit and stop on unexpected state.
- Treat Gameforge's terms and the account risk as a first-class constraint.

## Near-term engineering tasks

1. **Install NosTale in a dedicated Wine prefix** and run the injector against
   the real client. This is the gate for everything else.
2. **Verify the signatures** on the current `NostaleClientX.exe`. If they are
   stale, re-derive them (`docs/hooking.md`).
3. **Capture one session** with `ntpl monitor --log` and check the file log,
   the GUI and the engine against it.
4. **Fix whatever the real client exposes**: calling convention surprises,
   packet format differences, Wine quirks.
5. **Decide the fate of the Windows tree**: keep it as the reference, or port
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
