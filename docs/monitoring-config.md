# Monitoring config (NosBazar Items)

The **Items** tab is a catalog of the whole local Item.dat database (about
7,805 rows). The **monitoring set** is not a separate list inside the tab:
it is the bazar config file, and the two **Monitor / Relist** ticks in the
catalog are its switches. This document covers that file, the import/export
flows, the prepared monitoring profiles and how to verify them.

Related: [bazar.md](bazar.md) (the tab and the controller),
[protocol/bazaar.md](protocol/bazaar.md) (packet formats),
[database.md](database.md) (where the item names and NosMall catalog come
from).

## What the config contains

One JSON file per game client, normally
`~/.config/ntpl/bazar-<winpid>.json` (`NTPL_BAZAR_CONFIG` overrides the
location). The GUI saves it on every change; **Save config / Reload** in
**Configuration** write and re-read it.

```json
{
  "version": 1,
  "character": "root1",
  "refresh": 30.0,
  "npc_id": 9584,
  "open_option": 60,
  "open_page": 0,
  "items": [
    {
      "name": "Speech Bubble",
      "search_packet": "c_blist 0 0 0 0 0 0 0 0 4 1261 2174 9480 10029",
      "vnum": 2174,
      "inv_tab": 1,
      "amount": 5,
      "min_price": 0,
      "nos_cost": 0.5,
      "npc_cost": null,
      "unk1": 9,
      "unk2": 4,
      "durability": 1,
      "medal": 2,
      "enabled": true,
      "relist": false
    }
  ]
}
```

Item fields that matter for monitoring:

| Field | Meaning |
|---|---|
| `name` | The owner's shortcut label and the config key. The lists show the real Item.dat name for the vnum; the label appears in tooltips and the editor's **Name**. |
| `search_packet` | The `c_blist` sent by Monitor; `vnums` are its positive template vnums. A saved group can hold aliases; the controller searches only the vnums whose Item.dat name matches the row's own item. |
| `vnum` | The exact template vnum, used for relisting and for matching the inventory slot. |
| `enabled` | **Monitor** tick: the item is searched by Monitor Start. Off keeps the row and its settings but skips every pass. |
| `relist` | **Relist** tick: the item is in the relist set. Relist Start searches only items with `enabled && relist` and may reprice them. |
| `min_price` | The per-unit floor. Relist refuses to go below it; a relist row with no floor is skipped until one is set. |
| `nos_cost` | NosMall ND cost. A positive value puts the item in the **NosMall** Monitor view and computes gold per ND. |
| `npc_cost` | NPC gold price. Used for the **NPC resale** view (markup = listing ÷ NPC price) when no ND cost is set. |

`MAX_ITEMS` is 5,000. An older file without `relist` loads as monitor-only,
except that a positive `min_price` infers relisting (the pre-flag meaning).
An item with neither `nos_cost` nor `npc_cost` still monitors in the NosMall
view as price discovery.

## Importing in the GUI

**Items → Import…** has two config entries:

- **Import config (add)…** — reads a JSON config and merges its items into
  the current window. An item is skipped when its name already exists or a
  configured row already covers its vnum (name-aware `tracked_vnums`), so
  re-importing the same file adds nothing. Existing items and their relist
  floors are untouched.
- **Replace with config…** — asks for confirmation, then swaps the whole
  item list for the file's items. This is the way to switch to a pure
  monitoring profile. The Bibi id, character and refresh interval stay this
  client's (they are learned per map/session, not from the file).

Both write the current per-client `bazar-<pid>.json`; the source file is
never modified. Both use `load_config`, so a malformed file (bad JSON, a
duplicate name, more than `MAX_ITEMS`) is rejected before anything changes
and the error is logged in the Bot log.

After an import, use the catalog's **All items / Monitored / Relisting**
filter to see just the monitoring set. The filter and the search box combine.

## Prepared monitoring profiles

These files live in the user's home, not in the repository:

| File | Contents | Typical pass |
|---|---|---|
| `~/.config/ntpl/bazar-monitor-15.json` (copy `~/bazar-monitor-15.json`) | The 17 profitable NosMall items from the earlier curated 19-item Nostale-NB config, real Item.dat names, all **Monitor on / Relist off**. | ~45 s at 2.5 s/search |
| `~/bazar-nosmall-all.json` | The full 1,184-group NosMall catalog export, all monitor-only. Useful as a reference or a whole-catalog run. | ~49 min at 2.5 s/search |

The 17-item profile:

| Item | Vnum | ND |
|---|---|---|
| Fairy Experience Potion | 5370 | 2.5 |
| Soulstone Blessing | 1362 | 1 |
| Speech Bubble | 2174 | 0.5 |
| Wings of Friendship | 2160 | 0.5 |
| Chicken Specialist Card | 907 | 100 |
| Ancelloan's Blessing | 1286 | 5 |
| Partner Skill Ticket (Single) | 5931 | 50 |
| Medicine Bottle | 1765 | 2 |
| Fairy Booster | 1296 | 5 |
| Gourmet Pet Food | 2158 | 2 |
| Tarot Card Game | 1904 | 10 |
| Equipment Protection Scroll | 1218 | 20 |
| Blessing Amulet | 282 | 50 |
| Higher SP Protection Scroll | 1364 | 5 |
| Lower SP Protection Scroll | 1363 | 5 |
| Speaker | 2173 | 0.5 |
| Homing Perfume | 1156 | 0.8 |

The search packets keep the original alias groups (for example Soulstone
Blessing 1362/5195/9075), so the monitor behaves like the earlier configs.
The current per-client fallback `~/.config/ntpl/bazar.json` is a 25-item
NPC-resale list (all monitor-enabled); it is independent of the profiles
above.

### Applying a profile to a live client

1. Run host **0.7.8** or newer; the Items catalog, filter and config import
   arrived in 0.7.7/0.7.8. Only the GUI restarts — the game client and the
   DLL stay.
2. NosBazar → **Items → Import… → Replace with config…**.
3. Pick `~/bazar-monitor-15.json` and confirm **Replace**.
4. Set the catalog filter to **Monitored** to check the 17 rows, then
   **Monitor Start**.
5. Raise the floor or edit an item with the editor on the right; tick
   **Relist** only for items you want Relist Start to touch.

## Regenerating a profile

The 17-item file was generated from the old curated config, replacing the
shortcut labels with the real Item.dat names:

```python
import json
from pathlib import Path
from ntpl import database
from ntpl.bazar import BazarItem, BazarConfig, save_config

old = json.load(open("~/.config/ntpl/bazar-1116.json"))  # any old 19-item config
source = [it for it in old["items"] if (it.get("nos_cost") or 0) > 0]
conn = database.connect()
info = database.item_info(conn)
conn.close()
used, items = set(), []
for raw in source:
    vnum = int(raw["vnum"])
    base = (info[vnum].name if vnum in info else raw["name"])[:80]
    name, counter = base, 2
    while name in used:
        name = (f"{base[:72]} [{vnum}]" if counter == 2
                else f"{base[:64]} [{vnum}/{counter}]")[:80]
        counter += 1
    used.add(name)
    items.append(BazarItem(
        name=name, search_packet=raw.get("search_packet"), vnum=vnum,
        amount=int(raw.get("amount") or 5), min_price=0,
        nos_cost=float(raw["nos_cost"]), enabled=True, relist=False,
        unk1=int(raw.get("unk1") or 9), unk2=int(raw.get("unk2") or 4),
        durability=int(raw.get("durability") or 1),
        medal=int(raw.get("medal") or 2),
    ))
save_config(Path("~/.config/ntpl/bazar-monitor-15.json").expanduser(),
            BazarConfig(items=items, character="", refresh=30.0, npc_id=None))
```

For a different set, tick items in the catalog, **Save config**, and copy the
per-client JSON. The full catalog export is still:

```bash
PYTHONPATH=linux/host python3 -m ntpl db nosmall --export ~/bazar-nosmall-all.json --all
```

## The Items catalog

- The list is a virtualized `Gtk.ColumnView` over every Item.dat row (about
  7,805), plus configured rows whose vnums the database does not know and
  inventory-only vnums. Columns: **Monitor**, **Relist**, **Item**, **Vnum**,
  **Where**, **Cost**.
- **Where** annotates the live inventory (a bag/slot ×quantity, or “not in
  inventory”). Ticking a DB row that is not configured creates a monitor-only
  item from the DB; the Relist tick also sets floor 1 and, when the item is in
  a bag, its vnum/tab/amount. A bound (negative) vnum or bags 6/7 cannot be
  relisted; their Monitor tick still works.
- **Search** matches the real name, the configured label, the vnum and the
  Where text. The filter selects **All items**, **Monitored** or
  **Relisting**; the info line reads e.g. `7/7805 items · monitored only`.
- Rendering the whole catalog takes about 0.16 s; a tick schedules an idle
  rebuild of the virtualized store and does not overwrite unsaved editor
  values.

## Verification

`linux/test/gui_bazar_config_import_smoke.py` covers the import and the
filter:

- add-import merges a file and skips a duplicate name/vnum;
- an old file without `relist` loads monitor-only;
- replace-import swaps the list, and the saved `bazar-<pid>.json` matches;
- the catalog ticks follow the imported flags;
- the All/Monitored/Relisting filter returns the expected counts;
- a broken file changes nothing.

A one-off check against the full database (2026-11-26, host 0.7.8) imported
`bazar-monitor-15.json` into a 7,805-row catalog: 17 configured, 17 ticked,
17 Monitor rows, 0 relisting, catalog render 0.16 s, and a second add-import
added no duplicates.

Run the tests with:

```bash
make -C linux host-test
make -C linux bot-test      # includes gui_bazar_config_import_smoke.py
```

## Caveats

- The Monitor pass is paced at 2.5 s per search; the game drops faster
  `c_blist` packets. 17 items ≈ 45 s, the full 1,184-group catalog ≈ 49 min,
  plus the refresh wait.
- ND costs are not in the client data. The curated map plus the costs of
  already-configured items are the only price sources; an unpriced group
  cannot show a profit and is not offered by **Import NosMall…** (pets,
  fairies and partner supplies are excluded there too).
- Import config operates on **items only**. Bibi id, character and refresh
  stay the client's; the `name` field is a shortcut label, not the displayed
  item name.
- Relist Start requires `enabled && relist`; unticking Monitor therefore also
  removes an item from the relist pass. Relist also needs a positive floor
  and, for `c_reg`, an inventory slot.
- Gameforge prohibits automation; the account risk is the owner's.
