# NPC Interaction Packets

## `npc_req` (SEND)

Initiate conversation with an NPC.

**Format**: `npc_req <entityType> <entityId>`

*TODO: Capture by talking to NPCs*

---

## `n_run` (SEND)

Select an option in an NPC dialog.

*TODO: Capture by selecting NPC dialog options*

---

## `shop` (RECV)

Shop contents received after talking to a shop NPC.

*TODO: Capture by opening a shop*

---

## `buy` (SEND)

Buy an item from a shop.

*TODO: Capture by buying items*

---

## NPC Interaction Flow (Expected)

1. Player clicks NPC → `ncif` SEND (select)
2. Player double-clicks/interacts → `npc_req` SEND
3. Server sends dialog → RECV (dialog packet TBD)
4. Player picks option → `n_run` SEND
5. If shop → `shop` RECV
6. If buy → `buy` SEND
