# Minigame tab — Sheep Farm and Miniland production

The seventh GUI page automates the **Sheep Farm** mini-raid: it spams a key
press at the game window and keeps another **player** selected, because the
raid's NPC sheep are worth only 5 points while a disguised player is worth 20.
It works with or without the bot.

The raid starts automatically every three hours at **:15** on channels 1 and 2
(1:15, 4:15, … 22:15 CET/CEST), lasts five minutes and is a free-for-all. The
client's own `srlst 2 <charId> <name> <points> <sheep> <wolves>` rows are the
scoreboard. A death costs 20 points and a 20 s respawn.

Turn the bot off while using the tab: both change the client's target, so a
running farmer would fight the focus selection.

## What the tab does

| Control | Meaning |
|---|---|
| **Start** | Start/stop the key loop and the focus loop. It does not start the bot. |
| **Key** | One ASCII letter/digit, or `space`, `enter`, `tab`, `esc`, `backspace`, `shift`, `ctrl`, `alt`, arrows, `home`, `end`, `insert`, `delete`, `pageup`, `pagedown`, `F1`–`F12`. Default `y`. |
| **every (ms)** | Delay between key presses, 40–5000 ms. Default 100. |
| **Focus nearby players** | Keep another player selected (the nearest at selection time, then while it stays in range). On by default. |
| **within (tiles)** | Chebyshev search radius for that character, 1–50. Default 15. |

The settings are stored per attached client at
`~/.config/ntpl/minigame-<winpid>.json` (`NTPL_MINIGAME_CONFIG` overrides) and
are saved on every change and on shutdown. The loop never auto-starts.

### Player-only focus

Only `Entity.kind == 1` is considered, and the local character is excluded:

| Kind | What it is | Focused? |
|---|---|---|
| 1 | player character | yes |
| 2 | pet, partner or NPC | no |
| 3 | monster or map NPC (including the Happy Wooly / Yellow Sheep) | no |

The nearest player wins by Chebyshev distance, then name, then char id. The
current player is kept while it stays inside the radius; when it leaves, dies
or disappears the tab picks the nearest remaining player. A player whose
client-side selection is refused is skipped for 5 s and the next one is tried.

Each selection is one `TARGET|<charId>` DLL command (the client's own selector
sets the local target ring and sends its `ncif 1 <charId>`); with an old DLL
that has no `TARGET`, the host sends an injected `SEND|ncif 1 <charId>`
instead. The first selection of a target is sent **before** the first key
press of the same tick, and the DLL handles commands in order, so the spammed
attack lands on the player rather than on a sheep.

The host watches the client's own `ncif` in the packet stream. If a key press
or anything else moves the client's target away from the focused player (for
example to a sheep), the next 0.5 s focus tick selects the player again
*before* the following key press. While the client still has the focused
player selected, the selection is not re-sent; a 5 s safety refresh re-asserts
it if the client never reported the `ncif`.

### Key delivery

The host sends `KEY|<key>` on the normal TCP command channel. The DLL:

1. finds the game's own top-level window (`TNosTaleMainF`) in its process,
   caching it;
2. queues the press to a small key thread, so packet forwarding and `TARGET`
   are never blocked;
3. when the game window is foreground, sends a real `SendInput` key-down,
   waits 25 ms, key-up, and also posts the key-up to the game queue;
   otherwise posts `WM_KEYDOWN` / `WM_KEYUP` to the game window.

Real `SendInput` updates the thread keyboard state (`GetKeyboardState` /
`GetKeyState`) as a physical key does; a posted message does not. Keeping the
game window focused is therefore the reliable mode. The key is sent even when
no player is in range, because some actions do not need a target.

A successful queue is reported once as `STATUS|KEY OK <key>`; a failure is
reported as `STATUS|KEY ERR <reason>` on the first failed press and then at
most every 250 ms, so a retried Start gets a fresh reason without a 10–25 Hz
flood. An old DLL answers `unknown command: KEY`; the tab stops the loop and
asks for a game restart rather than spamming the command.

**The `y` key**: in the Spanish/Turkish control lists `Y` is the
secondary-weapon attack; the German/English lists bind the same action to `Z`.
That is the QWERTY/QWERTZ difference. If `y` does nothing, try `z`; the entry
accepts both.

## Miniland production (Great Quarry)

The **Miniland production** frame plays a Miniland production game and claims
the reward box you choose. **Great Quarry** (object vnum 3125) is implemented
first; Saw Mill (3126), Shooting Range (3127) and Fish Pond (3128) use the same
packet flow and will be added after a live test.

In the game, the object's window shows **My production points** (account-wide,
capped at 2000 and refilled daily) and the reward ranges:

| Score | Reward |
|---|---|
| 0–999 | no item |
| 1,000–4,999 | 1x box |
| 5,000–7,999 | 2x box |
| 8,000–11,999 | 3x box |
| 12,000–15,999 | 4x box |
| 16,000–1,000,000 | 5x box |

The minigame itself is played with the arrow keys: **←/→** attacks a bug on
that side, **↑** digs (a normal dig is ~50–60 points, a glowing stone ~300),
and a bug reaching the character costs a life. The tab does **not** play that
visual game; it sends the result the client sends at the end of a game.

| Control | Meaning |
|---|---|
| **Game** | Great Quarry (3125). |
| **Play until** | Reward box to claim, 1x–5x. Default **5x**, whose minimum score is **16,000**. The result is the box's minimum from the server's own `mlo_info` tier table; the built-in table is the fallback. |
| **production points** | The last `mlo_info`/`mlo_mg`/`mlpt` balance. The label says how to learn it when it is unknown. |
| **Play** | Sends the sequence below, then clicks the client's **Stop** (Storno) and waits **every** seconds and starts the next one — it claims until the production points fall below 100, a raid team blocks it or a claim gets no answer. Stop ends the loop. |
| **Stop** | Cancels the running sequence and the Play loop. |
| **every** | Seconds between claims, 30–600. Default **40**: the server refuses a reward for about 40 s after one is collected, so a shorter gap gets every other claim refused. |

The sequence is the client's own, live-captured on 2026-11-26. The second
field is the **object's slot** from `mlo_info` (the live object was on slot 4;
the forum's `1` was silently ignored by the server):

```text
mg 1 <slot> 3125                         start (live: mg 1 4 3125)
  -> mlo_st 0                            the server accepted the start
mg 3 <slot> 3125 <points> <points>       result (live: 16124 16124)
  -> mlo_lv 4                            reward level, 0-based (4 = 5x)
mg 4 <slot> 3125 <level>                 claim with the server's level
  -> mlpt 1900 100                       production points 2000 -> 1900
  -> mlo_rw 2193 2                       reward item 2193 x2
mg 2 -1 -1                               Stop (Storno), back to Start Game
```

The tab waits for `mlo_lv` and claims exactly that level (the GUI's 5x choice
is the server's level 4); if the server stays silent it falls back to the
chosen level minus one. After a confirmed claim it sends `mg 2 -1 -1` —
the client's own **Stop** (Storno) button, which returns the window to its
Start Game screen — so the next run starts clean. Do not use the window
close form `mg 2 <slot> <vnum>` there: that is the client's X, the dialog
disappears and the game window ends up out of sync (live 2026-11-26).

The server refuses a second reward soon after one was collected. The
first automated claim was live-verified on 2026-11-26 (`mlo_lv 4`,
`mlpt 1900→1800`, `mlo_rw 2213 ×1`), but an immediate second attempt
16 s later got no `mlo_lv`. The cooldown was measured live on 2026-11-26
(20:38–20:43): every attempt 35–38 s after a reward was refused, while the
retry ~80 s after the reward claimed, so the server cooldown is above 38 s
and at most ~78 s; the user's live test puts the boundary at **40 s**.
Play waits **40 s** between claims by default (`every`, 30–600 s), retries
an ignored run up to 3 times (see the live **next claim in N s** countdown
under the status) and ends with a points/raid/no-answer message. The 35–38 s
attempts were refused; 40 s is the shortest gap observed to work and was
live-confirmed repeatedly (2026-11-26). Raise `every` to 45–50 if an attempt
is still refused. Twenty claims take about 13 minutes at 40 s. The status reads
`5x claimed · production points 2,000 → 1,900 · reward item 2193 ×2`, or a
warning when no answer arrived (the window may be closed or the result
rejected). A claim spends production points — **100** normally, less with the
game's stacking discounts; when the last known balance is below 100 the tab
refuses to start. The live `mlo_info` line observed on 2026-11-26 was:

```text
mlo_info 0 3125 4 2000 0 0 0 999 1000 4999 5000 7999 8000 11999 12000 15999 16000 1000000
```

Fields: owner flag, object vnum, slot, production points, two flags, then
`(minimum, maximum)` reward ranges.

**Open the Great Quarry window in the game first and keep it open.** That is
what sends `mlo_info`, so the host knows the object, your production points and
the server's exact tiers. Without it the tab still uses the fallback tiers,
but it cannot show the balance and the claim is likely to be refused.

A GUI that attaches — or restarts — while the window is already open does not
need to reopen it: the attach-time seed reads the last `mlo_info`/`mlo_mg`/
`mlpt` from the attached client's own DLL log, exactly like the bazaar NPC and
inventory seeds, and logs `minigame: Great Quarry from the client log`.

**Raid team:** the game refuses the production minigame while the character is
in a raid team — the client shows "You cannot use it when you are already a
member of a raid team" and the server ignores the `mg` packets. The engine
tracks the server's own raid state: `raid 1 1` + `raid 2 <leaderId>` on
joining, `raid 1 0` + `raid 2 -1` on leaving (live 2026-11-26 23:00–23:02).
That state is authoritative — the tab unblocks the moment the leave packet
arrives. Every `rdlst` (raid/party list) is also recorded into
`WorldState.raid_members` (including the local character) as the fallback
for a host that attached after the last state packet; the fallback only
blocks while the local id is in a list younger than 60 s. The block is shown
in the points label (`· in a raid team (15) — leave it to play`) and on
Play. Leave the raid team in the game, then press Play.

This is a packet shortcut, not a visual play-through: the server may reject
the fabricated result, and it is a game-rule violation with a **high ban
risk**. The account risk is the owner's.

## Requirements

- **DLL 0.3.0** for the `KEY|<name>` command. Updating
  `linux/build/PacketLogger.dll` is not enough: the old module stays mapped in
  the running client. Restart the game client and inject again (the picker's
  **Inject & connect**), then the window title/status shows `DLL 0.3.0`
  or newer (`0.3.2` is the current build).
- Host **0.6.0 or newer** for the tab (0.9.12 today). Only a GUI restart is
  needed for host changes.
- The game window attached to this GUI window. The tab is per client, like the
  Family and NosBazar pages.

## Tests

- `linux/host/tests/test_minigame.py` — key names, settings round-trip and
  clamping, player-only nearest selection, NPC/pet/monster/self exclusion,
  Chebyshev radius, tie-breaks; the production helpers (default 5x target,
  the server's tier table winning over the fallback, the three `mg` packet
  shapes) and the engine's `mlo_info`/`mlo_mg`/`mlpt` parsing.
- `linux/test/gui_minigame_smoke.py` — a fake map with two players, an NPC and
  a monster: `KEY|y` is spammed, only the nearest player is targeted, focus
  switches when that player leaves, settings are saved per client, Stop sends
  nothing further, and an old DLL's `unknown command: KEY` stops the loop.
- `linux/test/gui_quarry_smoke.py` — the fake server sends the live Great
  Quarry `mlo_info` (2,000 points, slot 4, the six ranges): Play sends
  `mg 1 4 3125`, `mg 3 4 3125 16000 16000` and `mg 4 4 3125 4` in order (the
  server's `mlo_lv 4` is used for the claim), the `mlpt`/`mlo_rw` replies
  change the label to 1,900 and show the reward, and a balance below 100
  refuses to start. A raid list (`rdlst`) plus `raid 1 1`/`raid 2 1000`
  blocks Play, and the live leave shape `raid 1 0`/`raid 2 -1` (no new
  `rdlst`) unblocks it immediately and starts the two-claim loop.
- `make -C linux key-test` — the native C++ check of the DLL's key parser.

Live status: the raid itself and player targeting through the client's own
`ncif 1 <charId>` are confirmed in the client's session log (map 2009), but the
`KEY|` press and the `TARGET|` selection of a player have **not** been
live-verified yet. The first live run should watch the Bot log for
`minigame keyboard ready`, `minigame focus -> …` and the raid's Wolves column.

The Great Quarry natural flow was live-verified on 2026-11-26: GAMESTART
(`mg 1 4 3125`), a played game ending with score 16,124 (`mg 3 4 3125 16124
16124`), the server's `mlo_lv 4`, the client's claim `mg 4 4 3125 4`, and the
`mlpt 1900 100` + `mlo_rw 2193 2` reward. The tab's **automated** result was
live-verified once (20:08:14: `mlpt 1800`, `mlo_rw 2213 ×1`). The whole
**Play loop** was live-verified on 2026-11-26 and confirmed working at the
40 s default: claim, `mg 2 -1 -1` (Stop), the countdown, the next run, and
the stop when the production points fall below 100. Attempts 35–38 s after
a reward were refused, which is what set the 40 s default.

## Troubleshooting

- **Start does nothing / stops immediately** — the window title must show DLL
  0.3.0. An old DLL is detected and reported; restart the game and inject.
- **Keys are sent but nothing happens** — focus the game window (click it once)
  so the DLL uses real input; on a QWERTZ layout try `z`; check that the key is
  bound in-game.
- **Focus refused for player N** — the client would not select that player
  (dead, out of the client's range, or not targetable yet). The tab retries
  another player after 5 s; the key loop keeps running.
- **No player found** — the radius is too small, or those entities are pets,
  NPCs or monsters (kind 2/3), which the tab deliberately ignores.

Automation violates the game rules. The account risk belongs to the owner.
