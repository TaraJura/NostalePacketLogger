# Minigame tab — Sheep Farm

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

## Requirements

- **DLL 0.3.0** for the `KEY|<name>` command. Updating
  `linux/build/PacketLogger.dll` is not enough: the old module stays mapped in
  the running client. Restart the game client and inject again (the picker's
  **Inject & connect**), then the window title/status shows `DLL 0.3.0`.
- Host **0.6.0 or newer** for the tab (0.7.0 today). Only a GUI restart is
  needed for host changes.
- The game window attached to this GUI window. The tab is per client, like the
  Family and NosBazar pages.

## Tests

- `linux/host/tests/test_minigame.py` — key names, settings round-trip and
  clamping, player-only nearest selection, NPC/pet/monster/self exclusion,
  Chebyshev radius, tie-breaks.
- `linux/test/gui_minigame_smoke.py` — a fake map with two players, an NPC and
  a monster: `KEY|y` is spammed, only the nearest player is targeted, focus
  switches when that player leaves, settings are saved per client, Stop sends
  nothing further, and an old DLL's `unknown command: KEY` stops the loop.
- `make -C linux key-test` — the native C++ check of the DLL's key parser.

Live status: the raid itself and player targeting through the client's own
`ncif 1 <charId>` are confirmed in the client's session log (map 2009), but the
`KEY|` press and the `TARGET|` selection of a player have **not** been
live-verified yet. The first live run should watch the Bot log for
`minigame keyboard ready`, `minigame focus -> …` and the raid's Wolves column.

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
