# Project context — 2026-11-19

Quick map for the next session. The detailed history is in
[handoff-2026-11-19.md](handoff-2026-11-19.md); packet formats in
[protocol/](protocol/README.md); the market feature in [bazar.md](bazar.md);
the data layer in [database.md](database.md).

## Current state

- **Linux host**: 279 unittest cases, 3 GTK4 smokes, 37 fake-server bot
  smokes, and the native `speed-test` pass. Build: `make -C linux all
  test-host`, tests above.
- **Speed +%** (Path section, default 0): the DLL scales the local player's
  incoming `cond` speed before the client parses it and keeps its `walk` speed
  consistent. Client hack, high ban risk; reverts when the GUI disconnects.
  Needs the 2026-11-20 DLL (a game-client restart/injection), not just a GUI
  restart.
- **NosBazar tab** (fourth GUI page): monitor + floor-protected relist ported
  from `TaraJura/Nostale-NB`. It learns the Bibi Basar entity id/option from
  the client's own `npc_req`/`n_run` or DLL log, searches `c_blist` at 2.5 s
  pacing, parses the real 15-field `rc_blist` (minimum visible price,
  truncation-aware), keeps old rows across refresh cycles, sorts by profit
  and has an **Only priced** filter. Relist is double-gated and reports
  `register sent (no ack)`.
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
```

## Next likely work

1. Live-verify the bazaar open/search replay while standing at Bibi Basar
   (the controller already waits/retries; last live attempt was out of range).
2. Live-test **Mirror movement & spells** with two of our own clients: both
   GUIs publish to `~/.cache/ntpl/mirror`; the follower should replay the
   leader's walks and spells with its own char id. Fake-server verified.
3. Live-test `c_reg` on a cheap item; no server acknowledgement is captured,
   so relist is currently fake-server verified only.
4. Grow the NosMall ND price map: the importer now lists only priced,
   non-pet groups (18 today). Client COST blocks do not contain the web-shop
   price; add an item with **New**, set its Cost, then reopen the picker, or
   extend `known_reference_costs`.
5. If desired: a CLI `ntpl bazar` monitor and a read-only MCP bazar tool
   using the same controller; a price-map importer.
6. Remember the UI preferences: never clear the table at a cycle start;
   profit-sorted with Only priced; cp1252/latin-1 name decoding.

## Commands

```bash
cd ~/games/gameforge/NostalePacketLogger
make -C linux all test-host
make -C linux host-test gui-test bot-test
PYTHONPATH=linux/host python3 -m ntpl db build
PYTHONPATH=linux/host python3 -m ntpl db stats
PYTHONPATH=linux/host python3 -m ntpl db packets --opcode rc_blist
PYTHONPATH=linux/host python3 -m ntpl db nosmall --search "Soulstone"
linux/scripts/gui.sh
```

Gameforge prohibits automation; the account risk is the owner's. Do not read
or store credentials/session data (`usercache-*`, `webcache-*`).
