# Inventory Packets

Live captures are in [bazaar.md](bazaar.md#inventory-needed-for-relisting);
the short version:

## `inv` (RECV)

Full inventory contents for a bag type, one token per occupied slot:

```
inv 1 0.5119.4 1.1324.1 2.1007.1
inv 2 0.2115.5 1.2282.11
inv 0 27.-4962.5.7.1.0.0 30.885.0.0.0.0.0
inv 6 0.905.0.5.0 1.906.0.5.0
inv 7 0.4256.0.0.0 1.4248.0.0.0
inv 3
```

`bag` is `0` Equip, `1` Main, `2` Etc. The live server also sends
equipment-style bags `6` and `7` (SP cards/costumes in the 2026-11-20
captures) and empty bags `9`/`10`; the tab names only 0/1/2 and shows the
rest as **Bag N**. A token is `slot.vnum.quantity[.<…>]`:

- Bags 1/2 use three fields in `inv` (the `ivn` update appends one unknown
  field, see below). Quantity 0 is empty.
- Bag 0 uses seven fields; bags 6/7 use five. The extra fields are not
  decoded. Quantity 0 is **normal** for an occupied equipment row (it is not
  a stack). The server removes such a row with vnum 0, e.g.
  `ivn 0 30.0.0.0.0.0.0`.
- A negative vnum is a limited/bound item (`63.-4962...`); the absolute
  value is the Item.dat template. The engine keeps these for display and the
  NosBazar inventory scan searches `abs(vnum)`, but relisting ignores them.

A bag line with no tokens clears that bag. The engine stores each slot as an
`InventorySlot(bag, index, vnum, quantity, fields)`; `fields` keeps the extra
dot fields verbatim. `parse_inventory_token` is the shared parser used by the
live handlers and the log backfill.

The full burst is sent as part of character login (after `p_clear`, followed
by `inv 0/1/2/3/6/7`), not on map changes, and **no client packet requests a
refresh**. A host that misses it must see a relog/channel change, or use the
host-side snapshot cache (`spawnlog.load_inventory_cache`); `Rescan` only
re-reads the DLL log.

---

## `ivn` (RECV)

Single inventory slot update or removal:

```
ivn 2 1.2282.10.0            # slot 1, vnum 2282, quantity 10
ivn 2 1.2282.0.0             # standard bag: quantity 0 removes the slot
ivn 0 30.885.0.0.0.0.0       # equipment: quantity 0 is an occupied row
ivn 0 30.0.0.0.0.0.0         # equipment removal: vnum 0
```

The fourth dot field on a standard-bag update is unknown and ignored. Because
equipment updates also have four or more fields, the parser classifies
equipment by bag (`0`, `6`, `7`) or by five-plus fields, not by field count
alone; a four-field standard update therefore still removes on quantity 0.

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
