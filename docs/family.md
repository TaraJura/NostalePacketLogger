# Family tab

The **Family** page holds family tools. The first feature is inviting a
member. It works while the bot is stopped and needs no route or combat state.

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

## GUI behaviour

- The tab has one compact row: a **Player** entry and an **Invite** button.
  Enter sends too.
- Names are sent as one token; empty names and names containing whitespace
  are refused locally.
- The tab sends the command through the same raw-packet path as the Packets
  tab. The server reply appears in the frame and in the Bot log.
- A `%Familyinvite` packet typed in game is picked up from the DLL's SEND
  stream, so the tab mirrors manual invites as well.
- The whole tab follows the **?** help standard; the popover explains the
  invite and family rules, and no explanatory paragraph sits in the layout.

## Live verification

2026-11-20, client PID 2740 on port 47823 (DLL 0.2.5, hooks active):

```
SEND %Familyinvite sjkrimer  ->  RECV infoi2 234 1 sjkrimer
SEND %Familyinvite redenkor  ->  RECV infoi2 234 1 redenkor
```

Both testing accounts received the confirmation path. The sender in PID 2408
also produced the same pair for `redenkor` at 15:14:33.

`gui_family_smoke.py` covers the tab end to end: send, waiting state, server
confirmation, Bot-log entry, invalid/empty-name blocking, and a manual
`SEND|%Familyinvite` picked up from the packet stream.

## Upstream reference

`~/.cache/ntpl/sources/noscore-packets/src/NosCore.Packets/ClientPackets/Commands/FamilyInvitePacket.cs`
defines `%Familyinvite` with one `CharacterName` field and the regional header
aliases; the CS region uses the same English header on this server.
