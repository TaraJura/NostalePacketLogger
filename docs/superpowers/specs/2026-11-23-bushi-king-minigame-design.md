# Minigame tab: Bushi King mode (design proposal)

**Status: proposal, NOT approved yet** (2026-11-23). Jiří asked for the
feature, Claude sent this design, and the session closed before he approved
it ("zapiš to do .md souborů, budeme pokračovat v hlavní session"). Next step:
Jiří approves or corrects it, then plan and implement. Nothing is built yet.

## Request

Jiří, 2026-11-23: "přidej do miniher Bushi King minihru, kdy mi to bude
spamovat A key na nejbližší bushi enemies". In English: add a Bushi King mode
to the Minigame tab that spams the `a` key at the nearest enemy Bushi.

Why (Claude's assumption, not confirmed): to collect as many action points as
possible in the **Bushi King Defence raid** without clicking. The client has
the task "Reach %d action points in the Bushi King Defence Raid minigame %d
times." (conststring 4926).

## What the raid looks like (observed)

Source: Jiří's own play as root1 (char 7001) on 2026-11-23, 12:15:56–12:19:40,
map instance 9307. The log is `~/games/gameforge/prefix/drive_c/NosTalePacketLog-1512.txt`,
lines ≈188 498–201 500. Raid texts come from `NScliData_UK.NOS`
conststring 12300, 12307 and 12308 ("You can now register for the Bushi King
Defence raid.").

- **Two Bushi King (vnum 839)** stand at the map ends: id 2490803 at (6, 36),
  HP in `trboss`, and id 2490802 at (66, 36), HP in `tbboss`. Both had
  193 262/193 262 HP for the whole raid.
- **The enemies (kind 3):** Water Bushi 2200 spawns at (62, 37), Fire Bushi
  2201 at (10, 35), plus Centurio Bushi 2202 and Boxer Bushi 2203. A wave
  comes every ~3 s. The window had 63 `in` for 2200, 61 for 2201, 11 for 2202
  and 6 for 2203.
- Other players kill a fresh Bushi in ≈0.4–0.6 s (several
  `su 1 <player> 3 <id> -1 0 -1 201 …` lines, then `die 3 <id>`), so
  a target lives only briefly.
- **A hit is `toria 3 <id>` (SEND).** No `u_s` is sent during the raid.
  Selection is the usual `ncif 3 <id>`. root1 sent 116 `toria`: 66 at 2200,
  24 at 2202, 10 at 2203 and **28 at a Bushi King**. The King's HP stayed
  full, so those hits were wasted. He never hit 2201, which spawns at the far
  end. His hit cadence was about 120–170 ms.
- The client seems to pick a target on its own when the hit key is pressed.
  Some `ncif` came only ~34 ms before the `toria`. At 12:17:18.152 the
  `toria` went to 2491400 right after `ncif 2491399`. That is probably how it
  ended up on the King. The log cannot show which key does what; Jiří says the
  hit key is `a`.
- root1 stood around (57, 32), about 5 tiles from the Water Bushi spawn.

## Proposed design (awaiting approval)

- **A new frame "Bushi King"** in the Minigame tab, below "Sheep Farm".
  Controls: **Start** (toggle), **Key** (default `a`), **every (ms)** (default
  100, range 40–5000) and **within (tiles)** (default 10, range 1–50).
- **Target rule:** the nearest entity with `kind == 3` and `vnum` in
  {2200, 2201, 2202, 2203}, by Chebyshev distance, then entity id. Never the
  Bushi King (839), and never a player, pet or NPC. Keep the current target
  while it is alive and in range. When it dies or leaves, take the next
  nearest at once.
- **Selection:** `_client_target` (`TARGET|<id>`, the client's own selector).
  The fallback for an old DLL is `SEND|ncif 3 <id>` (monster type 3, not 1
  as in Sheep Farm). Bushis die in ~0.5 s and a key press may re-target on its
  own. So check the focus on **every key tick**, not only every 0.5 s as Sheep
  Farm does. When `world.target_id` is not our target, send the selection
  before that tick's `KEY|a`.
- **One minigame at a time:** starting Bushi King stops Sheep Farm and the
  other way round, using the same GLib tick.
- **No walking:** the mode only selects and presses the key.
- **No `toria` injection:** Jiří asked for the A key, and a real key press
  keeps the packets identical to his own play.
- **Settings** go in the existing per-client `~/.config/ntpl/minigame-<winpid>.json`
  under a new `"bushi"` object. Files with only the current flat Sheep Farm
  keys must still load.
- **Status line:** the current target (name, id, distance) and the number of
  presses sent.
- **Requirements** are the same as Sheep Farm: DLL 0.3.0 for `KEY|`. A host
  change needs the bot window closed and reopened.

## Approaches considered

1. **A second frame that reuses the Sheep Farm key and focus loop.** This is
   the proposal: the least new code and the same key delivery and target
   machinery, with each game's settings side by side.
2. **A mode dropdown in the one Sheep Farm frame.** Fewer widgets, but the key
   default and radius differ per game, and one shared config makes switching
   error-prone.
3. **Injecting `toria 3 <id>` directly.** Faster and needs no focused window,
   but it sends packets the client did not produce (the server may check
   range and cadence), and Jiří asked for the key. Rejected.

## Open points for Jiří

- Approve or correct the design.
- Should Fire Bushis at the far end count too? Proposal: whatever is nearest
  within the radius counts, and the radius keeps the bot on its own side.
- `a` is the hit key according to Jiří. Packets cannot confirm it.

## Implementation pointers

- `linux/host/ntpl/minigame.py`: `BUSHI_ENEMY_VNUMS = {2200, 2201, 2202,
  2203}`, `BUSHI_KING_VNUM = 839`, a pure `nearest_bushi(entities, position,
  radius, exclude)`, and the config fields with backward-compatible loading.
- `linux/host/ntpl/gui.py`: `_build_minigame_page` (new frame),
  `_minigame_tick` / `_minigame_focus` (mode-aware),
  `_minigame_start` / `_minigame_stop`, `_minigame_read_config` /
  `_minigame_apply_config`, and `_client_target`.
- Tests: `linux/host/tests/test_minigame.py` and
  `linux/test/gui_minigame_smoke.py` (fake map with Bushis, a King and a
  player: the King is never targeted, a dead target is replaced on the next
  tick, the selection comes before `KEY|a`).
- Docs: `docs/minigame.md`, the Minigame row in `CLAUDE.md` and
  `docs/context.md`. The raid packets are in `docs/protocol/unknown.md`.
