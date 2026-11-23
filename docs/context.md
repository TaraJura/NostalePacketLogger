# Project context — 2026-11-22

Quick map for the next session. The detailed history is in
[handoff-2026-11-19.md](handoff-2026-11-19.md); packet formats in
[protocol/](protocol/README.md); the market feature in [bazar.md](bazar.md);
the data layer in [database.md](database.md).

## Current state

- **Linux host**: 352 unittest cases, 6 GTK4 smokes, 43 fake-server bot
  smokes, and the native `speed-test` and `key-test` pass. Build: `make -C
  linux all test-host`, tests above. The host is **0.7.1**: the MCP now
  seeds `world.entities` from the attached client's log
  (`spawnlog.load_entities`) and has `game_resync` (`walk -1 -1` →
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
  restart/re-inject**; the host is 0.7.1. Covered by `test_minigame.py`,
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
  cond. The value applies immediately (no +1 probe since 0.2.5); a server
  `tp` correction settles at +1 (or off if +1 itself is rejected) and the
  GUI shows the limit. The spin has no small
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
  (vnum 1955, `u_i` from the Main/Etc bag) and waits for `wopen 32`. When the
  bazaar is already open before the attach there is no new `wopen`; the
  verified Bibi dialog (460; Carve Runes 11009 is never clicked) plus the
  listing answer continue the scan. It
  searches `c_blist` at 2.5 s
  pacing, parses the real 15-field `rc_blist` (minimum visible price,
  truncation-aware), keeps old rows across refresh cycles, sorts by profit
  and has an **Only priced** filter. Relist is gated by the relist set (a
  per-row tick, **Add from Items** / **Add from Inventory**, **Remove from
  relisting**), an **Only relisting** filter, Ctrl+click multi-selection and
  **Group actions…** (select/unselect all, relist/stop all shown or
  selected); the live checkbox, a character name, a positive floor and an
  inventory slot, reports **register accepted (rc_reg)** or **register
  ignored (no rc_reg)** (a c_reg needs the bazaar window — `wopen 32` — and
  `unk1` is repaired from Item.dat before a pass; `unk2` is item-specific), and **Relist Start**
  searches only the relist set while Monitor searches every enabled item.
  The page has a second-level navbar (**Inventory**,
  **Relist**, **Monitor**, **Items**, **Configuration**); **Monitor** has a
  third-level navbar, **NosMall** (ND cost; gold per ND) and **NPC resale**
  (NPC gold cost, e.g. Pet Food or Bell of Sweet Home; Markup = listing ÷ NPC
  price). One Start searches both; each view has its own Only priced filter.
  The editor Cost type chooses the view; NPC gold auto-fills from Item.dat
  when left at 0. **Inventory** scans
  every bag the client reports (including equipment bags 0/6/7, quantity-0
  rows and bound negative vnums), shows Item.dat names, NPC prices and known
  ND costs, and **Set up relist…** adds the item to the relist set and opens
  **Relist** with the exact vnum/tab/amount for the floor and start; the
  **Items** editor carries **Monitor**/**Relist** tracking switches;
  **Price all** adds one search per
  distinct owned item. The **Monitor** table and the **Relist**/**Items**
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
~/.config/ntpl/bazar.json              fallback, 19 curated Nostale-NB items
~/.config/ntpl/bazar-<winpid>.json     per attached client (active: 4356)
~/bazar.json                           visible copy of the 19-item config
~/bazar-nosmall-all.json               1,184-group catalog export
~/.config/ntpl/nostale.db              19 MB game/packet SQLite DB
~/.cache/ntpl/sources/                  cloned public packet projects
~/.cache/ntpl/mirror/                   cross-client Follow mirror bus (JSON lines)
~/.config/ntpl/bot-<winpid>.log        per-client bot/action log
~/.config/ntpl/minigame-<winpid>.json  per-client Sheep Farm key/focus settings
```

## Next likely work

1. Live-verify the Minigame tab at a Sheep Farm raid (automatic at :15 on
   channels 1/2): restart the game with DLL 0.3.0, inject, start with `y`, then
   watch the Bot log for `minigame keyboard ready` / `minigame focus -> …` and
   the Wolves score; try `z` if `y` does nothing (QWERTZ).
2. Live-verify the bazaar open/search replay while standing at Bibi Basar
   (the controller already waits/retries; last live attempt was out of range).
3. Live-test **Mirror movement & spells** with two of our own clients: both
   GUIs publish to `~/.cache/ntpl/mirror`; the follower should replay the
   leader's walks and spells with its own char id. Fake-server verified.
4. Live-test `c_reg` on a cheap item; no server acknowledgement is captured,
   so relist is currently fake-server verified only.
5. Grow the NosMall ND price map: the importer now lists only priced,
   non-pet groups (18 today). Client COST blocks do not contain the web-shop
   price; add an item with **New**, set its Cost, then reopen the picker, or
   extend `known_reference_costs`.
6. If desired: a CLI `ntpl bazar` monitor and a read-only MCP bazar tool
   using the same controller; a price-map importer.
7. Remember the UI preferences: never clear the table at a cycle start;
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
