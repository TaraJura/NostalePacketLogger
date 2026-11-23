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

`wopen <type>` names the window that opened: **type 32 is the NosBazar**
(`wopen 32 2 642`), a warehouse is 28, Carve Runes is 83. `rc_blist` alone
is **not** proof: the server answers `c_blist` searches over the packet
socket even when no bazaar window is open, but it **ignores `c_reg`** without
it (live 2026-11-23: searches were answered while Carve Runes was open and
every registration went unanswered; the same bubble registered fine once
the bazaar window was open). The host therefore requires a fresh `wopen 32`
after the open burst and checks it again before each `c_reg` — except when
the bazaar was **already open before the host attached**: no new `wopen`
arrives then, so a verified Bibi dialog (`[RECV] npc_req 2 <id> 460`) plus
the `rc_blist` answer to the initial `c_blist` is accepted and the scan
continues (live 2026-11-23, the reported case).

The dialog id identifies the NPC before any option is clicked: Bibi opens
**460**, Carve Runes opened **11009**. The host refuses to click an option
when the reply dialog does not match the known Bibi dialog.

A GUI that attaches after login can recover the id/option from the client's
DLL log (`spawnlog.load_bazaar_open`). Entity ids are per map instance: the
learned id is only replayed while the current map's entity list knows it as
Bibi Basar (vnum 793), never when the same id is a different NPC.

Without a visible Bibi, the **portable NosBazar medal** (Item.dat
`Gestore del NosBazar`, vnum **1955**) opens the same window from the
inventory:

```
[SEND] u_i 1 <charId> <bag> <slot> 0 0   # use the medal (bag 1 Main / 2 Etc)
[RECV] wopen 32 2 <page>                 # the bazaar window, no NPC dialog
```

No `npc_req`/`n_run` is involved on this path; a fresh `wopen 32` is the
whole confirmation.

A third opener exists on **FrosTale**: the client's own NosBazar shortcut.
A bare `c_skill` send is answered with `wopen 32` about 0.1–0.2 s later,
11 times out of 11 openings in the 2026-11-23 live log (four maps), with no
`npc_req`/`n_run` and no `u_i`:

```
[SEND] c_skill
[RECV] wopen 32 2 626
[SEND] c_blist  0 0 0 0 0 0 0 0 0    # the client's own initial listing
[SEND] c_slist 0 0 0
```

The host learns the pairing (a `c_skill` send at most 2 s before a
`wopen 32`, from the live stream or this client's DLL log) and then replays
the exact packet when it has to open the bazaar itself. No NPC entity and
no medal are needed.

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
c_reg 0 <invTab> <slot> <unk1> <unk2> <durability> 0 <amount> <price> <tax> <medal> 0
```

| Field | Meaning |
|---|---|
| `0` | Packet type |
| `invTab` | `0` Equip, `1` Main, `2` Etc |
| `slot` | Slot index inside that bag (from `inv`/`ivn`) |
| `unk1`, `unk2` | Per-item constants. Main/Etc: `unk1 = 8 + INDEX[0]`; `unk2` is item-specific (3, 4 and 5 all seen on accepted packets). |
| `durability` | Default `1` |
| `isPackage` | Always `0` here |
| `amount` | Pieces per listing (default 5) |
| `price` | **Per-unit** price; the reference undercuts by 1 |
| `tax` | Client-side tax estimate (see below) |
| `medal` | NosMerchant medal flag; default `2` |
| trailing `0` | Every client-sent packet carries it (24/24 in the 2026-11-22/23 logs). The server ignored the host's packets without it for bag-2 items — 105 attempts at 33 s spacing in one session and 901 in another — while the client's own packet for the same item, slot and constants was accepted (Speech Bubble, 2026-11-23). Send the client's exact shape. |

The emulator packet definitions (NosCore, OpenNos) list only the eleven
fields up to `medal`; the live client appends one more `0`.

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
server ignores gets no packet at all. Live 2026-11-23: every registration
for a Speech Bubble was ignored — both the configured pair and a `10 4`
variant — because the bazaar window was not open (Carve Runes was, type
83); the server also answers searches in that state, so `rc_blist` is not
proof. With `wopen 32` open, the same item family registers normally.

The **first** constant follows Item.dat for Main/Etc items: `unk1 = 8 +
INDEX[0]` (5370 INDEX[0] 1 → 9; the Etc stackables INDEX[0] 2 → 10). The
remaining constants are item-specific, not derivable: the client's own
packets show `2072` (Bell of Sweet Home) → `10 3 1 0`, `2077` (Pet Food) →
`10 5 1 0`, `1363` → `9 2 1 0`, `5370` → `9 4 1 0`. Do not force `unk2`;
copy it from a client relist of the same item if a registration is ignored
with the window open.

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
