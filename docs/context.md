# Project context — 2026-11-26

Quick map for the next session. The detailed history is in
[handoff-2026-11-26.md](handoff-2026-11-26.md) and
[handoff-2026-11-19.md](handoff-2026-11-19.md); packet formats in
[protocol/](protocol/README.md); the market feature in [bazar.md](bazar.md);
the monitoring set/import in [monitoring-config.md](monitoring-config.md);
the data layer in [database.md](database.md).

## Current state

- **Client build**: 2026-11-25 Gameforge maintenance updated NosTale to
  **0.9.3.3262 (build 6124)**; 0.3.0's hardcoded player global/MoveTo
  addresses no longer matched and `POS` returned "player object not
  readable". **DLL 0.3.2** (0.3.1 re-derived them) has the player global
  `0x007964BC`, MoveTo `0x0056620C` and speed table `0x0078B3C8` and needs a
  **game-client restart/re-inject**. The host also logs a repeated POS read
  error only once instead of every poll. The speed extension for 3262 is
  derived but not live-verified above 59.
- **NosBazar** (2026-11-26): rows report only the listings of the item name
  they display (mixed Nostale-NB groups such as the single/all partner skill
  tickets no longer leak a sibling's price); the Monitor has a search box
  filtering both tabs by name/config label/vnum, and every row has a Copy
  button (Item text is selectable; Relist/Items/Inventory keep right-click
  copy). The Items list uses the same name-aware coverage. The Relist
  section has no Character field: own listings are matched by character id
  (`rc_blist` owner id, `self_id`, or the seller-list owner id) and the name
  is learned from the self `in 1` spawn (whole DLL log) or `rdlst`; the
  relist toolbar is one row (Add from Items / Add… / Actions… / Only
  relisting), with select/unselect, remove and the bulk switches inside
  **Actions…**.
  **Items** is a searchable catalog of every Item.dat row (about 7,805)
  plus configured rows the DB lacks and inventory-only vnums. A configured
  item is listed once at its own vnum; its other search vnums still match
  the search but are not separate rows (they would switch the same ticks).
  Each row has
  a Monitor/Relist tick (a DB row is created on the first tick; Relist
  fills vnum/tab/stack and floor 1), an **All items / Monitored /
  Relisting** filter narrows the catalog, the selection opens the full
  editor,
  **Add to Monitor/Relist** applies to a Ctrl+click selection, Import…
  covers NosMall, the full Item.dat picker and saved config files (add or
  replace), and ⋯ holds rescan/price-all/bulk switches. A curated 17-item
  profitable-NosMall monitoring config is
  `~/.config/ntpl/bazar-monitor-15.json`. The Monitor left panel lists only
  monitored items (one compact toolbar: Add from Items / Add… / Actions…),
  and Relist shows only the relist set by default.
  The Character frame shows the detected name above HP/MP. The active user
  config is a 25-item relist list for char `root1` (see the handoff).
- **Minigame — Great Quarry (2026-11-26, host 0.9.11)**: the Minigame tab's
  **Miniland production** frame plays a production minigame (Great Quarry
  3125 first). **Play until** picks the reward box 1x–5x, default 5x whose
  minimum score is 16,000; the production-points label comes from
  `mlo_info` (engine `WorldState.minigame`). **Play** sends the client's own
  sequence `mg 1 <slot> 3125` → `mg 3 <slot> 3125 <points> <points>` →
  `mg 4 <slot> 3125 <mlo_lv level>` (the server's level is 0-based: 4 =
  5x), confirmed by `mlpt`/`mlo_rw`; under 100
  points it refuses. The Great Quarry window must be open in the game (it
  sends `mlo_info`). The game refuses the minigame inside a raid team, so
  the server's own `raid 1 0/1` state (`WorldState.raid_active`) blocks Play
  with a clear hint and the packet clears it the moment the team is left
  (the `rdlst` membership with its 60 s freshness is only the fallback for a
  host attached mid-raid). **Play** loops: it claims, sends `mg 2 -1 -1` (the client's
  Stop/Storno; `mg 2 <slot> <vnum>` is the window close and desyncs the
  dialog), waits **40 s** (`every`, the live reward cooldown — 35–38 s was
  refused) and starts the next run until the points fall below 100; an
  ignored run is retried 3×, a live next-claim countdown is shown, and a
  raid team or missing answer stops it. The wait is driven by the 1 s
  Minigame tick (a one-shot GLib timer stalled live). Packet shortcut, high
  ban risk; the natural flow, the automated claim and the 40 s Play loop
  are live-verified.
- **Linux host**: 386 unittest cases, 6 GTK4 smokes, 47 fake-server bot
  smokes, and the native `speed-test` and `key-test` pass. Build: `make -C
  linux all test-host`, tests above. The host is **0.9.11**; the GTK4 window
  is split into `gui.py` (core) plus one mixin per page (`gui_bazar.py`,
  `gui_map.py`, `gui_bot.py`, `gui_family.py`, `gui_minigame.py`,
  `gui_stats.py`, `guicommon.py`), and a handler exception is logged and
  skipped instead of stopping the packet pump. It opens the
  bazaar with the client's own shortcut (`c_skill` → `wopen 32`, FrosTale)
  when no Bibi or medal is available, and charges the bazaar's **50-gold
  minimum listing fee** in `c_reg` (the server silently drops a registration
  whose fee is below it — live 2026-11-23, every failure carried 4/23/28
  gold and every accepted packet at least 50). 0.7.1 added the MCP seeding
  of `world.entities` from the attached client's log
  (`spawnlog.load_entities`) and `game_resync` (`walk -1 -1` →
  server `tp`) for a client that desynced during a warp.
- **Autonomous play**: the observe → decide → act → confirm → survive policy
  is in [autonomous-play.md](autonomous-play.md). Live 2026-11-22 (client
  winPID 1996, char 10170): Ranger SP card 903 equipped, Warp Tower → map 20
  Mountain Cave 1, one Wolf (vnum 200) killed with skill 833 and two
  attacking wolves killed in self-defence; the character ended at 91% HP,
  the GUI reconnected and the game was never closed. Key lessons: resync
  with the impossible-walk probe, seed entities from the log, check the
  target's HP in `su` (a killed monster sends no `out`), and leave the spawn
  cluster before sitting — a character left standing next to two wolves lost
  ~55 HP/s while the GUI was idle.
- **Minigame tab** (seventh GUI page, appended after Malicious): Sheep Farm
  helper. **Start** spams `KEY|<key>` (default `y`, the secondary attack; `z`
  on QWERTZ) every 40–5000 ms through DLL 0.3.0, and **Focus nearby players**
  selects the nearest other `kind == 1` character within a radius through the
  client's own selector (`TARGET|id`, fallback `SEND|ncif 1 <id>`), never an
  NPC, pet, monster or the local character. The DLL uses real `SendInput` when
  the game window is foreground and posted keys otherwise. Per-client
  `~/.config/ntpl/minigame-<winpid>.json`. Needs **DLL 0.3.0 and a game
  restart/re-inject**; the host is 0.7.2. Covered by `test_minigame.py`,
  `gui_minigame_smoke.py` and `key-test`; the key press and player TARGET are
  **not yet live-verified**. See [minigame.md](minigame.md).
- **Stats tab** (eighth GUI page, appended last): a player **DPS meter**.
  Every `su` hit from a player (attacker type 1, positive damage) adds the
  decoded damage (`args[12]`, target HP `args[15]/[16]`); the table shows
  name, DPS, damage, hits, share, last hit and session total, with a
  5/10/30/60 s or Session window and Reset. Partner/monster hits and
  zero-damage buffs are ignored. Refreshes twice a second while visible;
  collection continues in the background. Covered by `test_dpsmeter.py`
  and `gui_stats_smoke.py`. See [stats.md](stats.md).
- **Speed +** (**Malicious** tab, default 0): the DLL reads the server's
  `cond` speed, calls the client's own entity speed setter on the local
  player actor, and clamps the outgoing `walk` speed back to the server's
  cond. The value applies immediately (no +1 probe since 0.2.5); a **lone**
  server `tp` correction is ignored (a respawn, mount/dismount or resync —
  live 2026-11-26), and only a burst of 3 corrections within 10 s — the real
  correction loop — settles at +1 (or off if +1 itself is rejected), with
  the GUI showing the limit. The spin has no small
  cap (up to 2,000,000,000): on the first value above the client's own 59
  limit the DLL raises that check and supplies a 256-entry speed table, and
  values above the byte's 255 are emulated by scaling its top entry. Client
  hack, high ban risk; reverts when the GUI disconnects. Needs DLL 0.2.9+
  (a game-client restart/injection), not just a GUI restart — the raised cap
  is DLL-side too.
- **Cast speed** (**Malicious** tab, plain on/off switch, default off):
  host-side, works with or without the bot; cooldowns stay visible
  (`CAST|0` on attach undoes DLL 0.2.8's zeroing). Manual: pressing a
  skill once starts an assist that re-sends that exact `u_s` on every
  `sr <slot>` cooldown-end packet (press another skill to switch,
  120 s idle / 5 min cap). Bot: no waiting for the previous `su`; the
  next eligible spell/attack goes out immediately and overlaps the
  current cast (live PID 676: 385 overlapping casts accepted vs 21
  refused), re-casting a skill on `sr`. The server's cast time and
  per-skill cooldown remain the floor. Saved as `cast_boost`.
  `gui_cast_assist_smoke.py` and `gui_cast_ready_smoke.py` cover it.
  No game restart.
- **NosBazar tab** (fourth GUI page): monitor + floor-protected relist ported
  from `TaraJura/Nostale-NB`. It learns the Bibi Basar entity id/option from
  the client's own `npc_req`/`n_run` or DLL log (refusing an id this map's
  entity list knows as another NPC — ids are per map; a stale one opened
  Carve Runes); with no Bibi visible it uses the portable NosBazar medal
  (vnum 1955, `u_i` from the Main/Etc bag) and waits for `wopen 32`.
  A client whose own shortcut opens the bazaar (FrosTale: a bare `c_skill`
  answered by `wopen 32`; learned live or from the DLL log) is replayed
  first, so no NPC and no medal are needed. When the bazaar is already open
  before the attach there is no new `wopen`; the verified Bibi dialog (460;
  Carve Runes 11009 is never clicked) plus the listing answer continue the
  scan. It
  searches `c_blist` at 2.5 s
  pacing, parses the real 15-field `rc_blist` (minimum visible price,
  truncation-aware), keeps old rows across refresh cycles, sorts by profit
  and has an **Only priced** filter. Relist is gated by the relist set
  (**Relist** tick in Items; **Add from Items** / **Add…** for whole
  inventory or the Item database; **Actions…** for select/unselect, remove
  and the relist/stop bulk actions), shown alone by default
  (**Only relisting**), with Ctrl+click multi-selection; a positive floor is
  required (Relist Start is the only switch).
  Each pass asks the seller tab (`c_slist` → **`rc_slist`**) for our own
  listings: one with **nothing sold** is repriced with the in-game Change
  action (`c_mod`, no inventory slot needed, confirmed by the refreshed
  seller list — **price changed (c_mod)**), and only when every own listing
  has some amount sold (Change unavailable) does it add a new listing with
  `c_reg`, which reports **register accepted (rc_reg)** or **register
  ignored (no rc_reg)** (a c_reg needs the bazaar window — `wopen 32` — and
  the client's exact twelve-field shape with the 50-gold minimum listing
  fee (both live 2026-11-23); `unk1` is repaired from Item.dat before a
  pass; `unk2` is item-specific). **Relist Start**
  searches only the relist set while Monitor searches every enabled item.
  The page has a second-level navbar (**Items**,
  **Relist**, **Monitor**, **Configuration**); **Monitor** has a
  third-level navbar, **NosMall** (ND cost; gold per ND) and **NPC resale**
  (NPC gold cost, e.g. Pet Food or Bell of Sweet Home; Markup = listing ÷ NPC
  price). One Start searches both; each view has its own Only priced filter.
  The editor Cost type chooses the view; NPC gold auto-fills from Item.dat
  when left at 0. **Items** is one searchable list of every available item:
  configured rows plus every bag the client reports (including equipment
  bags 0/6/7, quantity-0 rows and bound negative vnums), each row with a
  **Monitor** and a **Relist** tick (an inventory row is created on the
  first tick; Relist sets floor 1), Item.dat names, NPC prices and known ND
  costs; the selection opens the full editor; **Add to Monitor/Relist**
  applies to the selection; **Price all** adds one search per distinct
  owned item. The **Monitor** table and the **Relist**/**Items**
  lists show the real item name resolved from each entry's vnum (local DB
  first, `Item.dat` fallback), with the configured shortcut only as the
  fallback when no vnum resolves; hover the item name for the shortcut and
  search vnums.
- **Game/packet database**: `~/.config/ntpl/nostale.db` (`NTPL_DB`) built by
  `PYTHONPATH=linux/host python3 -m ntpl db build`. 7,805 items, 2,723
  monsters, 1,964 skills, 779 maps, 3,639 NosMall records / 1,184 groups,
  1,004 packet definitions from 14 public projects plus the local wiki.
  Query with `ntpl db stats|sources|packets|items|nosmall`.
- **NosMall importer**: NosBazar tab → **Import NosMall…** (DB groups or the
  client archives), search/add one or **Add all matching**. Only groups with
  a known ND cost are listed and pets/fairies/partner supplies are excluded
  (18 currently), because an unpriced item cannot show a profit. Full export
  (all 1,184 groups, unfiltered): `ntpl db nosmall --export
  ~/bazar-nosmall-all.json --all`.

## User files

```
~/.config/ntpl/bazar.json              fallback, 25-item NPC-resale list
~/.config/ntpl/bazar-<winpid>.json     per attached client
~/.config/ntpl/bazar-monitor-15.json   17 profitable-NosMall monitoring set
~/bazar-monitor-15.json                visible copy of the 17-item profile
~/bazar.json                           visible copy of the 25-item list
~/bazar-nosmall-all.json               1,184-group catalog export (monitor-only)
~/.config/ntpl/nostale.db              19 MB game/packet SQLite DB
~/.cache/ntpl/sources/                  cloned public packet projects
~/.cache/ntpl/mirror/                   cross-client Follow mirror bus (JSON lines)
~/.config/ntpl/bot-<winpid>.log        per-client bot/action log
~/.config/ntpl/minigame-<winpid>.json  per-client Sheep Farm key/focus settings
```

## Next likely work

0. **Bushi King mode for the Minigame tab** (Jiří's request 2026-11-23):
   spam `a` at the nearest Water/Fire/Centurio/Boxer Bushi, never the Bushi
   King. Design proposal, **not approved yet**, with the raid's observed
   packets: `docs/superpowers/specs/2026-11-23-bushi-king-minigame-design.md`.
1. Live-verify the Minigame tab at a Sheep Farm raid (automatic at :15 on
   channels 1/2): restart the game with DLL 0.3.0, inject, start with `y`, then
   watch the Bot log for `minigame keyboard ready` / `minigame focus -> …` and
   the Wolves score; try `z` if `y` does nothing (QWERTZ).
2. Live-verify the bazaar open/search replay while standing at Bibi Basar
   (the controller already waits/retries; last live attempt was out of range).
3. Live-test **Mirror movement & spells** with two of our own clients: both
   GUIs publish to `~/.cache/ntpl/mirror`; the follower should replay the
   leader's walks and spells with its own char id. Fake-server verified.
4. Try the 17-item profitable NosMall monitoring config
   (`~/.config/ntpl/bazar-monitor-15.json`) on a live client: Items →
   Import… → Replace with config…, then Monitor Start; the pass takes
   about 45 s at the default 2.5 s pacing.
5. Live-test `c_reg` on a cheap item; no server acknowledgement is captured,
   so relist is currently fake-server verified only.
6. Grow the NosMall ND price map: the importer now lists only priced,
   non-pet groups (18 today). Client COST blocks do not contain the web-shop
   price; add an item with **New**, set its Cost, then reopen the picker, or
   extend `known_reference_costs`.
7. If desired: a CLI `ntpl bazar` monitor and a read-only MCP bazar tool
   using the same controller; a price-map importer.
8. Remember the UI preferences: never clear the table at a cycle start;
   profit-sorted with Only priced; cp1252/latin-1 name decoding.

## Commands

```bash
cd ~/games/gameforge/NostalePacketLogger
make -C linux all test-host
make -C linux host-test speed-test key-test gui-test bot-test
PYTHONPATH=linux/host python3 -m ntpl db build
PYTHONPATH=linux/host python3 -m ntpl db stats
PYTHONPATH=linux/host python3 -m ntpl db packets --opcode rc_blist
PYTHONPATH=linux/host python3 -m ntpl db nosmall --search "Soulstone"
linux/scripts/gui.sh
```

Gameforge prohibits automation; the account risk is the owner's. Do not read
or store credentials/session data (`usercache-*`, `webcache-*`).
