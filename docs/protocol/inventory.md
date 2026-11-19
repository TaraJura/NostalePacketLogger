# Inventory Packets

Live captures are in [bazaar.md](bazaar.md#inventory-needed-for-relisting);
the short version:

## `inv` (RECV)

Full inventory contents for a bag type, one token per occupied slot:

```
inv 1 0.5119.4 1.1324.1 2.1007.1
inv 2 0.2115.5 1.2282.11
inv 0 27.-4962.5.7.1.0.0 30.885.0.0.0.0.0
inv 3
```

`bag` is `0` Equip, `1` Main, `2` Etc. A token is
`slot.vnum.quantity[.<…>]`; Equip entries carry extra fields. A bag line with no
tokens clears that bag. The engine stores `slot index → (vnum, quantity)` for
the NosBazar relist.

---

## `ivn` (RECV)

Single inventory slot update or removal:

```
ivn 2 1.2282.10.0     # slot 1, vnum 2282, quantity 10
ivn 2 1.2282.0.0      # quantity 0 removes the slot
```

---

## `gold` (RECV)

Own gold:

```
gold 34661 0
```

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

The 2026-11-18 server sends free drops with **-1** instead of 0, for example
`drop 1303 513032 39 26 1 1 -1`; a `get 1 <charId> <dropId>` on it was
accepted. `ownerCharId` 0 or -1 means unowned; otherwise only that character
can loot it. The bot picks up only your own or unowned drops.

---

## Inventory Types

*TODO: Document bag types (equipment, main, etc.)*
