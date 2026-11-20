# NosBazar market tab

The **NosBazar** tab ports the two flows of
[`TaraJura/Nostale-NB`](https://github.com/TaraJura/Nostale-NB) into the packet
client:

1. **Monitor** — search each configured item, keep the cheapest listing and
   show its profitability against a NosMall ND cost (gold per ND) or an NPC
   gold cost (multiplier).
2. **Relist** — for an item in the inventory, undercut the cheapest other
   seller by one gold and send `c_reg`, never below the configured
   `min_price` floor.

The packet formats and live captures are in
[protocol/bazaar.md](protocol/bazaar.md).

## Quick start

1. Attach the GUI to a game client as usual.
2. Walk to a **Bibi Basar** NPC and open the NosBazar **by hand once**. The
   client learns the map-specific NPC entity id and the dialog option from
   your own `npc_req`/`n_run` packets (or from this client's DLL log if the
   GUI attached later). The id appears in **Bibi id** on the tab.
3. Add items in the right-hand **Item editor**:
   - **Name** — display label only; the server searches by vnum. Item names
     are rendered in full (wrapped, never ellipsized) in both the Items list
     and the results table. The **Copy** button next to Name puts the full
     name on the clipboard; right-clicking an item row or a result row does
     the same.
   - **Search vnums** — e.g. `1261 2174`; sent as
     `c_blist 0 0 0 0 0 0 0 0 2 1261 2174`. One vnum per item gives that
     item's cheapest listing. Several vnums form the same group search as
     Nostale-NB; the answer is grouped in the searched order, and the tab
     uses the minimum visible price across the intact entries. A truncated
     answer is marked `ok: truncated answer`.
   - **Item vnum / Tab / Amount / Min price** — needed only for relisting.
     `Tab` is Equip (0), Main (1) or Etc (2); `Min price` is the floor.
   - **Cost** — `NosMall ND` (fractional allowed) or `NPC gold`; this drives
     the profit column. Leave `No cost` for reference-only rows.
   - Thin rules separate the result rows (and one under the header), so the
     **Cost** (ND) and **NB Price** columns stay visually tied to their item.
   - The results table is always sorted by profit ratio, highest first. The
     **Only priced** filter (on by default) hides rows with no listing,
     timeout, or no answer yet; untick it to see those rows at the bottom.
   - **Advanced c_reg fields** — the reference's `unk1=9`, `unk2=4`,
     `durability=1`, `medal=2`; change only if a captured packet differs.
4. To find more items, click **Import NosMall…**: it lists only catalog
   groups with a **known ND cost** and excludes pets, fairies and partner
   supplies (currently 18 of 1,184 groups). A group without a cost cannot
   show a profit, so it is not offered at all. Search by name or vnum, then
   click a row or **Add all matching** to seed several groups. ND prices are
   not in the client data; the curated `known_reference_costs` fill the ones
   known, and an item already in the window contributes its vnums too — add
   an item with **New**, set its Cost, then reopen the picker to offer that
   group. **Remove unpriced & pets** cleans an existing item list: click it
   twice within five seconds and every row without an ND cost, plus every
   pet/fairy/partner row, is deleted (priced ordinary items are kept). The
   unfiltered full catalog can still be exported with
   `PYTHONPATH=linux/host python3 -m ntpl db nosmall --export ~/bazar-nosmall-all.json --all`.
5. Click **Monitor Start**. The tab opens the bazaar if needed, searches one
   item at a time (2.5 s pacing), retries timed-out items once, then refreshes
   after **Refresh (s)** (default 30).
6. To relist, tick **Allow live re-listing**, fill **Character** (your own
   name, so your listing is never undercut), then **Relist Start**.

## What the controller does

`ntpl.bazar.BazarController` is a tick-driven state machine; the GUI calls
`tick()` every 100 ms and feeds it the engine's `WorldState`.

- **Waiting** — no NPC id known: report “walk to Bibi Basar and open the
  bazaar once”, retry after the refresh interval. No search spam.
- **Opening** — replay `npc_req`, then `n_run`, then the initial
  `c_blist`/`c_slist`; success is an `rc_blist`. Timeout → waiting.
- **Pass** — one search in flight, 2.5 s between searches, 5 s per answer.
  `ok` / `empty` / `bad` are recorded; `timeout` items are retried once after
  the pass.
- **Cooldown** — show the table, wait the refresh interval, reuse the open
  bazaar if it answered recently.
- **No table reset between cycles** — the old quotes stay visible while the
  next pass runs, and each row is replaced when that item's answer arrives.
  A timeout/send/parse failure keeps the last price and marks it
  `timeout: stale price`; a definitive “no listings” answer clears the price.
- **All-timeout pass** — mark the bazaar closed, drop to waiting so the
  character cannot be flagged for search spam.
- **Relist** — only in relist mode, only with a character name, a positive
  `min_price`, an inventory slot for `(tab, vnum)`, and a cheapest **other**
  seller. Sends one `c_reg` per item per pass. No server acknowledgement for
  `c_reg` has been captured, so the row says **register sent (no ack)** rather
  than claiming the listing succeeded.

The bazaar/entity state lives in `WorldState.bazar`; inventory slots are
`WorldState.inventory` (`bag → slot → InventorySlot`), gold is
`WorldState.gold`. `WorldState.find_inventory(tab, vnum)` is what relist uses.

## Configuration

Each attached game client gets its own file:

```
~/.config/ntpl/bazar-<winpid>.json
```

`NTPL_BAZAR_CONFIG` overrides the path for every window (the test suite uses a
temporary file). Before a client is attached the tab starts empty; when a
client attaches and has no per-client file, `~/.config/ntpl/bazar.json` is
loaded as a seed and saved per-client on the next change.

Example:

```json
{
  "version": 1,
  "character": "root1",
  "refresh": 30.0,
  "npc_id": 10284,
  "open_option": 60,
  "open_page": 0,
  "items": [
    {
      "name": "bubbl",
      "search_packet": "c_blist 0 0 0 0 0 0 0 0 4 1261 2174 9480 10029",
      "vnum": 2174,
      "inv_tab": 2,
      "amount": 5,
      "min_price": 43000,
      "nos_cost": 0.5,
      "npc_cost": null,
      "unk1": 9,
      "unk2": 4,
      "durability": 1,
      "medal": 2,
      "enabled": true
    }
  ]
}
```

- `search_packet` is the full `c_blist`; the GUI builds it from **Search
  vnums** unless the raw packet field is filled.
- `name` is display-only; deduplicate names per file.
- `nos_cost` wins over `npc_cost`; both are per single piece.
- `min_price` is required for relisting; monitor-only items can leave it 0.

## Safety and limits

- **Relist is gated**: the checkbox, a character name, a positive `min_price`,
  a slot in the chosen inventory bag with at least the configured amount, and
  a cheapest listing whose vnum is inside the searched group. It never
  undercuts your own listing.
- **Monitor never sends `c_reg`.** The smoke asserts this.
- **Pacing**: do not lower the 2.5 s search interval; the game silently drops
  faster `c_blist` packets.
- **Truncated answers**: the DLL copies at most 2048 bytes per packet, so a
  long `rc_blist` is cut mid-entry. The parser keeps every intact entry, uses
  the minimum visible price and marks the answer `truncated`.
- **Name encoding**: the host decodes UTF-8, falling back to Windows-1252 and
  Latin-1 for the game's ANSI text, so seller names no longer show a `�`
  replacement glyph. The self-listing check also compares an ASCII-normalized
  name for accented characters.
- **Group searches**: one answer covers all vnums, grouped in search order.
  The tab uses the minimum visible entry; a truncated answer can hide the true
  minimum, so configure one vnum per item when the exact price matters.
- **Inventory after a late attach**: `inv` is sent at login. The tab replays
  the client's DLL log (`spawnlog.load_inventory`); if no dump is in the log,
  relist reports “not in inventory” until the character relogs or the bag is
  sent again. Monitor works without inventory.
- **Tax**: the port uses `int(price * amount * 0.0005)`. Nostale-NB computes
  the tax with its default amount 5 even when the listing amount differs; the
  server may recompute the field.
- **One character per window**: each GUI window/controller belongs to the
  attached client; use another window for another client.
- Gameforge prohibits automation; the account risk is the owner's.

## Tests

```bash
make -C linux host-test    # unit tests: parser, builders, config, controller
make -C linux bot-test     # includes gui_bazar_smoke.py
```

`test_bazar.py` covers the real 15-field `rc_blist` shape (including a
truncated tail and double spaces), the open sequence, pacing, retry/waiting
behaviour, response cross-talk and the relist floor/own/slot/vnum checks.
`gui_bazar_smoke.py` spawns a fake Bibi NPC, answers the open and one search,
and asserts the profit column and that no `c_reg` was sent.
`gui_bazar_relist_smoke.py` checks that the live-relist gate blocks the first
attempt and then asserts the exact `c_reg` packet and the `register-sent`
status.
