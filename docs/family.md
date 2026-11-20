# Family tab

The **Family** page holds family tools: invite one player by name, or
auto-invite map players who have no family. It works while the bot is stopped
and needs no route or combat state.

## Invite a member

```
SEND  %Familyinvite <characterName>
```

The client sends this raw game command exactly as typed in the in-game chat;
it is not wrapped in `say`. The player must be online and accept the
invitation, and the sending character must have permission to invite.

Live capture (2026-11-20 15:14:33, client PID 2408):

```
[SEND] %Familyinvite redenkor
[RECV] infoi2 234 1 redenkor
```

`infoi2 234` is the server's invitation confirmation. NosCore's Game18N table
names value 234 `YouInvitedToGroup`; the client reuses it for this invite
confirmation. The Family tab matches the argument against the name it just
invited (60 s window) before showing it as a family confirmation, so an
unrelated group-invite message is not mislabelled.

The same server refuses invites sent too soon:

```
[RECV] infoi 2359 0 4 3
```

`CannotSendInvite = 2359`; the client renders "You cannot send the invitation.
Please try again in %d seconds." The live server did not substitute a useful
number, so the tab uses its own 15 s countdown (successful test invites were
accepted 12.45 s apart). A 2359 clears the pending invite and starts the
countdown; the name is not added to the invited list, so it can be retried
after the timer.

## GUI behaviour

- The tab has one compact row: a **Player** entry, **Invite** and **Add to
  list**. Enter sends too.
- Below it, a full-height split view shows **Players on map** (name and
  family, with Invite on a known no-family player) on the left and **Invited
  players** (with Remove) on the right.
- Names are sent as one token; empty names and names containing whitespace
  are refused locally.
- The tab sends the command through the same raw-packet path as the Packets
  tab. The server reply appears under the row and in the Bot log.
- A `%Familyinvite` packet typed in game is picked up from the DLL's SEND
  stream, so the tab mirrors manual invites as well.
- The whole tab follows the **?** help standard; the popovers explain the
  invite, auto-invite and family rules, and no explanatory paragraph sits in
  the layout.

## Auto-invite players without a family

The **Auto-invite players with no family** toggle scans the map and invites
players whose family state is known and who have none.

- Family data comes from the player's `in 1` packet (`familyId` and
  `familyName` after the char id; `-1 -` means no family), from `gidx`
  updates (`gidx 1 <entityId> <familyId>.<rank> <familyName> ...`), and from
  the DLL session log for players who spawned before the GUI attached
  (`load_player_families`).
- A player whose family state was never sent is **skipped**, not guessed.
  The map list shows only actionable players (known no-family, invited,
  no-reply); the header shows the total on the map. Hidden `family unknown`
  rows are not rebuilt, which keeps a busy 150+ player town responsive.
  The visible list refreshes every second.
- **Only players with a fairy** is a second auto-invite filter. It uses the
  fairy vnum in the `in 1` equipment sub-packet (sixth dot-separated value)
  and the `pairy` element; both are backfilled from the log. No-fairy players
  are usually fresh alt accounts. The option is saved with the invited list
  and defaults off; the map list labels each candidate `fairy`, `no fairy` or
  `fairy unknown`.
- Candidates are invited nearest first, one at a time. The tab waits for the
  server's `infoi2 234` confirmation before the next one (2.5 s pacing).
- The live server also has an invite cooldown. Sending too early returns
  `infoi 2359` (`CannotSendInvite`, rendered as "You cannot send the
  invitation. Please try again in %d seconds."). Successful test invites
  were accepted 12.45 s apart, so the tab uses a conservative **15 s
  cooldown** and shows a visible **Next invite in Ns** countdown; auto-invite
  waits for it. A `2359` reply starts the countdown and leaves the name
  retryable.
- A name is added to the invited list only after the server confirms it with
  `infoi2 234` within 4 s. The list is displayed on the right and saved to
  `~/.config/ntpl/family-<winpid>.json` (`NTPL_FAMILY_STATE` overrides the
  path). A name is never invited again, even if the player declines or never
  answers.
- A name that gets no reply (offline, bad encoding, not found) is marked
  **no reply**, skipped for this session, and auto-invite continues to the
  next candidate. Five silent attempts in a row stop it with a hint to check
  the client's family permission.
- The list is editable: **Add to list** records a typed name without sending
  anything; **Remove** (on an invited/no-reply row) deletes it so auto-invite
  can try it again. A row on the map list can be invited with **Invite** or
  retried with **Retry** after a no-reply.

`gui_family_auto_smoke.py` covers the scan: one no-family player is invited,
a player in a family and a player with unknown family are skipped, the list
is persisted, and the invited name is not retried.

## Live verification

2026-11-20, client PID 2740 on port 47823 (DLL 0.2.5, hooks active):

```
SEND %Familyinvite sjkrimer  ->  RECV infoi2 234 1 sjkrimer
SEND %Familyinvite redenkor  ->  RECV infoi2 234 1 redenkor
```

Both testing accounts received the confirmation path. The sender in PID 2408
also produced the same pair for `redenkor` at 15:14:33.

`gui_family_smoke.py` covers the manual path end to end: send, waiting
state, server confirmation, Bot-log entry, invalid/empty-name blocking, and a
manual `SEND|%Familyinvite` picked up from the packet stream. The auto path is
covered by `gui_family_auto_smoke.py` as described above.

## Upstream reference

`~/.cache/ntpl/sources/noscore-packets/src/NosCore.Packets/ClientPackets/Commands/FamilyInvitePacket.cs`
defines `%Familyinvite` with one `CharacterName` field and the regional header
aliases; the CS region uses the same English header on this server.
