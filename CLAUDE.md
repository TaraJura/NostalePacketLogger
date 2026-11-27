# CLAUDE.md

Working context for Claude Code in this repository. Keep it current: fix a line
that stopped being true instead of appending a new one. Never write account
names, passwords, session data or anything from `usercache-*` / `webcache-*`
here.

## What this is

A packet logger and automation engine for the MMORPG NosTale. A 32-bit Windows
DLL is injected into the game, hooks the client's own recv/send functions,
copies plaintext packets into a bounded queue, and serves them to a host over
TCP loopback. The host can display, log, replay and inject packets, and feed a
world-state model. The long-term goal is a game engine that can play through
packets; the packet wiki is the foundation.

Location: `~/games/gameforge/NostalePacketLogger` (its own git repo,
`github.com/TaraJura/NostalePacketLogger`). It is not part of the Gameforge
client setup in the parent directory; use a separate Wine prefix.

## Status

Last checked 2026-11-27. Current state and open work:
[`docs/context.md`](docs/context.md). A game session is normally driven through
the Gameforge prefix (`WINEPREFIX=~/games/gameforge/prefix`), not
`~/.wine-nostale`; after any game restart the GUI's client picker injects into
the new window (or run `inject.sh` yourself).

| What | State |
|---|---|
| `windows/` | Original MSVC/C# implementation, moved unchanged. Builds with Visual Studio; not exercised on this machine. |
| `linux/` build | `make -C linux all test-host` is warning-free; all three outputs are PE32 i386. |
| Linux host tests | 395/395 pass (`make -C linux host-test`); the native `speed-test` and `key-test` pass too. |
| Game/packet DB | `ntpl db build` fills `~/.config/ntpl/nostale.db` (`NTPL_DB`) from the client archives plus 14 public projects cloned under `~/.cache/ntpl/sources` (`NTPL_SOURCE_CACHE`): 7,805 `Item.dat`, 2,723 `monster.dat`, 1,964 `Skill.dat`, 779 maps, 3,639 NosMall records / **1,184 searchable groups**, 1,004 indexed packet definitions (NosSmooth, NosCore.Packets, OpenNos, go-noskit, NosBazar, Gfless, auth/crypto, human-readable local wiki). Source text/license/commit kept in `source_files`; `ntpl db stats|sources|packets|items|nosmall` query it. The full NosMall catalog is `ntpl db nosmall --export FILE --all` or the GUI **Import NosMall…** picker (DB groups, `c_blist` built per group; the picker lists only groups with a known ND cost and excludes pets, fairies and partner supplies). NosMall ND prices are not in the client data; the curated reference costs plus the costs of already-configured items are the price map, so unpriced groups are not importable. Docs: `docs/database.md`. |
| NosBazar tab | Fourth GUI page, ported from `TaraJura/Nostale-NB`: searches configured `c_blist` vnums (2.5 s pacing, one in flight, retry pass, waiting state), shows the cheapest listing (seller field 2, price field 6, vnum field 3), computes gold/ND or NPC markup, and optionally relists (a character name + positive `min_price`; Relist Start is the only switch, there is no extra allow-live checkbox). The Bibi entity id/option are learned from the user's own `npc_req`/`n_run` or the DLL log (`spawnlog.load_bazaar_open`), but an id this map's entity list knows as a non-Bibi entity is refused (entity ids are per map instance; live 2026-11-23 a stale id opened Carve Runes), so Start waits until a real Bibi is visible instead of opening a random NPC dialog; inventory is `inv`/`ivn` in `WorldState.inventory`, backfilled from the log (`spawnlog.load_inventory`) and parsed by `engine.parse_inventory_token`, which keeps equipment bags 0/6/7, extra fields, quantity-0 rows and bound negative vnums. The NosBazar page has a second-level navbar: **Items**, **Relist**, **Monitor** and **Configuration**. **Items** loads names/NPC prices from the local DB first (`database.item_info`, `Item.dat` fallback) as soon as the section opens (an empty lookup retries on a later page show; **Configuration → Reload names** forces a reload after rebuilding a stale DB, no restart) and is a virtualized searchable catalog of every Item.dat row (about 7,805) plus configured rows the DB lacks and inventory-only vnums, with a **Monitor** and a **Relist** tick per row (a DB row is created on the first tick; Relist sets floor 1 and fills vnum/tab/stack from the bag; an **All items / Monitored / Relisting** filter narrows the catalog to the monitored items or the relist set); the **Monitor** table, **Relist** list and **Items** list show the same real name for each item's vnum (exact `vnum`, else the first resolving search-group vnum), using the configured `name` only when no vnum resolves — the shortcut stays the config/result key, appears in the item's tooltip with the search vnums, and is what the editor's **Name** field edits; the selection opens the full editor; the **Relist** compact editor sets vnum/tab/amount/min, marks items ready/needs-floor/needs-vnum-tab/not-relisting and relists (the in-game Change `c_mod` for an unsold own listing, else a new `c_reg`); the **Relist** list shows the relist set by default (**Only relisting** is on; turn it off to browse all), with a per-row tick to remove an item (removal keeps it configured, monitored and its floor), a one-row **Add from Items / Add… / Actions…** toolbar (Add… = whole inventory or the Item database; Actions… = **Select all shown**, **Unselect all**, **Remove from relisting**, **Relist all shown**, **Stop relisting all shown**, **Relist selected**, **Stop relisting selected**; one config save per group action) and Ctrl+click multi-selection; a tick updates its row in place and every render restores the scroll adjustment, so a scrolled list does not jump to the top; the **Items** editor has **Monitor**/**Relist** tracking switches; `Relist Start` queues only `BazarController.relist_items()`, while Monitor searches every `enabled_items()` (a config without the flag infers `relist` from `min_price > 0`; `BazarItem` defaults to relist **off**, and every monitor-first path — NosMall import, catalog export, inventory scan — sets it explicitly); the server acknowledges an accepted `c_reg` with `rc_reg` (3 s timeout → `register ignored (no rc_reg)`), but only with the NosBazar window open (`wopen 32`; `rc_blist` is answered without it — live 2026-11-23 it answered while Carve Runes was open and every c_reg was ignored); when the bazaar was already open before the host attached the server sends no new `wopen`, so a verified Bibi dialog (`npc_req 2 <id> 460`; Carve Runes opens 11009 and is never clicked) plus the listing answer is accepted and the scan continues; a visible Bibi Basar is opened through `npc_req`/`n_run`; a client whose own shortcut opens the bazaar (FrosTale: a bare `c_skill` answered by `wopen 32`, learned live or from the DLL log by `spawnlog.load_bazaar_skill`) is replayed first; with none visible the controller uses the portable NosBazar medal (vnum 1955, `u_i 1 <charId> <bag> <slot> 0 0`) and waits for `wopen 32` (a stale unknown NPC id is only tried when no medal exists); each relist pass asks the seller tab (`c_slist 0 0 0` → `rc_slist`, parsed by `parse_rc_slist` with listing id, vnum, sold amount, amount and price): when our own listing of the item has **sold 0** the pass reprices it with the client's own Change action `c_mod 0 1 <listingId> <vnum> <amount> <price>` (no inventory slot needed; confirmed by the refreshed seller list carrying the new price; live-verified packet shape 2026-11-23) instead of adding a new listing, and only a listing with any amount sold (Change gone) or no own listing gets `c_reg`; `build_register_packet` sends the client's exact twelve-field `c_reg` and its fee is `max(50, int(price*amount*0.0005))` — the live server silently drops a registration whose fee is below the 50-gold minimum (2026-11-23: failures carried 4/23/28 gold, accepted packets at least 50); the DLL log is seeded once per attach (Rescan forces a re-read) and the reader re-reads the DLL port file on every failed connect, so a reused Windows PID with a stale port file (live 2026-11-23: 47823 vs the DLL's 47822) recovers by itself; Relist Start repairs only the first constant for Main/Etc items (`unk1 = 8 + INDEX[0]`; `unk2` is item-specific and left as configured); a repeated click on the only selected relist row unselects it; **Price all** adds one search per distinct inventory template (deduped by absolute vnum) and starts Monitor — never a relist. **Monitor** has a third-level navbar: **NosMall** (positive `nos_cost`; gold/ND) and **NPC resale** (NPC `npc_cost`; the Markup column is listing ÷ NPC price, e.g. Pet Food or Bell of Sweet Home); one Start/Stop searches both, each view has its own **Only priced** and row count, and the editor's Cost type chooses the view (NPC gold auto-fills from Item.dat when left at 0). The Monitor section's left panel lists only monitored items, with a compact **Add from Items / Add… / Actions…** toolbar (Add… = whole inventory or the full Item.dat picker; Actions… = monitor/stop the shown or selected rows, select/unselect, Open full editor); the Items **⋯** menu has the bulk switches over every configured item (Monitor all / Stop monitoring all / Relist all — re-enables rows and sets floor 1 where unset — / Stop relisting all). **Configuration** holds the Bibi id/refresh/config file plus **Reload names**. **Import…** has the NosMall catalog, the full Item.dat picker and saved config files (**Import config (add)…** merges and skips duplicates; **Replace with config…** swaps the list after a confirmation; old files without `relist` load monitor-only). ⋯ holds Rescan inventory, Price all inventory, Add whole inventory, the bulk Monitor/Relist switches and selection. Equipment quantity 0 counts as amount 1; negative vnums are searched by magnitude but cannot be relisted. The server sends `inv` only at login; when the 16 MB DLL log was truncated before the dump (`spawnlog.log_was_truncated`), the Items summary says so (a list built from later `ivn` updates alone is marked partial) and a relog/change channel with the window open fills it automatically. Every complete inventory is cached per character at `~/.config/ntpl/inventory-<char_id>.json` (`NTPL_INVENTORY_DIR`) and loaded when live/log data is missing, shown as `cached <time>`. Proof: `gui_inventory_scan_smoke.py` (including cache save/restore), `gui_relist_track_smoke.py` (relist-set ticks, add/remove, persistence), `test_inventory_cache.py`. Live capture 2026-11-19: NPC 10284, `n_run 60 0 2 10284`, `wopen 32 2 717`, 15-field `rc_blist` entries (`ownerName` 2, `vnum` 3, `amount` 4, `price` 6); the DLL's 2048-byte slot truncates long answers, so the parser keeps every intact entry, takes the minimum visible price and flags `truncated`. Config per client: `~/.config/ntpl/bazar-<pid>.json` (`NTPL_BAZAR_CONFIG` overrides). Docs: `docs/bazar.md`, `docs/monitoring-config.md`, `docs/protocol/bazaar.md`. |
| Headless client | `ntpl headless` ports NosBazar's login cipher, `NoS0577`/`NsTeST` and world handshake to Python. Live 2026-11-08: the login server at `79.110.84.75:4000` answers `failc 1` to an invalid token, proving the packet layout, client version/hash and login cipher. The v2 email/password login is **live-validated (201)**; without an identity file the client signs in through gameforge.com's own form in headless Chrome (Node/CDP; the site solves its PoW challenge) and **iovation returns 200**. The final `thin/codes` call returns `403 Not allowed to create code` for browser-generated devices (the official client adds a `codeId` authorization ticket; Gfless uses a captured identity). A 30-value identity (`--identity`) or a launcher token (`--token-file`) is needed for the game token. The **image-drop captcha** is handled in one GTK window; the website PoW is handled by the site. Docs: `docs/headless.md`. |
| MCP (agent access) | `python3 -m ntpl mcp` speaks MCP JSON-RPC over stdio and exposes 22 tools (state, entities, drops, map, portals, time-spaces, A* path, spells, move/travel, attack/cast, raw send, rest, resync, pickup, wait, memory). It owns the DLL's single host connection and rebroadcasts it on `127.0.0.1:47900`; run the GUI with `--port 47900`. The DLL port is discovered from its `C:\ntpl-<pid>.port` file (the DLL may have fallen back from 47821 to 47822+). The DLL accepts a host only while none is connected (a second connection waits in the backlog), so a GUI→MCP handover pauses the GUI and destroys its socket; the GUI reconnects afterwards. At startup the service seeds `world.entities` from the attached client's log (`spawnlog.load_entities`: `in`/`mv`/`tp`/`out`, map-change clears), so stationary monsters are known. `game_resync` sends `walk -1 -1 0 10` and applies the server's `tp` when the client desyncs after a warp. `.mcp.json` is committed; docs in `docs/mcp.md` and `docs/autonomous-play.md`. |
| Autonomous play | Playbook in `docs/autonomous-play.md`: observe → decide → act → confirm → survive. Live 2026-11-22 on client winPID 1996 (char 10170): Ranger card 903 equipped (`u_i … 6 0`, server `sc`/`ivn`/`eq`/`sp`), Warp Tower NPC 955/entity 3095 dialog 439 → `n_run 16 1 2 3095` → map 20 Mountain Cave 1, `walk -1 -1` resync → `tp 15 96`, then a Wolf (vnum 200) killed with skill 833 (`su` HP 4366→0) and two attacking wolves killed in self-defence; final HP 91%, GUI reconnected, game never closed. |
| Map data | `NStcData.NOS` parsed (779 grids, one byte per cell). Live 2026-11-07: map **2** NosVille Meadows (150x150), then map 1 NosVille (160x180) via `c_map`; 2026-11-06: map **20** Mountain Cave 1 (230x260). Map names come from `NSlangData_UK.NOS` → `_code_uk_MapIDData.txt` (`zts<id>e<TAB><name>`, 374 entries). `astar()` is 8-direction with sqrt(2) diagonals and no corner cutting; the path is drawn as a continuous polyline. |
| Monster names | Two-step join in `monsternames.py`: `NSgtdData.NOS` → `monster.dat` maps the template vnum to a language text key (`VNUM 419` → `NAME zts489e`), and `NSlangData_UK.NOS` → `_code_uk_monster.txt` maps that key to the localized name (2,679 entries; live map 103: vnum 419 = Violent Seedle, 420 = Crazy Dander, 421 = Violent Chicken). The text key is **not** the vnum (record `zts419e` is "Wheat"). Only `monster.dat` is decrypted from the 18 MB archive, so loading takes ~0.4 s. `spawnlog.py` backfills entities that spawned before the host attached from the attached client's own `C:\NosTalePacketLog-<pid>.txt` (`[RECV] in ...` lines), so several clients in one prefix never mix session data. The backfill reads only the log's last 8 MB (the login `ski` list from the first 8 MB), so a 100+ MB log no longer freezes the GUI on attach. |
| Spell catalog | `NSgtdData.NOS` → `Skill.dat` parsed by `spells.py`: 1,964 records, 791 active (`TYPE[0]==1`), with name from the `NAME` zts key, MP cost, range, cast time, cooldown and target type. Live examples: 866 Holy Attack (0 MP/0.9 s), 869 Mana Shield (self, 300 MP/60 s), 876 Holy Hammer (145 MP/90 s). The GUI **Add spell…** picker uses it; the bot enforces MP/range/cooldown/cast time. |
| Map UI / bot | GTK4 window with a startup **client picker** (lists every running `NostaleClientX.exe` window through `Injector.exe --list`, which numbers the visible game windows in their title bars `NosTale [#n]` so each picker row and the game window share a number; pairs the row with the DLL's `C:\ntpl-<pid>.port` file and injects the chosen client; **Client…** in the header reopens it; one already-injected client is attached silently) and nine tabs: **Packets**, **Map / Bot**, **Bot log**, **NosBazar**, **Family**, **Malicious**, **Minigame**, **Stats** (a player DPS meter fed by every `su`: only attacker type 1 with positive damage, damage is args[12], target HP args[15]/[16]; a 5/10/30/60 s or session window, Reset, rows sorted by DPS with name, hits, share, last hit and session total; the table refreshes twice a second while the tab is visible while the meter always collects — see `docs/stats.md`), and **Pet Trainer** (its own row below) (the Packets table is paused while its tab is hidden, keeping the last 500 packets to replay on reopen, because appending every busy-map packet froze GTK; the DLL session log has the full history) (timestamped actions for debugging: attacks, TARGET selections/errors, walks, pickups, recovery, DLL errors; 4,000-line cap, Clear log; mirrored to `~/.config/ntpl/bot-<winpid>.log` once a client is attached (`bot.log` before that), `NTPL_BOT_LOG` overrides, 4 MiB rotation). The Map/Bot page has a responsive layout (map + sidebar, reflows under ~1050 px), automatic whole-map (contain-fit) view with no manual map/scale/center controls, **immediate map switch** from `c_map`/`at` (the map id is the second `c_map` field), **attach-time auto-detect** when no map id was seen (all live entities, tightest perfect fit — same-layout maps tie on positions alone), structured sidebar (Character HP/MP bars + recovery banner, Path, Follow (trail a named player), Combat with green/red toggles, Targets with an unknown-vnum option, Spells, Loot, Recovery, Configuration), **Block portals** (Path section, **off by default** — portals stay usable unless the user opts in) keeps the bot on one map: the GUI sends `PORTALS|1` on every connect and the DLL drops the client's own `preq` portal request (logged as `SEND|preq`, server never sees it), A* treats the `gp`/learned portal cells as obstacles, a map click on one is refused, every `MOVE` is checked against them (straight leg included), and a drop/monster on a portal is approached from a neighbouring cell; the setting is saved as `block_portals` and the live state appears as `portalblock=BLOCKED`. The **Family** tab invites members: it sends the client's own `%Familyinvite <name>` command (the raw command packet, not `say`), picks up manual `%Familyinvite` sends from the SEND stream, and shows the server's `infoi2 234 1 <name>` confirmation. **Auto-invite players with no family** scans map players whose `in 1`/`gidx` family state is known and empty (family/fairy fields are backfilled from the client log for players who spawned before attach), invites nearest first one at a time, waits for the confirmation, then waits out the server's 15 s invite cooldown (shown as a **Next invite in Ns** countdown; `infoi 2359` starts it and keeps the name retryable). It persists every confirmed name per client (`~/.config/ntpl/family-<winpid>.json`, `NTPL_FAMILY_STATE` overrides) so nobody is invited twice, even after declining; **Add to list** / **Remove** edit the list manually. **Only players with a fairy** filters auto-invite to known no-family players whose `in 1` equipment fairy vnum or `pairy` element is present (no-fairy players are usually fresh alts); the map list labels `fairy`/`no fairy`/`fairy unknown` and the option is saved with the list. The Family tab uses a full-height split view: Players on map (only actionable rows — known no-family, invited, no-reply — with Invite/Retry) on the left, Invited players (with Remove) on the right; the header shows the total on the map and the list refreshes every second. A silent invite (no `infoi2 234` in 4 s) is marked no reply, skipped and auto-invite continues; five silent attempts in a row stop it (wrong client / no family permission). Live manual test from PID 2740: `sjkrimer` and `redenkor` both confirmed; auto is covered by `gui_family_auto_smoke.py`. It works without the bot. See `docs/family.md`. **Speed +** (**Malicious** tab, **default 0 = off**) adds a flat bonus to the character's walking speed the way a NosTale speed buff works (+1, +2, +3...): the spin has no small cap (the DLL's emulated bound is 2,000,000,000), the DLL reads the server's `cond 1 … <speed>` for the base (or the player entity's current speed if no cond arrived), calls the client's own entity speed setter (`0x00492938`) on the local player actor (`[player+0x20]+0xAA`, the byte the walk builder reads), and clamps the outgoing `walk` speed back to the server's cond value (zero-padding the field when the base has fewer digits; the cond packet is not modified); a 25 ms watchdog re-applies it because the client's own cond handler resets the entity on every packet. On the first value above the client's own 59 limit the DLL raises the setter's cap byte to `0xff` and repoints the 11 code references to the 60-entry speed table at a 256-entry copy with the game's threads suspended (entries 0–59 are the client's own values, 60–255 continue the linear timing, entry 255 is scaled for emulated speeds above the byte maximum); the table stays allocated for the client process, so the patched code remains valid after the DLL unloads, and a mismatching build reports `STATUS|SPEED EXTEND FAILED` instead of patching. The value applies as soon as the spin changes (manual play included, no bot needed), is saved as `speed_boost`, is sent as `SPEED|<add>` on attach/change and reset with `SPEED|0` on shutdown. Live 2026-11-20 (PID 1948, base 11): +3 applied (actor 14), every walk packet clamped to 11, no `tp`. Live 0.2.5 check at 15:05 (PID 3856, base 12): `SPEED|2` answered `STATUS|SPEED APPLIED 14` in 22 ms with no probe, and `SPEED|0` reset 14 -> 12. The value applies immediately (0.2.5; the +1 probe was removed); 0.2.6 adds the above-59 table extension (not yet live-verified); 0.2.9 raises the speed cap to 2,000,000,000 and pairs with the host's full-range spin. On a lone server `tp 1 <self>` it does **not** settle any more (live 2026-11-26: the client also gets one on a death respawn, a partner mount/dismount and a resync, with the walk speed clamped the whole time) — DLL 0.3.2 settles only on a burst, 3 corrections within 10 s, the real correction loop: at +1 (`STATUS|SPEED TUNED 1`, GUI silent spin update) or off if +1 itself was rejected (`STATUS|SPEED CORRECTED`, spin 0); a new SPEED request starts a fresh window. This replaces the 2026-11-20 one-`tp` safety net, which kept dropping the boost to +1 every few minutes. This is a client hack; the vendor labels speed hacks high ban risk. **Cast speed** (same tab, a plain on/off switch, default off) works with or without the bot, host-side. The client's skill-bar cooldowns stay accurate and visible (the GUI sends `CAST|0` on attach to undo the 0.2.8 zeroing; old DLLs ignore it). **Manual (no bot):** press a skill once and it is remembered; every `sr <slot>` (the server's cooldown-end packet) re-sends that exact press, so the character keeps casting at the server's fastest rate; pressing another skill switches the assist, turning the switch off stops it, and a session is capped at 5 min / 120 s idle. The DLL's two echo lines for host sends are ignored (`_host_skill_sends`). **Bot:** no waiting for the previous `su` — the next eligible spell/attack is sent immediately and overlaps the current cast (live PID 676 analysis: the server accepts overlapping casts, 385 accepted vs 21 refused), the rule cooldown and post-cast pause are dropped, and a skill is re-cast only on its `sr`. Each spell still lands after the server's own cast time; same-skill sends faster than its cooldown are refused. Saved as `cast_boost`, `CAST|0` on attach/shutdown. Proofs: `gui_cast_assist_smoke.py` (bot off: press → repeats on `sr`, switch skill, off stops) and `gui_cast_ready_smoke.py` (+0.05 s on `sr`, different spell 0.06 s later — overlap). High ban risk. Paths are replanned from the current position at Start, and any goal farther than 3 cells (a raw waypoint after a loop wrap, a stale route) is walked in <=3-cell MOVEs; a far `walk` is rejected by the server and leaves the character standing still. The bot also has drops + 8-direction A* path + loop + auto-attack/Retaliate + pickup (walks to drops up to 20 cells, then resumes the path) + low-HP/MP sit/recover (finishing the current kill first — unless combat is paused by refusals or the target has not been attacked for 12 s (`FINISH_KILL_GRACE_S`, which fixes recovery "working only after Stop/Start"), then it heals instead — and standing to fight back when hit. Recovery priority is Retaliate -> finish the current kill -> recover: `recovery_pending` is set as soon as a threshold is crossed, and from then on the bot cancels route/loot walks and never picks a new monster — it only retaliates and finishes the current target, then sits) (the bot clears in-range monsters before taking the next path leg; with no path it chases an out-of-range target on foot), monster target filters (all/only/except by vnum), ordered spell rules from the scanned Skill.dat catalog (Always/my HP/my MP, cooldown, MP/range/cast-time checks, monster/self), JSON save/load of the whole bot config, including the drawn route (waypoints, path start, map id, Draw path state), bigger own dot + facing arrow, and hover tooltips for players/monsters/NPCs/drops. **Follow player** (a player name or char id in the **Follow** frame) makes the character stand up and walk toward that player through the client's own MoveTo, staying within two cells; while it is on, normal combat, recovery and the drawn path stay idle (loot near the player is still collected when the Pickup toggle is on), and a player out of sight is reported (throttled to 5 s) with the map, visible names and any nameless player char ids (a player already in view before injection has no `in` packet, so Follow accepts its char id) and waited for; players who spawned before the GUI attached are seeded from the client's own log (`load_player_spawns` — `in` + later `mv`, last position wins). With **Attack the same target as the player** checked, the follower also attacks the monster in the player's latest `su` (5 s TTL) as soon as it is in range and in line — including while still walking toward the player; walking resumes between casts. It never picks a self-chosen target and never chases. With **Pickup** on it also collects own/free drops within 10 cells of the followed player and then resumes following. The settings are saved as `follow`/`follow_player`/`follow_attack`/`follow_mirror`. **Mirror movement & spells** (Follow frame) makes every attached client publish its own `walk`/`u_s` to a per-user file bus under `~/.cache/ntpl/mirror` (`NTPL_MIRROR_DIR` overrides); the follower replays the followed character's actions across separate GUI processes, resolves leader `u_s` slots to vnums and then to its own slots (falling back to the leader's published slot when the follower's own `ski` list is missing, including slot-only `skill = 0` events), substitutes its own char id for a self/ally cast aimed at the followed player or itself, paces mirrored casts by the leader's rotation rather than each spell's full cooldown, and falls back to the server's `mv`/`su` broadcasts (deduplicated against the bus) when the followed host publishes nothing. **Combat reads Skill.dat:** Skill `0` is the equipped SP card's basic attack, detected from `u_s 0` + `su` (Energy Bolt 260 on the live mage, not the old hardcoded 866), and its range/cast/cooldown/MP drive the attack. The Combat UI is only Auto-attack/Retaliate plus that info line — no Skill/Search/Min delay/template fields; the bot always uses slot 0, the skill's own range, a 15-cell acquisition radius and no extra delay. Focus fire is strict: the current target is kept until it dies — a closer monster, another attacker (even with Retaliate on) or a momentarily blocked line does not steal it; Retaliate only picks the first target and takes over after the current one dies. While the engaged monster is in skill range and line the bot fights in place: no drawn-path leg, no chase and no loot walk (a drop at the feet is still picked). If it steps out of range or cover, or combat is paused by refusals, the bot does not freeze — the path (or the stationary chase) moves instead; `_engaged_target` only suppresses far loot walks during a fight. Attacks require a clear line of sight over walkable map cells (no firing through town walls); a blocked line keeps the target (the bot waits or walks), and repeated server refusals pause combat instead of switching (the pause ends early after moving 5 cells or when a resolvable attacker hits the character, which fights back). On death the bot stops immediately when **Revive on death** is off (default); when it is on, it answers the `dlgi #revival` dialog with the client's own Continue burst (10 Seeds of Power) and resumes on `revive`. Strict focus keeps every target that lands hits, even through refused casts; a target that never lands one (the server cancels every attack — a client-only ghost) is dropped after 3 consecutive refusals and the refusals pause combat. A `TARGET ... ERR` refusal (the client cannot select the object) drops the target too; a **build mismatch** (the running DLL's hardcoded selector is for another client build) instead disables DLL TARGET for the session, logs the hint and falls back to injected `ncif` without blocking the monster — client 0.9.3.3262 moved the selector to `0x005699D0` with the controller at `[0x007944F0]` (one load), re-derived in **DLL 0.3.3**; older DLLs cannot select, and injected `ncif` does not set the client's own selection, so attacks may be refused until the game restarts with 0.3.3. Retaliate ignores the target filter and works on its own (the monster that hit you is attacked back, and only that one, even when its vnum is not selected); recovering never continues while the character has been hit recently (the bot stands and fights back, even when the attacker is not in the entity list), sit/stand requests are debounced to 5 s (`REST_RETRY_S`) because `rest` is a toggle whose confirmation lags under multi-client load, and loot is picked up right away, also while monsters hit the character (free drops marked `-1`/`0` and own drops; another player's protected drop is skipped); only below the Recovery HP threshold does the bot defend first and leave drops for after the fight; a server `cancel` after a landed `su` is the normal end of that cast (cancels are paired with the cast they end, so a fast next attack cannot turn it into a refusal), while a cancel with no hit clears the cooldown for an immediate retry. The **Targets** list shows only monster vnums spawned on the current map (selected ones not on it stay marked). At startup the GUI **seeds the map id, position and char id from the DLL log's last `at` + own walks**, so an attach-after-login shows the real map (the label marks a position-only guess `· auto`; with no data the canvas stays blank "waiting for the game…"). **Portals are server-sent**: on map entry the server sends `gp <x> <y> <toMap> …` for every portal of that map, stored in `WorldState.gates`, drawn as rings with a "Portal → map N" tooltip and persisted in `~/.config/ntpl/portals.json` (`NTPL_PORTALS` overrides); a crossing is only the fallback when a `gp` list was missed. Right after `gp` the server sends the map's **Time-Space entrances** as `wp <x> <y> <tsId> <flag> <minLv> <maxLv>`; they are stored in `WorldState.timespaces` and drawn with the nosapki stone icon in a gold frame, tooltip in the client's own wording: "Time-Space Stone / Required Level 26". Live map 4 has all eight (TS 16, 17, 18, 19, 20, 21, 22, 704); map 3 has TS 5, 6, 10, 11, 12, 13. A `gp` destination in the 2xxx range is a Time-Space **instance** gate (map 4: (224,103)→2110, (228,53)→2510) drawn as an amber ring. The icon asset is `linux/host/ntpl/assets/ts-1.png` from `nosapki.com/images/nt-images/ts-1.png`, with an amber-ring fallback when missing. The spell picker always lists all active spells and marks `slot N` / `not learned` (only when `ski` is known) / `slot unknown` (no list — use the spell once or relog); slots are learned from `u_s`/`su` pairs because `ski` is sent at login. `make gui-test` (6 smokes), `make speed-test`, `make key-test` and `make bot-test` (48 smokes) pass. |
| x86 hook stubs | `HookStubTest.exe` (Wine) calls the real `hooks.S` detours and verifies the EDX packet pointer at `20(%ebp)`, the original-call replay, the send trampoline and the drop path (a 0 return skips the trampoline; read `-40(%esp)` after `popal`, not `-4(%ebp)`). Passes. |
| GUI | Six headless GTK4 smokes pass (`make -C linux gui-test`, Xvfb): the packet window (fake stream → 2 rows + STATUS in the status bar), the client picker (two clients listed, Connect attaches to the chosen port, the uninjected row offers Inject & connect), the multi-client window (a child process owns one client's attach lock; the window skips it, auto-attaches the free client, and releases its own lock on exit), the Family manual invite (`%Familyinvite`, `infoi2 234`, invalid names, manual-command mirroring), Family auto-invite (only known no-family players, one invite each, persisted list, no retry), and the Minigame key/focus window. `gui.py` is non-unique: one window per game client, with per-client attach locks under `~/.cache/ntpl`, a distinct window title and its own bot config. |
| Injector | Verified under Wine end-to-end against both the fake target and the live client (PID 1216): remote `LoadLibraryA` succeeds and the DLL starts. `--list` numbers every visible game window in its title bar (`NosTale [#2]`) so the picker row and the window share a number; `--no-tag` disables it. **Refuses to inject when a `PacketLogger*.dll` module is already loaded** in the target (two DLLs hooking the same functions corrupt the client, seen live 2026-11-18); restart the game client to inject again. The module stays mapped after `QUIT`, so this also blocks re-injection after a graceful stop. |
| Multi-client | **Supported since 2026-11-18.** Each injected DLL writes its own `C:\NosTalePacketLog-<pid>.txt`, so backfill never mixes clients; the GUI is non-unique (one window per client, per-client attach locks) and reads the attached client's log by Windows PID. `inject.sh`/the picker injects every running client; run `gui.sh` once per client. 385 host tests + the native speed-test/key-test + the 6 GUI smokes pass; four simultaneous live clients were running on pc1 (4-window bot run not yet done). |
| Minigame tab | Seventh GUI page, appended after Malicious. **Sheep Farm**: Start spams `KEY\|<key>` (default `y`, the secondary attack; `z` on QWERTZ) every 40–5000 ms, and **Focus nearby players** selects only `kind == 1` characters within a radius (`TARGET\|id`, fallback `SEND\|ncif 1 <id>`), keeping the current player while in range and skipping a refused one for 5 s. Works without the bot; per-client `~/.config/ntpl/minigame-<pid>.json` (`NTPL_MINIGAME_CONFIG`). The DLL queues the press to a key thread (real `SendInput` when the game is foreground, otherwise `WM_KEYDOWN`/`WM_KEYUP`). Needs **DLL 0.3.0**: a game restart and re-inject, not only a GUI restart. Covered by `test_minigame.py`, `gui_minigame_smoke.py` and `key-test`; not live-verified yet. The same page has **Miniland production**: **Game** (Great Quarry 3125 first), **Play until** 1x–5x (default 5x, minimum 16,000 points), a production-points label fed by `mlo_info`/`mlo_mg`/`mlpt` (engine `WorldState.minigame`), **Play**/Stop. Play sends the client's own live-captured sequence `mg 1 <slot> 3125` (GAMESTART; the second field is the object's slot from `mlo_info`, live 4) → `mg 3 <slot> 3125 <points> <points>` (the chosen box's minimum from the server's tier table, fallback `PRODUCTION_TIERS`) → the server's `mlo_lv <level>` (0-based: 4 = 5x) → `mg 4 <slot> 3125 <level>`; `mlpt`/`mlo_rw` confirm the claim (the tab shows `Nx claimed · points → points · reward item ×N`) and it refuses below 100 production points. The Great Quarry window must be open in the game (it sends `mlo_info`); the game refuses the minigame inside a raid team, so the engine tracks the server's own `raid 1 1`/`raid 2 <leaderId>` (joined) and `raid 1 0`/`raid 2 -1` (left) as authoritative `WorldState.raid_active`, with every `rdlst` in `WorldState.raid_members`/`raid_at` only as the fallback for a host attached mid-raid; the tab refuses Play (with the hint `in a raid team (N) — leave it to play`) while the raid state says yes or the local id is in a list younger than 60 s, and unblocks at once on the leave packet (live 2026-11-26 23:02:52); **Play** is the loop (there is no separate Auto button): it claims, sends `mg 2 -1 -1` (the client's Stop/Storno; `mg 2 <slot> <vnum>` is the window close and desyncs the dialog), waits 40 s (`every`, 30–600 s — live minimum ≈40 s; an ignored run is retried up to 3×; a live "next claim in N s" countdown is shown) and starts the next run until the production points fall below 100; a raid team or a missing answer stops it; this is a packet shortcut, not a visual play-through, with a high ban risk. `test_minigame.py` covers the tiers/packets/parsers and `gui_quarry_smoke.py` the sequence. See `docs/minigame.md`. |
| Pet Trainer tab | Ninth GUI page, its own 50 ms timer and per-client settings (`~/.config/ntpl/tamer-<winpid>.json`, `NTPL_TAMER_CONFIG`). **Tame**: nearest monster of the chosen vnum (default 333 Chicken) within 15 cells, Whip (`u_s 0`) until the `st` HP is at or below the limit (default 25%), Tame on the chosen slot (default 1, skill 1787, 15 s cooldown padded to 16 s); the cast must start (`ct`), a refused press is retried in ~1 s and not counted. **End to end**: after the server's `msgi 0 1887` (pet slots full) it walks to the Miniland portal (live map 1 at 49,133), casts the summon (slot 2, skill 1788; the sparring monster is vnum 1497) and sends every tamed pet at it with `suctl <map> <count> <petId> 3 <monster> ...` every 1.5 s for the training time (default 8 minutes); then Extract Essence (slot 3, 1789, target type 2) on each pet, `msgi 0 2627` releases it and frees the slot, and the loop tames again. Extraction needs a max-training-level pet (2625/2639 → another training round, 3 max). Engine: `Entity.hp`/`hp_max` (`st`), `WorldState.pets`/`pet_entities` (`sc_p`/`p_clear`), `msgi` key and the `ct` cast log; state machine in `tamer.py` + `gui_tamer.py`. Live protocol captured from PID 836 (taming, summon/suctl training at 02:36, extraction at 02:45); the full loop awaits its first live run. See `docs/taming.md`. |
| Wine integration | Verified with Wine 11 + the fake target: DLL loads, listens on `127.0.0.1:47821`, host gets `STATUS`, `SEND` command path answers. Hooks correctly reported `NOT-FOUND` because the fake target is not the game. |
| Client build 2026-11-18 | The FrosTale client patch moved the client-specific code. The pattern hooks resolve to `recv=0x004DBEC9 send=0x007369B4 tnt=0x0078265C`; the re-derived constants are MoveTo `0x00576920`, player global `0x007954B4`, selector `0x0057A0E4`, selection global `0x00798294` (chain `[0x798294] -> 0x7934D4 -> controller`). `POS` verified live on two clients 2026-11-18. The skill hook is `NOT-FOUND` on this build; the send hook still captures the client's own `u_s`, and Block portals works through the send hook. Previous build (2026-11-06): `recv=0x004DBB4D send=0x004EB5C4 tnt=0x0055AA84`, MoveTo `0x00565E88`, player `0x00792468`, selector `0x0056964C`, selection `0x00795200`. |
| Real client | **Verified live 2026-11-06 (that build).** Injected into PID 1216 (Wine 11): both hooks ACTIVE (`recv=0x004DBB4D send=0x004EB5C4 tnt=0x0055AA84`), drops 0. `rest` controls sit/stand. Movement uses the DLL's `MOVE\|x y`, which calls the client's own MoveTo (`0x565E88`): the client pathfinds, animates and sends its own `walk`; `POS` reads its local position. The GUI bot walked `(26,133)→(23,130)` as six client walk packets (no injected walks or teleports). Raw injected walks need the checksum `1 if (x+y)%3 == 1` and are not animated. **TARGET verified live 2026-11-07** (v0.2.1, PID 1188): `TARGET|8684` → client `[SEND] ncif 3 8684` → `su ... 3 8684 855 ...`; the DLL runs the client's selector at `0x56964C`. The entity lookup validates every id occurrence during the scan, so a small id seen in thousands of tables is still found (live: six current monsters selected in ~0.5 s each). |
| Skill hook | Captures the client's own `u_s` builder at the `0x72D5B2` call site (packet in ECX) on the 2026-11-06 build; status showed `skill=ACTIVE`. On the 2026-11-18 build the call site is `NOT-FOUND` (the send hook still captures `u_s`, and Block portals drops `preq` in the send hook). The client also sends `preq` through this builder, so the same call site drops it for **Block portals** (see the send hook's return-value rule in `hooks.S`). Normal attack is `u_s 0 3 <id>`; the server answers with the card's basic skill (`su ... 260 ...` on the live mage 2026-11-07, `866/868` on the swordsman 2026-11-06). `u_s 866` itself is answered with `cancel 0 866 -1`. Live auto-attack verified. |
| GUI | PyGObject 3.56 / GTK 4.22 present; `ntpl.gui` imports. Not opened on a display in this environment. |

## Commands

Every Bash tool call is a fresh shell. Use absolute paths or `cd` in the same
command.

```bash
cd ~/games/gameforge/NostalePacketLogger

# Linux build (MinGW-w64 i686; installed via apt)
make -C linux all test-host
make -C linux check                 # file(1) on the three PE32 outputs
make -C linux host-test             # 385 stdlib unittest cases
make -C linux key-test              # native KEY| key-name parser test
make -C linux speed-test            # native speed-boost/walk-clamp test
PYTHONPATH=linux/host python3 -m ntpl mcp   # MCP stdio server + broker on 47900 (DLL port auto-discovered)
PYTHONPATH=linux/host python3 -m ntpl db build            # local data + public packet repos → SQLite
PYTHONPATH=linux/host python3 -m ntpl db stats            # row counts
PYTHONPATH=linux/host python3 -m ntpl db packets --opcode c_blist
PYTHONPATH=linux/host python3 -m ntpl db nosmall --export ~/bazar-nosmall-all.json --all
PYTHONPATH=linux/host python3 -m ntpl headless --token-file ~/.config/ntpl/token --game-dir <Nostale dir>  # clientless login
make -C linux gui-test              # headless GTK4: map, help standard, picker, multi-client, family, minigame
make -C linux bot-test              # 47 smokes: combat, rules/config, spell order, out-of-range, rejection, HP/MP + finish-kill recovery, stale-target recovery, recovery priority, recovery threshold, loot approach, loot under attack, player follow, follow-attack, follow loot, portal blocking, speed boost control, cast speed control, cast assist, cast-ready pacing, retaliate-only defence, far-goal leg chopping, death/revive dialog, target focus, focus+retaliate, cancel pairing, refusal keep, defend first, start while sitting, route resume, in-range hold/resume, NosBazar monitor/relist/price-change/copy, NosBazar inventory scan/price-all, NosBazar relist-set tracking, NosBazar monitor item set/config import, NosMall import filters, Stats DPS meter
make -C linux clean

# End-to-end without the game (temporary Wine prefix, deletes itself)
./linux/scripts/dev-test-wine.sh 25

# With the game running in its own prefix
export WINEPREFIX="$HOME/.wine-nostale"       # never the Gameforge prefix
linux/scripts/inject.sh
linux/scripts/host.sh status
linux/scripts/host.sh monitor [--engine] [--filter TEXT] [--log FILE]
linux/scripts/host.sh walk 25 118              # client MoveTo; natural walk
linux/scripts/host.sh cast 0 --target-id 5160   # normal attack (u_s 0 3 id)
linux/scripts/host.sh send "rest 1 1 14881134" # sit/stand toggle
linux/scripts/host.sh send "walk 19 146 0 10"  # raw packet
linux/scripts/host.sh scan 34 73               # memory debug: pair
linux/scripts/host.sh find 14881134            # memory debug: charId
linux/scripts/host.sh read 0x07B96FA4 32       # memory debug: dump
linux/scripts/host.sh map info 20 --at 25,117  # map size + walkability
linux/scripts/host.sh map render 20 /tmp/map.png --scale 3
linux/scripts/host.sh map best --log "$WINEPREFIX/drive_c/NosTalePacketLog-<pid>.txt"
linux/scripts/gui.sh                           # GTK4 UI + client picker
linux/scripts/gui.sh --no-client-picker        # direct to --port (MCP broker)
linux/scripts/host.sh gui

# Windows (from Windows, or WSL with the full MSBuild path)
msbuild windows/PacketLogger.sln /p:Configuration=Release /p:Platform=x86
```

The host can be run without installation:

```bash
PYTHONPATH=linux/host python3 -m ntpl status
```

Two discovery/injection safety rules (both live-tested 2026-11-18):

- The injector **refuses** a target that already has a `PacketLogger*.dll`
  module loaded. A second DLL hooks the same recv/send functions again and
  crashed a live client; the module stays mapped after `QUIT`, so restart the
  game client to inject again.
- ⚠️ The injector's refusal only matches the module name. The send hook copies the 6 bytes at the send address into its
  trampoline **without checking them** (`linux/src/dll/packetlogger.cpp` around the `g_send_original` memcpy). A foreign tool
  that hooked send first (a renamed PacketLogger, FishBot, Phoenix Bot's payload) leaves an `E9 rel32` there, which is
  copied, points to the wrong place, and crashes the client on the next send. Not fixed yet. The fix is a prologue `memcmp`
  like `clientmove.cpp`/`clienttarget.cpp`. Analysis: `~/games/gameforge/diag/phoenix-bot/README.md` §8.
- The picker ignores a `C:\ntpl-<pid>.port` whose port has no LISTEN socket
  (`/proc/net/tcp*`). A killed client leaves the file behind, Wine can reuse
  the Windows PID, and the picker would otherwise dial a dead port; it now
  offers **Inject & connect** instead.

## Architecture

- **DLL** (`linux/src/dll/`): resolves three byte signatures in
  `NostaleClientX.exe`, installs two detours (5-byte call replay for recv,
  6-byte trampoline for send), copies packets into two `PacketQueue` rings
  (4096 slots of 2048 bytes each), logs to its own `C:\NosTalePacketLog-<pid>.txt` (capped at 16 MB; a multi-hour session truncates and starts a new file in place), and serves
  `TYPE|PAYLOAD` lines over TCP `127.0.0.1:47821`. A second client (or a stale
  listener left by Wine) pushes each DLL to the next free port from
  `NTPL_PORT`; the port it bound is written to `C:\ntpl-<pid>.port` (Windows
  PID) so the host can find it without probing, which would steal the single
  host slot. `PORTALS|1` makes the send hooks drop the client's own
  `preq` portal request (still logged) so warp tiles cannot change maps.
  `MOVE|x y` calls the
  client's own MoveTo (`0x00576920` on the 2026-11-18 build; `0x565E88` before, packed x/y), so movement is pathfound and
  animated by the game; `POS` reads the local player object (x, y, char id and the rest flag at +0xA9, so a host that attaches while sitting stands up before walking); `TARGET|entityId`
  calls the client's own selector (`0x0057A0E4` on the 2026-11-18 build; controller chain `[0x798294] -> 0x7934D4`), so
  the client highlights the monster and sends its own `ncif`.
- **Injector** (`linux/src/injector/`): expands `VirtualAllocEx` +
  `WriteProcessMemory` + `CreateRemoteThread(LoadLibraryA)`, with a real
  `WaitForSingleObject` / `GetExitCodeThread` check. Enumerates Windows PIDs by
  executable name, so it works under Wine without Linux PID mapping.
  `--list` shows the windows, `--pid <winpid>` injects one, `--all` every one.
- **Host** (`linux/host/ntpl/`): `protocol.py` (parse/encode), `client.py`
  (TCP, retry, reader thread), `clients.py` (client discovery: Wine contexts
  from `/proc`, `Injector.exe --list`, the DLL port files, injection of one
  pick, per-window attach locks), `engine.py` (`WorldState`; parses `mv`, `tp`,
  `rest`, `cond`, `ncif`, `su`, `stat`, `st`, spawns, `inv`/`ivn`/`gold`,
  `rc_blist` and the NPC dialog sequence), `bazar.py` (`BazarConfig`/items,
  `c_blist`/`c_reg` builders, real 15-field `rc_blist` parser, and the
  tick-driven monitor/relist state machine), `nosmall.py` (full
  `*_nosmall.dat` catalog, Item.dat names, variant grouping, Bazar export),
  `database.py` (SQLite schema; client data + protocol wiki + public packet
  repo indexing; `ntpl db` queries), `mapdata.py`
  (`NStcData.NOS` grids, A*, map ranking), `monsternames.py`
  (`NSgtdData.NOS` `monster.dat` vnum → text key joined with the
  `NSlangData_*.NOS` archive and its nibble text codec → `vnum → monster
  name`), `spells.py` (`NSgtdData.NOS` → `Skill.dat` catalog: MP, range, cast
  time, cooldown, target), `spawnlog.py` (entity id → vnum/player name from
  the DLL session log, for spawns that happened before the host connected),
  `noscrypto.py` (login/world cipher ported from NosBazar: packing tables,
  session XOR, first-packet case), `headless.py` (clientless login: NoS0577,
  NsTeST, world handshake, clist/select, keepalive; feeds the same `Engine`),
  `gameforge_auth.py` (current v2 credentials login; identity or
  website-login path, game1.js blackboxes, iovation, thin/codes; terminal
  prompt, no stored credentials),
  `gameforge_browser.py` + `browser_login.mjs` (the gameforge.com sign-in
  form in headless Chrome via Node/CDP: solves the site's PoW challenge,
  returns the bearer and the site-generated blackboxes),
  `captcha_ui.py` (single GTK4 window for Gameforge's image-drop captcha:
  instruction, icon strip, target scene and an icon-number box),
  `botconfig.py`
  (JSON bot configuration: target filters, spell rules, validation and atomic
  save/load), `cli.py` (`walk` waits for the server's `tp`/`mv`; `cast` waits
  for `su`), `gui.py` (the core: window/tab scaffolding, packet table, Bot log,
  status bar, client picker/attach and the drain loop; non-unique windows, one
  per client, per-client attach locks and a client-specific title) with one
  mixin per page: `gui_bazar.py`, `gui_map.py`, `gui_bot.py`, `gui_family.py`,
  `gui_minigame.py`, `gui_stats.py`, and `guicommon.py` (shared constants and
  row models); the bazar tab saves `bazar-<pid>.json`, `mcp_server.py` (MCP
  stdio server + `Broker`: the broker owns the DLL's one host connection and
  fans out `RECV`/`SEND`/`STATUS` to the GUI/CLI on 47900; tools read
  `WorldState` and act through the same commands). Standard library only
  outside the GUI (PyGObject + pycairo for the window).
- **Sit/stand:** `rest 1 1 <charId>` toggles; the server answers
  `rest <type> <charId> <state>` with 0 = standing, 1 = sitting. Sitting
  regenerates HP/MP but blocks movement, so stand before `walk`. The client
  does not echo its own `mv`; wait for the server's `tp` after a walk.
- **Combat:** the server sends `su <attackerType> <attackerId> <targetType>
  <targetId> <skillVnum> ...` for every hit; the engine records the first five
  fields. `ntpl cast <slot> --target-id <id>` makes the client select the
  monster (`TARGET|id`, which sends the client's own `ncif`; a player target
  still uses an injected `ncif`), then sends a default
  `u_s <skillSlot> <targetType> <targetId>` and waits for `su`. The server
  validates the skill target against the client's selection, so an injected
  `ncif` alone does not redirect the attack. The first
  field is a zero-based **skill slot** from `ski`, not the Skill.dat vnum
  (`u_s 0` = normal attack, `u_s 10` → `su ... 876`); later fields are
  unknown. Use `--packet` for a literal
  captured packet when experimenting.
- **Map/bot:** `mapdata.py` reads `NostaleData/NStcData.NOS` (16-byte header,
  count, id/offset index, zlib blocks; `width:int16`, `height:int16`,
  `width*height` terrain bytes; `0x00` walkable). Live 2026-11-07 maps: **98**
  Comet Meadow (50x45), **103** Deep Cave (55x40), **104** (17x15), and the
  earlier **2**/1/20. The Map/Bot tab draws the grid; a left click walks the
  character there along the A* path in short three-cell MoveTo legs (a single
  far MOVE makes the client jump), or with **Draw path** on plans a route:
  the first click sets its starting point (cyan ring, not tied to the
  character), later clicks add waypoints. The drawn preview always keeps the
  whole route and a click never resets it (a route without a start point just
  gains a waypoint). Start plans from the character's current position to the
  nearest remaining leg — an interrupted or manually moved character resumes
  the route instead of walking back to the start point (Stop/Start continues
  where it was; standing on the last waypoint starts the route again). The
  drawn preview in `path_cells` is separate from the execution path planned
  at Start (`_apply_execution_path`). A manual walk (`SEND|walk` to a cell
  other than the last commanded `MOVE`) or a server `tp` to another cell
  while the bot runs sets `external_move`; the next tick clears the pending
  leg, re-plans from the new cell (nearest leg) and continues, so an
  interruption no longer needs Stop/Start or piles up rejected-walk
  timeouts. The own marker follows the DLL's `POS`
  (polled 2 Hz),
  so it tracks the real position instead of the walk packet's destination.
  It computes an
  A* path, and the bot recovers below the HP threshold (sit, wait for full,
  stand), stands (`rest`), picks up own/unowned drops (`get 1 <charId>
  <dropId>`), auto-attacks with Retaliate priority (`TARGET|id` selects the
  monster in the client, then `u_s`; skill 0 = normal attack), chases a
  target that is outside attack range (select, walk one leg, attack once in
  range) and walks in three-cell legs through the client's own MoveTo
  (`MOVE|x y`), handing the next leg off one cell early so walking is
  continuous. A killed monster gets no `out`, so a TARGET refusal drops the
  entity from the world state and the bot picks a live one.
  Combat takes priority over the path: the bot clears all attackable
  monsters in range before taking the next leg; with no path it fights
  stationary and chases an out-of-range target on foot.
  **Targets** filters by monster template vnum (all / only selected / all
  except selected); **Spells** uses the scanned Skill.dat catalog and ordered
  rules (Always / my HP% / MP%; per-rule cooldown; monster or self target;
  MP/range/cast-time checks) before falling back to the Skill field. Always +
  cooldowns is a rotation; rule order is explicit (1., 2., up/down). The whole configuration is JSON
  save/load (see [docs/bot-config.md](docs/bot-config.md)).
  With no path the bot runs stationary combat/pickup/recovery. Map changes come from
  `c_map <type> <mapId> <state>` (`mapId` is the **second** int) plus `at`;
  the GUI switches immediately and clears stale entities/drops. When the host
  attaches after login and saw no map id, `choose_map_for_positions` ranks all
  779 grids against **every** live entity (players, monsters, NPCs), picks the
  tightest perfect fit, and keeps the current map only while it is tied for
  best; a server map id with no grid in the archive is reported instead of
  showing another map (do not "improve" this back to players-only — the
  NosVille Meadows map 2 is inseparable from 300/5613 with two points).
  A GUI attached after login now **seeds `map_id`/position/char id from the
  DLL log's last `at` and own walks** (`spawnlog.load_last_position`) before
  the position ranking runs, so the guess is only a last resort.
  **Portals are server-sent:** on map entry the server sends the map's whole
  portal list as `gp <x> <y> <toMap> <unknown> <index> <unknown>`
  (`WorldState.gates`; live maps 2026-11-07: 103 → (20,20)→104 and
  (42,32)→3; map 3 → (10,208)→6, (19,7)→7, (45,12)→103, (156,2)→4,
  (178,189)→2). The same entry sends the map's Time-Space list
  (`wp <x> <y> <tsId> <flag> <minLv> <maxLv>`) into
  `WorldState.timespaces`, drawn with the stone icon; live map 4 has all
  eight Time-Spaces (TS 16–22, 704). Crossing learning is the fallback: a
  leaving `c_map`/`mapout` remembers the cell held, and the following `at`
  gives the destination and arrival cell, recording both sides
  (`WorldState.portals`).
  `spawnlog.load_gates`/`load_portals` backfill from the DLL log;
  `set_gate` lets an exact gate replace a nearby crossing guess,
  `normalize_portals` averages leftovers, and the GUI persists to
  `~/.config/ntpl/portals.json`, drawing magenta rings with a "Portal → map
  N" tooltip after the own marker so a portal under the character stays
  visible.
  **Combat values come from the equipped SP card, not constants:** `u_s 0` is
  answered by `su ... <vnum> ...` (260 = Energy Bolt on the live mage), and
  the Skill.dat record for that vnum gives range (6), cast time (0.8 s),
  cooldown (0.5 s) and MP (0); the bot caps the Search radius by the skill
  range and paces actions by `max(Min delay, cast, cooldown)` (Min delay defaults to 0). `ski` is only
  sent at login, so slot→vnum mappings are also learned from paired
  `u_s <slot>` / `su ... <vnum> ...` (live in `WorldState.skill_learned`,
  backfilled from the DLL log by `spawnlog.load_skill_slots`); without a
  mapping a spell rule is skipped with a status message instead of sending a
  vnum as a slot. The own marker is a bigger dot + a facing arrow from the
  last `walk` delta (the protocol has no own-facing packet).
- **Docs**: `docs/autonomous-play.md` (the observe→decide→act→survive
  playbook: resync, entity seeding, target/HP policy, handover, worked live
  session), `docs/handoff-2026-11-19.md` (previous session: multi-client,
  follow, focus, recovery priority, route resume, log-freeze fix),
  `docs/architecture.md`, `docs/engine.md`, `docs/map.md`,
  `docs/bot-config.md`, `docs/bazar.md`, `docs/monitoring-config.md`, `docs/family.md`, `docs/database.md`,
  `docs/context.md`, `docs/protocol/bazaar.md`,
  `docs/live-session-2026-11-06.md`,
  `docs/feature-review-2026-11-06.md`, `docs/hooking.md`, `docs/ipc.md`,
  `docs/linux.md`, `docs/windows.md`, `docs/development.md`,
  `docs/roadmap.md`, `docs/troubleshooting.md`; packet wiki in
  `docs/protocol/`; **upstream reuse** in `docs/reusable-projects.md`.

## Rules

- **32-bit only.** The game is a 32-bit Delphi application. The DLL, the
  injector and the stubs are i386. Do not build x64.
- **Patterns are client-build-specific.** `NOT-FOUND` is a non-fatal state, not
  a crash. Re-derive with `docs/hooking.md`; never guess-patch addresses.
- **Nothing in a hook may allocate, log, lock for long, or call the socket.**
  The only allowed operation is `PacketQueue::push`. Drop and count on
  overflow; never stall the game thread.
- **MinGW headers on Linux are lowercase**: `<windows.h>`, `<psapi.h>`,
  `<tlhelp32.h>`, `<winsock2.h>`, `<ws2tcpip.h>`. Capitalised includes fail.
- **The DLL uses no libwinpthread.** Keep using `CRITICAL_SECTION` / the
  atomic queue, not `std::mutex` or `std::thread`.
- **Wine prefix:** the game runs under the Gameforge prefix
  (`~/games/gameforge/prefix`, its Wine 11). Inject only into
  `NostaleClientX.exe` (the window is `NosTale - <pid>`), never into
  `gfclient.exe`/`WebHelper.exe`, and never into `~/.wine`
  (WarCraft III/Battle.net). Do not kill a client the user is using.
- **Delphi string ownership:** `send_packet` frees its buffer after the call,
  mirroring the original. If crashes appear right after a manual send,
  investigate this first (`docs/hooking.md`).
- **Movement is client-side.** `MOVE` calls the client's MoveTo (`0x00576920` on the 2026-11-18 build; re-derive from the prologue signature after a patch)
  from the DLL worker and `POS` reads the local player object (including the sit flag); both addresses
  are client-build-specific and prologue-validated. Do not "fix" a walk by
  injecting more `walk` packets: the server moves, the client teleports.
- **Git:** commit from this directory only. The home directory has a stray
  `~/.git`; never commit there and never run `gc`/`clean` on it.
- **Privacy:** never read, copy or write credentials, session data, or the
  contents of `usercache-*` / `webcache-*`.
- **GUI text:** explanations never sit in the layout. Every section, frame,
  tab and dialog gets a compact **?** button whose popover says what it does
  and how it works — use `_help_button()` and `_frame(..., help_text=...)` in
  `gui.py`. Put a page's UI in its mixin (`gui_<page>.py`), not in the core.
  Keep live status values and short empty states; keep control
  tooltips to one line. `gui_smoke.py` enforces this, so update the help text
  with every new feature instead of adding a paragraph.
- **Terms of service:** Gameforge prohibits automation. The owner accepts the
  account risk; do not add detection-evasion features.

## Achieved / next

First packet-driven game actions are confirmed on the live client:

1. The injector and both hooks work in the real client.
2. Sit/stand is controlled by `rest`.
3. The client's own MoveTo (`MOVE|x y`) walks and animates the character; it
   sends its own `walk` packets. `POS` reads its local position.
4. The client's own selector (`TARGET|id`) selects the monster and sends its
   own `ncif`; live 2026-11-07 the server then answered `u_s 0 3 8684` with
   `su ... 3 8684 ...` instead of the previously clicked id.
5. The engine parses the resulting `tp`/`rest`/`cond`/`stat` stream.
6. The `NStcData.NOS` map parser identified map **20** (230x260); the Map/Bot
   tab renders it, draws 8-direction A* paths and runs the loop/auto-attack.
   The bot smokes send `ncif`/`u_s`/`MOVE` and pass; `TARGET` is verified
   live (see 4).

Next:

1. Fully done: live path loop, auto-attack, pickup, map switching and the
   skill hook; normal attack is `u_s 0 3 <id>` (the server reports skill
   866/868 in `su`; 260/855 after the SP card changes).
2. **Agentic play:** the MCP server is live-ready (`ntpl mcp`); next is a
   live agent session against the game. The **Time-Space stone icon** from
   nosapki is integrated (`assets/ts-1.png`); the same source
   (`nosapki.com/images/nt-images/`, `images/map_previews/<map>.png`) can
   supply monster/map icons next.
3. ~~Capture the client's own `walk`/`u_s` SEND packets~~ — done: the send
   hook sees `walk`, and the skill hook captured `u_s` at `0x72D5B2` on the 2026-11-06 build; on the 2026-11-18 build it is `NOT-FOUND` and the send hook covers `u_s`.
4. Map switching is live-verified for the `c_map` 20↔85 portal capture; more
   maps will confirm the parser and 8-direction A* on other grids.
5. Self-target shape (`u_s <slot> 1 <charId>`) is live-captured
   (`u_s 7 1 14881134` → `su ... 873`); no remaining protocol guess.
6. Unknown-vnum monsters that existed before DLL injection remain the main
   target-filter gap; the UI has an explicit option and docs describe relog.
7. Reconcile `docs/protocol/` with more live captures, then extend the loop
   with path validation and combat state.
8. **Reuse the upstream MIT projects** cloned in `~/games/gameforge/`
   (`nosbazar`, `GflessClient`, `NosTale-Gfless`, `NosTale-Auth`,
   `nostale-auth`): see `docs/reusable-projects.md`. Two paths: (a) launch the
   real client without the Gameforge launcher by emulating the
   `GameforgeClientJSONRPC` pipe and `NoS0577` auth; (b) clientless login with
   NosBazar's `noscrypto`/session code, which also delivers the login `ski`
   skill list (the current `slot unknown` cause) and complete entity
   lifecycles.
9. The learned skill list is only sent in `ski` at login; the log is
   truncated on every DLL reinjection, so the spell picker says `slot unknown`
   until a relog. Persist `ski` (sidecar file / append log) or receive it from
   a clientless login.
10. **Headless client:** `ntpl headless` does the clientless login (cipher,
    NoS0577/NsTeST/world handshake) and is live-validated through `failc 1`
    for a bad token. The v2 login and the website-login iovation step are
    live (201/200); `thin/codes` still refuses browser-generated devices
    (`Not allowed to create code`). The official authorization-ticket
    endpoint is `POST /api/v1/auth/codes` (`clientId` + `redirectUri`); it
    issues a generic code, but account-scoped codes are 403 and thin/codes
    rejects the generic one. A captured identity or launcher token, the
    real-token world/character run, and a headless broker so the
    GUI/MCP bots can drive a clientless session remain. See
    [docs/headless.md](docs/headless.md).
