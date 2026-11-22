# Autonomous play

How the host drives a live character through observe → decide → act → confirm
→ survive. This is the playbook behind the MCP tools; it is a policy, not a
new cheat: everything goes through the client's own movement, selector and
send path, and the DLL never touches the game loop.

The owner accepts the account risk. The rules in the root `CLAUDE.md` still
apply: never close or restart the owner's game, inject only into
`NostaleClientX.exe`, and never read credentials or session data.

## The loop

1. **Observe** — `game_state` (map, cell, HP/MP, resting), `game_entities`
   (positions, vnums), `game_spells`, the DLL log tail.
2. **Decide** — pick a target by level, aggression, distance and boss risk;
   choose approach, attack range and a retreat route *before* engaging.
3. **Act** — the client's own `MOVE` legs, `TARGET` + `u_s`, `rest`.
4. **Confirm** — a hit is real only when the server answers `su`; a death is
   HP reaching 0 in `su` or the target disappearing without an `out`.
5. **Survive** — thresholds, kiting, retreat, recovery, and a post-fight
   disengage from the spawn cluster. Survival outranks the kill.

## Pre-flight checklist

- **DLL link.** One host at a time. If the GUI holds the DLL and the MCP
  needs it, pause the GUI, close its socket, let the MCP connect, and resume
  the GUI afterwards; it reconnects by itself. See
  [Single-host handover](#single-host-handover).
- **Map and cell.** `game_state.map_id` must match the map the log says.
  When the log starts after login (no `at`/`c_map`), seed a private log copy
  with `[RECV] c_map 0 <map> 1`; append the seed **before** the copied lines
  so `load_entities` does not clear the entities it is about to seed.
- **Position sync.** After a warp the client actor can lag the server. Ask
  the server for the authoritative cell with `game_resync` (the
  `walk -1 -1 0 10` probe). The server answers `tp 1 <char> x y 0`; the
  client applies it, so `POS` and the server agree again. The `at`/`tp`
  packets are authoritative; `POS` is only a local read.
- **Card and equipment.** Equip the SP card with
  `u_i 1 <charId> 6 <slot> 0 0` (bag 6 is the specialist-card holder). The
  server confirms with `sc` (card info), `ivn 6 <slot>.<vnum>...`, `eq` and
  `sp`. A Ranger card's basic attack is skill **833** (bow, range ≥ 6).
- **HP/MP.** Require ≥ 95% HP before engaging; recover first if lower.
  Know the potion slots before you need them.
- **Target intel.** The local DB (`monsters` table) gives level, HP scales
  and the `PREATT`/`SETTING` fields. Entity ids are stable for a map
  instance on this server: the same ids appear in the logs of other clients
  that visited the map, which is how spawn vnums can be recovered after the
  log was truncated (`in 3 <vnum> <id> <x> <y>`).

## Entity seeding

A host that attaches mid-map sees only the `mv` packets that happen after it
connects; a stationary monster is invisible. `spawnlog.load_entities`
replays the log tail in order (`in` spawns, `mv`/`tp` moves, `out` despawns,
clear on `c_map ... 1`/`mapout`) and `GameService._seed_from_log` puts those
entities into the world. `MCP` therefore starts with every entity the log
still knows, with its vnum where the log had a spawn. Live packets keep
updating positions afterwards.

```python
from ntpl.spawnlog import load_entities
rows = load_entities("C:/NosTalePacketLog-1996.txt")
# {5030: (3, 200, 17, 140), ...}  -> (kind, vnum, x, y)
```

## Target policy

| Risk | Rule |
|---|---|
| Boss | Never engage a `HP/MP` boss (e.g. Giant Warepard vnum 594, 470,000 HP) unless the plan explicitly says so. |
| Level | Prefer targets at or below the character's level. The Mt. Krem entrance area has Wolf 200 (57), Brutal Wolf 201 (59), Mountain Turtle 206 (58), Pig 204 (58) and Small Wasp 223 (57); their `PREATT` first field marks aggressive ones (wasps). |
| Distance | Start with the nearest known-vnum target, but do not cross a different spawn cluster to reach it. |
| Geometry | Approach to ~7 cells, attack from 4–9, step back when the target closes to ≤ 3. A ranged card can kite a melee wolf indefinitely. |
| Unknown vnum | Do not engage an unknown-vnum monster as the *planned* kill; treat it as an environmental risk and keep away. |

## Combat

- Attack with slot 0 (`game_attack`), which runs `TARGET|<id>` in the client
  (target ring + the client's own `ncif`) and then `u_s 0 3 <id>`. The server
  validates range and line; a refusal is a `cancel` and no `su`.
- Pace by `max(cast, cooldown)` from the Skill.dat record, with a small
  margin (~0.8–1.2 s for the Ranger bow). Do not spam the same skill faster
  than its cooldown, and keep one cast in flight.
- A hit is confirmed only by `su <type> <self> 3 <target> <skill> ...`. The
  last two numbers in the live `su` are the target's **current/max HP**:
  `... 2502 4140` after the first hit, `... 0 4140` on the kill.
- A killed monster gets **no `out`**. `out 3 <id>` means it left view, not
  that it died: break off or re-acquire instead of claiming a kill.
- `TARGET ... ERR` / `entity not found` after landed hits is the normal
  death signal. A refusal before the first hit means the client cannot
  select it (moved, out of sight, wrong id).

## Survival

| Threshold | Action |
|---|---|
| HP < 55% | Stop attacking. Step away 3 cells, then retreat ≥ 12 cells from the target; recover to ≥ 90% before re-engaging. Allow at most one such recovery per kill. |
| HP < 30% | Abort the kill. Retreat and recover; report the failure. |
| HP < 18–25% | Critical: break off even mid-attack, run, sit only when no hit has landed for a full tick (2 s). |
| Sitting and HP drops | Something is hitting the character. Stand, move again, sit further away; do not sit in the spawn cluster. |
| Post-fight | Leave the spawn cluster (≥ 20 cells, or to a known empty area), wait one full tick without an incoming `su`, then sit to ≥ 90% and stand. |

The HP guard must watch the **last incoming `su`** (`su 3 <monster> 1 <char>`),
not just the stat packets: a character left standing next to two wolves loses
~55 HP/s while the GUI is idle. In the 2026-11-22 session that exact mistake
dropped the character from 7,441 to 3,242 HP after the planned kill; the
rescue had to kill both wolves before it could heal.

## Disengaging

- Pick the retreat cell from the map grid (`is_walkable`), not from the
  target's direction alone; cave walls make "straight away" invalid.
- Walk in ≤ 3-cell client `MOVE` legs. The client pathfinds and animates, and
  its own `walk` packets keep the server position valid.
- If the client cannot path (bad start cell/desync), call `game_resync` and
  retry. Do not inject a long raw walk: the server answers with `tp` and the
  character jumps.
- After a rescue kill, keep moving until the log shows no incoming `su` for
  two seconds, then recover.

## Failure modes

### POS desync after a warp

The client can hold a stale or non-walkable cell (2026-11-22: `POS 72,109`
on a wall while the server had `12,94`). The authority is the server.
`game_resync` sends the impossible probe and applies the returned `tp`; the
old manual method was `game_send("walk -1 -1 0 10")` plus reading the log.

### Log truncation

The DLL rewrites its per-client log in place at 16 MB. The login `inv`/`ski`
dumps and early spawns are gone. Consequences and fixes:

- inventory: relog with the GUI open, or use the per-character cache
  (`~/.config/ntpl/inventory-<char>.json`);
- map seed: prepend a synthetic `[RECV] c_map 0 <map> 1` to a copy of the
  tail; do not append it after the lines whose entities you want to keep;
- spawn vnums: recover them from another client's log on the same map
  instance (the entity ids are stable).

### Stale `portals.json`

Learned crossings accumulate and go stale. The live map's `gp` list is
authoritative; e.g. on 2026-11-22 map 1 had only five portals
`(79,2)→2, (80,66)→147, (117,177)→145, (159,6)→2550, (48,132)→4995`, while
the file still had old map-1 → 20 entries. Do not walk to a persisted portal
without a fresh `gp`.

### Single-host handover

The DLL accepts a new host only while it has none; it does not replace a
connected GUI. The reversible handover used for live sessions:

```bash
kill -STOP <gui-pid>                                   # GUI paused
sudo -n ss -K dst 127.0.0.1 dport = 47823              # drop its socket
# start the MCP, do the work, stop it
kill -CONT <gui-pid>                                   # GUI reconnects (1 s)
```

`ss -K` is SOCK_DESTROY; the GUI's `PacketClient` reconnects on the next
loop. The game is never closed.

### Something else is driving

A long burst of 3-cell `[SEND] walk` packets at ~150 ms followed by a `tp`
is not normal walking: it is a follow/mirror replay or a speed correction.
Check the GUI's Follow/Mirror settings before blaming the MCP. In the
2026-11-22 session the character moved from (15,96) to (176,35) and was hurt
between two MCP sessions with the bot log idle; the server `tp` at
19:24:09 exposed the burst.

## Worked example (2026-11-22, client winPID 1996, char 10170)

1. **Card.** `u_i 1 10170 6 0 0 0` → `sc`, `ivn 6 0.903.0.5.0`,
   `eq 10170 ...`, `sp` — Ranger Specialist Card (vnum 903) equipped.
2. **Travel.** NosVille Warp Tower NPC vnum 955, entity 3095, dialog 439:
   `npc_req 2 3095` → `npc_req 2 3095 439` → `n_run 16 1 2 3095` (runner 16 =
   WarpTeleport) → `at 10170 20 12 94` → map 20 *Mountain Cave 1*.
3. **Resync.** `SEND|walk -1 -1 0 10` → `tp 1 10170 15 96 0`.
4. **Kill.** Wolf entity 5036 (vnum 200, lvl 57), approached to 7 cells,
   bow skill **833**: `4366 → 2688 → 879 → 0` (three `su` hits). The target
   left the entity list with no `out`.
5. **Mistake and rescue.** The character stopped among the spawn cluster;
   wolves 5071 and 5069 kept hitting for ~120 each. HP fell to 3,242. The
   rescue killed 5071 (`4366 → 2688 → 879 → 0`) and 5069
   (`4366 → 2664 → 878 → 0`), then retreated and healed to 7,384/8,132 (91%).
6. **Result.** All three monsters died, the character survived, the GUI
   reconnected, and nobody closed the game.

## MCP tool map

| Tool | Use |
|---|---|
| `game_state` | map, cell, HP/MP, resting, basic skill |
| `game_entities` | IDs, kinds, vnums, positions, distance (seeded from the log) |
| `game_map`, `game_path` | grid, walkability, A* |
| `game_move`, `game_travel` | client MoveTo; ≤ 3-cell legs, confirm by POS |
| `game_resync` | impossible-walk probe → server `tp`, fixes a desynced client |
| `game_attack`, `game_cast`, `game_cast_spell` | TARGET + `u_s`, waits for `su` |
| `game_rest` | sit/stand before movement or recovery |
| `game_pickup` | `get 1 <char> <drop>` after a kill |
| `game_wait_for` | wait for a packet (e.g. `c_map` after a portal) |
| `game_send` | one raw packet when a tool does not exist yet |
| `game_spells`, `game_memory_*` | skill catalog, read-only memory probes |

## Operational rules

- Confirm every action on the server (`su`, `tp`, `rest`, `get`); never
  assume a sent packet worked.
- The character must be standing to move.
- Keep the GUI and MCP off each other's DLL connection; hand over as above.
- Leave the character somewhere safe (no monster within ~15 cells, HP ≥ 90%,
  standing) and the GUI reconnected when the session ends.
- Account/ban risk is the owner's; no detection-evasion features.
