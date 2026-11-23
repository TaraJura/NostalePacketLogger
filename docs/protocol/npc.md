# NPC Interaction Packets

Live captures from the 2026-11-18 client build (2026-11-19 session):

```
[SEND] npc_req 2 10284          # request dialog with entity 10284
[RECV] npc_req 2 10284 460      # dialog opened; 460 is the dialog id
[SEND] n_run 60 0 2 10284       # choose option 60 (the NosBazar entry)
```

Other live dialogs used `n_run 16 1 2 3095` and `n_run 5002 0 2 9583`, so the
option id is NPC-specific and the second field is a page/modifier. The third
field is the entity type (2 = NPC) and the fourth the entity id.

A server `wopen` follows some dialog choices and its **type** names the
window: NosBazar is `wopen 32 2 642`, a warehouse `wopen 28 0 1322 0`,
Carve Runes `wopen 83 0 0 0`. `rc_blist` alone does not prove the bazaar
window is open — searches are answered without it while `c_reg` is ignored
— so the host requires the `wopen 32` and checks the Bibi vnum
([bazaar.md](bazaar.md)).

## `npc_req` (SEND/RECV)

**SEND format**: `npc_req <entityType> <entityId>` (`2` for NPCs).

**RECV format**: `npc_req <entityType> <entityId> <dialogId>`.

The client sends it when the player clicks an NPC; the host can replay it to
open a dialog without clicking. Entity ids are per map/session; Bibi Basar
(NosBazar) is template vnum `793`, and the port learns its id from the open.

## `n_run` (SEND)

`n_run <option> <page> <entityType> <entityId>` — the player's dialog choice.
For Bibi Basar the NosBazar option is `n_run 60 0 2 <entityId>`.

## `shop` (RECV)

`shop <entityType> <entityId> <shopId> <menuType> <shopType> <name>` — a map
shop descriptor pushed with the NPC spawn, **not** the "shop opened" event:

```
shop 2 9584 1 0 45 NosBazaar
```

A merchant's open event is `shopping 0 0 2 <entityId>`, answered by `n_inv`
(shop stock). The bazaar is different: it opens through `n_run 60` and answers
with `rc_blist`.

## `buy` (SEND)

*TODO: Capture by buying items.*

## NPC Interaction Flow

1. Player clicks NPC → `ncif` SEND (select)
2. Player double-clicks/interacts → `npc_req` SEND
3. Server sends `npc_req … <dialogId>` and the dialog data
4. Player picks option → `n_run` SEND
5. If shop → `shop` RECV; if a window → `wopen` RECV
6. If buy → `buy` SEND
