# Inventory Packets

## `inv` (RECV)

Full inventory contents for a bag type.

*TODO: Capture by opening inventory*

---

## `ivn` (RECV)

Single inventory slot update.

*TODO: Capture by picking up or moving items*

---

## `mvi` (SEND)

Move an item within inventory.

*TODO: Capture by rearranging inventory*

---

## `get` (SEND)

Pick up an item/drop from the ground.

**Format**: `get 1 <charId> <dropId>`

Live examples (2026-11-06):
```
get 1 14881134 13979116
```
The third field is the **dropId** from the `drop` packet, not the item vnum.
The server confirms with a four-field `get`:

```
get 1 14881134 13979116 0    (RECV)
```

The engine removes `drops[dropId]` on that confirmation. A client pickup for
another player's drop uses that player's char id; the server replies with the
same four-field confirmation.

---

## `drop` (RECV)

An item (or gold) appeared on the ground.

**Format**: `drop <itemVnum> <dropId> <x> <y> <amount> <unknown> <ownerCharId>`

Live examples (2026-11-06):
```
drop 5193 13979116 49 169 1 0 14881134
drop 1046 13961541 29 180 635 0 14881134
drop 2072 13965766 19 102 1 0 0            — unowned
```
`ownerCharId` 0 means unowned; otherwise only that character can loot it.
The bot picks up only your own or unowned drops.

---

## Inventory Types

*TODO: Document bag types (equipment, main, etc.)*
