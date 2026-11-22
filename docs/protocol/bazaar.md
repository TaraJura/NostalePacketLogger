# NosBazar Packets

The NosBazar is NosTale's player auction house. It is opened by talking to a
**Bibi Basar** NPC (template vnum **793**) and choosing the NosBazar dialog
option. Listings settle in gold; searches are answered with the cheapest
matching listings.

Live captures below are from the 2026-11-18 client build (2026-11-19 session,
`NosTalePacketLog-1172.txt`).

## Opening the window

The client talks to the NPC, clicks the NosBazar option, then initialises the
listing and seller lists:

```
[SEND] npc_req 2 10284
[RECV] npc_req 2 10284 460          # dialog opened; 460 is the dialog id
[SEND] n_run 60 0 2 10284           # option 60 = NosBazar
[RECV] wopen 32 2 717               # a window opened (not bazaar-specific)
[SEND] c_blist  0 0 0 0 0 0 0 0 0   # initial listing, no filter (note the
                                    #   tolerated double space)
[SEND] c_slist 0 0 0
[RECV] rc_blist 0 <entry> <entry> … # the initial answer; proves it is open
[RECV] rc_slist 0  0
```

`n_run <option> <page> 2 <entityId>` — option **60** and page **0** are what
the live client sends for Bibi Basar. The entity id is per map/session, not a
constant: Nostale-NB hardcodes `10188`, while the live 2026-11-19 session used
`10284`. The packet client learns the id and option from the user's own
opening (`npc_req` + `n_run` + `c_blist`/`c_slist`) and replays them.

`wopen` is **not** proof of the bazaar: a warehouse opens with
`wopen 28 0 1322 0`. Wait for `rc_blist`.

A GUI that attaches after login can recover the id/option from the client's
DLL log (`spawnlog.load_bazaar_open`).

## Searching

```
c_blist <page> <category> <subcategory> <level> <rarity> <upgrade> <order> <unknown> <count> <vnum…>
```

The bot sends the eight filter/order fields as `0` and a list of template
vnums:

```
c_blist 0 0 0 0 0 0 0 0 1 1285
c_blist 0 0 0 0 0 0 0 0 4 1261 2174 9480 10029
```

- `<count>` must equal the number of vnums that follow.
- A search with `count 0` is the initial listing request, not a price search.
- The server answers a multi-vnum search **grouped by the searched vnum order,
  price-sorted inside each vnum**. The first entry is the cheapest listing of
  the first searched vnum that has listings — **not** a global cheapest.
  Nostale-NB uses that first entry and treats a group as equivalent variants
  (e.g. bubbl: `1261 2174 9480 10029`); this port instead takes the minimum
  visible price across the intact entries, which is more robust for a group.
  For exact per-item prices, configure one vnum per item.
- Live 2026-11-19 broad search: `c_blist 0 0 0 0 0 0 0 8 517 518 …` — the
  8th header field can be non-zero (a category/unknown filter) and the count
  was 517; the DLL log line is cut at 2048 bytes after ~39 vnums, so a pasted
  log line is not a complete packet.
- **Rate limit:** searches sent less than about two seconds apart are
  silently dropped. Pace them at 2.5 s.

## `rc_blist` (RECV)

```
rc_blist <page> <entry> <entry> …
```

No listings:

```
rc_blist 0
```

Each entry is pipe-separated (live 2026-11-19):

```
listingId|ownerId|ownerName|vnum|amount|isBundle|price|minutesLeft|unknown|unknown2|rarity|upgrade|unknown3|unknown4|itemData
8950|74|Storm|9|1|0|50000|750|0|0|5|0|0|0|1^9^5^0^0^6^48^52^44^2^70^…
13175|1696|Chaotic|1285|45|0|4495|1300|1|1|0|0|0|0|^0
```

| Index | Meaning |
|---|---|
| 0 | Listing/auction id |
| 1 | Owner char id |
| 2 | **Owner name** (seller) |
| 3 | **Item vnum** |
| 4 | **Amount / stack size** |
| 5 | Is package (0/1) |
| 6 | **Price per unit in gold** |
| 7 | Minutes left |
| 8, 9 | Unknown |
| 10 | Rarity |
| 11 | Upgrade |
| 12, 13 | Unknown |
| 14+ | Item-specific chunk, `^`-separated; `^0` means none |

Only indexes 2, 3, 4 and 6 are relied on. Names cannot contain spaces (the
space separates entries).

**2048-byte truncation:** the DLL copies at most 2048 bytes per packet into its
ring/socket line, so an `rc_blist` with many entries ends in a partial entry
(the log line is cut mid-field). The parser keeps every intact entry, drops the
malformed tail and marks the answer `truncated`; the true cheapest listing may
be in the cut part. The GUI shows `ok: truncated answer` and still uses the
minimum of the visible entries.

## `rc_slist` (RECV)

```
rc_slist <page> <entries…>
```

Captured: `rc_slist 0  0`. The bot ignores it; `rc_blist` is the open
confirmation.

## Registering / relisting (`c_reg`, SEND)

```
c_reg 0 <invTab> <slot> <unk1> <unk2> <durability> 0 <amount> <price> <tax> <medal>
```

| Field | Meaning |
|---|---|
| `0` | Packet type |
| `invTab` | `0` Equip, `1` Main, `2` Etc |
| `slot` | Slot index inside that bag (from `inv`/`ivn`) |
| `unk1`, `unk2` | Per-item constants. Main/Etc: `8 + INDEX[0]` and `4` (see below); equipment keeps its own values. Nostale-NB's `10 3` pair is silently rejected on FrosTale. |
| `durability` | Default `1` |
| `isPackage` | Always `0` here |
| `amount` | Pieces per listing (default 5) |
| `price` | **Per-unit** price; the reference undercuts by 1 |
| `tax` | Client-side tax estimate (see below) |
| `medal` | NosMerchant medal flag; default `2` |

Tax in Nostale-NB:

```python
int(price * amount * 0.0005)
```

The reference computes it with its default amount 5 even when the listing
amount differs; this port uses the configured amount and truncates integer
math. Servers may recompute/ignore the field. Re-listing never goes below the
item's `min_price` floor, and the port refuses to relist when no floor is set.

**`rc_reg <value>` is the acknowledgement** (RECV, ~0.3-0.6 s after an
accepted c_reg; value 1 in every live capture on FrosTale). A c_reg the
server ignores gets no packet at all — that is how a wrong item-constant
pair is detected. Live 2026-11-23: `c_reg 0 2 44 10 3 1 0 5 11496 28 2`
was ignored three times, while the same item with `10 4` was accepted.

The four item constants are derived from Item.dat for Main/Etc items:
`unk1 = 8 + INDEX[0]`, `unk2 = 4` (equipment carries its own values). The
client's own packets confirm it: `5370` INDEX[0] 1 → `9 4 1 0`; the
Etc stackables (INDEX[0] 2) → `10 4 4 0`. A stale `unk2` (e.g. the
Nostale-NB default 3) makes the server drop the packet silently.

## Inventory needed for relisting

`c_reg` addresses `(bag, slot)`, so the host needs the inventory dump:

```
[RECV] inv 1 0.5119.4 1.1324.1 2.1007.1          # Main bag
[RECV] inv 2 0.2115.5 1.2282.11                  # Etc bag
[RECV] inv 0 27.-4962.5.7.1.0.0 …                # Equip bag (7 fields)
[RECV] inv 3                                    # empty bag clears it
[RECV] ivn 2 1.2282.10.0                        # one slot changed
[RECV] gold 34661 0                             # own gold
```

- `inv <bag> <slot>.<vnum>.<quantity>[.<…>] …` sends the whole bag; `ivn`
  updates or removes one slot. In a standard bag (1/2) quantity 0 removes the
  slot; in an equipment bag (0/6/7) quantity 0 is a normal occupied row and
  vnum 0 removes it (see [inventory.md](inventory.md)).
- Bags: `0` Equip, `1` Main, `2` Etc, plus equipment-style `6`/`7`. The first
  three dot fields are the slot index, template vnum and quantity. Equip
  entries have additional fields and negative ids for some limited/bound
  items. The port **keeps** both: the NosBazar inventory scan shows the raw
  fields, searches `abs(vnum)` for a bound item, and relisting skips negative
  vnums because `find_inventory` matches positive templates only.
- The server sends the full inventory at login and after some item changes. A
  host that attaches later replays the log (`spawnlog.load_inventory`); if no
  dump is in the log, relist reports "not in inventory" until the character
  relogs or the bag is sent again. The same backfill feeds the inventory scan
  (**Rescan** re-reads it).
