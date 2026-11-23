# Troubleshooting

## Connection

| Symptom | Likely cause | Fix |
|---|---|---|
| `ntpl: cannot connect to 127.0.0.1:47821 after N attempt(s)` | DLL not injected, game not running, wrong prefix, or wrong port | `scripts/inject.sh`; confirm `pgrep -af NostaleClientX`; make sure `NTPL_PORT` is the same in the game's environment and the host |
| Connects, then immediately disconnects | another host is connected (one at a time) | close the GUI/monitor before using `ntpl send`; or use the monitor's `/send` |
| Port already in use | a previous DLL instance, or another process | `ss -ltnp \| grep 47821`; the DLL now moves to the next free port and writes `C:\ntpl-<pid>.port`, so the GUI attaches there; set `NTPL_PORT` for both sides to pick a base |
| Connects to 47821 but no status line ever arrives | a stale listener left by a previous client (Wine keeps the socket open); the real DLL is on another port | use the GUI's **Client…** picker, or read `<prefix>/drive_c/ntpl-<winpid>.port`; do not probe ports (a probe steals the single host slot) |
| Several clients, GUI on the wrong one | the picker was bypassed, or the windows look identical | open the picker; listing numbers every game window in its title bar (`NosTale [#2]`) and shows the same number in its row. Then press **Client…**; the list is built from `Injector.exe --list` and the DLLs' port files |
| `ntpl status` times out | connected but the DLL is stuck before its status line | check the DLL log; re-inject |
| The Bot log stops growing and the bot stops reacting | an old GUI crashed in the 4,000-line trim (PyGObject returns `(found, iter)` from `get_iter_at_line`) | update the host and restart the GUI; `make gui-test` covers the trim path |
| `attack cancelled; retrying` appears after every hit | the `cancel` that ends a successful cast was counted as a refusal | update the host; the message now only appears when no `su` from the character arrived since the attack |

## Hooks

| Symptom | Likely cause | Fix |
|---|---|---|
| `recv=0x00000000 ... NOT-FOUND` | signature does not match this client build | re-derive the pattern, see `hooking.md` |
| `hooks recv=FAILED` | signature found, `VirtualProtect`/patch failed | the page may be protected differently; check the DLL log, try again after the client is fully loaded |
| Hooks `ACTIVE` but no packets | filter too narrow, or the game is idle | clear the filter; walk/say something in game |
| `send failed: hooks are not active` | send hook missing or the DLL was disabled | check `ntpl status`; re-inject |
| Game crashes on disconnect | unhook race with an in-flight packet | disconnect while idle; report if repeatable |

## Movement and combat

| Symptom | Likely cause | Fix |
|---|---|---|
| `MOVE` sent, but the character does not move | character is sitting, or the target is blocked | `POS` reports the rest flag (1 sitting) and the DLL answers `MOVE: character is sitting (stand up first)`; the GUI now trusts that answer, cancels the refused leg and stands at once. For the CLI send `rest 1 1 <charId>` once; then try another adjacent tile |
| Character teleports instead of walking | a raw `walk` packet was injected; the client did not animate it | use `MOVE` / `ntpl walk`, which calls the client's own MoveTo |
| `MOVE` returns `client build mismatch` | the client build moved the MoveTo function | re-derive the address (see `docs/protocol/movement.md`); raw packets still work |
| `POS` returns "not readable" | the client build moved the player object | re-derive the player global (2026-11-18: `0x007954B4`, chain `[[0x007954B4]]`) and the +4/+6/+0x24 layout |
| `cast` sent, but no `su` | character sitting, target out of range, or the `u_s` field order is wrong | stand, move into range, and use `--packet "u_s …"` with a layout captured from the live client |
| `rest` toggles twice | two identical packets were sent | send `rest 1 1 <charId>` once per state change |
| Client looks stuck after many injected walks | injected walks can desync the client | use `MOVE`; if already desynced, relog/restart the client |
| Spell vnum unknown | the client's hotbar mapping is not documented | cast once manually and read `su 1 <charId> ...` from the log |
| `TARGET <id> ERR entity not found` | the server/engine knows the monster, but the client has no live object for it (despawned, left the interest area, or the id was reused) | the bot drops that entity from the world state (the server sends no `out` for kills), blocks the id for 5 s and picks the nearest live monster; after 3 such failures in 10 s it pauses combat for 5 s instead of hopping through ghost ids. Check the **Bot log** tab. If every monster fails, the client build moved the entity layout |
| `TARGET <id> ERR client refused the selection` | the object is dead, off-map, or not targetable (type `0xA9 == 4`) | expected for a stale target; the bot skips it for 5 s. See the **Bot log** tab for the exact reply |
| Bot attacks after death / nothing is nearby | the character is dead (`stat` HP 0) or the target is a client-only ghost the server refuses | the bot idles while `dead` and resumes on `revive`. A refused cast no longer drops or switches a target that lands hits; repeated refusals pause combat (the status says so). A target that never lands a single hit (a client-only ghost) is dropped after 3 straight refusals, and a `TARGET` refusal (the client cannot select the monster) drops it too. If it still happens, re-inject the current host and DLL |
| Bot fires at monsters across a town wall / in town | attacks used to be range-only | updated: combat requires line of sight over walkable cells, and repeated `cancel` refusals pause combat (30 s or after moving 5 cells) instead of hopping targets |
| The Bot log spams `HP … recovering` / `attacked while recovering` | old recovery-abort loop (fixed) | update the host and restart the GUI |
| Character sits and stands over and over and never fills up | an old host resent `rest` every 2–2.5 s; `rest` is a **toggle**, so a slow confirmation (common with several clients) stood the character back up | update the host; sit/stand requests are now debounced to 5 s (`REST_RETRY_S`). If the character is being hit, standing to fight back is intended |
| Recovery does not start while a monster is nearby | the bot finishes the current kill first; with strict focus a refused/ghost fight used to block it | the current host heals at once when combat is paused by refusals, and a never-hit ghost is dropped after 3 refusals |
| Bot keeps fighting at ~20 % HP instead of sitting | the required priority is Retaliate -> finish the current kill -> recover; while a monster keeps hitting, the bot fights it | that is the intended order. Once the attacker is dead (or no longer hitting for 5 s) and the current target is finished, the bot sits. While recovery is pending it no longer walks the route or picks new monsters |
| Recovery only starts after Stop/Start | an old host let a stale `fight_target_id` (a kill whose `st` was never seen) block "finish the kill first" forever | update the host; the deferral only applies while the bot has attacked that target within the last 12 s, then recovery proceeds. Combat/Retaliate still win when you are being hit |
| Attacks land on the previously clicked monster | the injected `ncif` cannot change the client's own selection | re-inject the current DLL (0.2.x) and use `TARGET\|id`; check the Bot log for `monster <id> selected ...` |
| Bot walked onto a portal and changed map | **Block portals** is off, or the injected DLL predates the `preq` drop | turn the toggle on; `host.sh status` must show `portalblock=BLOCKED` after the GUI connects. If `PORTALS\|1` answers `unknown command`, reinject the current DLL. A dropped attempt still appears as `SEND\|preq` in the packet stream/log |
| `PORTALS|1` answers `unknown command` | old DLL (before 0.2.3) | reinject the current `PacketLogger.dll` (new filename or client restart after `QUIT`) |
| Speed + keeps falling back to 0, or the GUI says the client DLL is older than 0.2.6 | the game client still runs an older DLL (host-only update) | restart the game client, not just the GUI; 0.2.6 applies the value immediately, lifts the 59 cap and clamps the walk field. Cast speed is host-side and needs no DLL update |
| Speed + above 59 does nothing and the status says `cannot go above 59` | this client build's speed setter/table did not match the signature, so the DLL left the client untouched | restart the game client to load the current DLL; if the build changed again, re-derive the signature before using high values |
| Speed + was corrected before the clamp build | the server rejected a walk field above its `cond` | current builds clamp the walk field to `cond`; live 2026-11-20 at base 11: +3 applied (actor 14), walks 11, no correction. If it still corrects, lower the value |
| Other players/server see normal speed while my client looks faster | by design | the server is told the `cond` speed; the extra speed is client-side only |
| Cast speed does not make a single spell land sooner | the server applies each spell after its own cast time; no client hack can shorten that | Cast speed repeats the skill you press at the server's fastest rate and overlaps casts. One spell still takes its own cast time |
| Cast speed ON but my character still waits between casts | each spell lands after the server's own cast time, and a same skill cannot exceed its cooldown | without the bot, press a skill **once**: the assist repeats it on every `sr` cooldown-end packet; press another skill to switch. With the bot running, its spells overlap automatically. A `cancel 0 <castSlot> -1` is a same-skill cooldown refusal |
| `CAST\|1` answers `unknown command` | the injected DLL is older than 0.2.8 | harmless: Cast speed is host-side now and the GUI only sends `CAST\|0` to undo the old zeroing; an old DLL can ignore it |
| Skill cooldowns disappeared from the skill bar | an earlier 0.2.8 build zeroed them | reopen the GUI: it sends `CAST\|0` on attach and the server cooldowns come back; no game restart needed |
| `Injector` says a logger is already loaded after `QUIT` | the old module stays mapped, and the guard refuses a second injection | reload with a new DLL filename and `Injector.exe --pid <pid> --dll <name>.dll --force` (only after that logger's QUIT and its `C:\ntpl-<pid>.port` file has disappeared; `--force` refuses while the port file exists), or restart the game client |
| Client crashes after injecting a DLL | a second live logger was injected (two send/recv hooks) | restart the game client; never `--force` while the old logger is still listening. The injector now refuses if the old port file exists |
| Bot cannot reach a target near a map edge | the only route crosses a warp tile and blocking is on | that is the protection working; walk there manually with blocking off, or fight from range |

## Minigame

| Symptom | Likely cause | Fix |
|---|---|---|
| **Start** turns itself off, status says this DLL has no `KEY` command | the game client still runs a DLL older than 0.3.0; updating the build or restarting only the GUI does not replace the mapped module | restart the game client, inject again with the picker/injector, and check the window title/status shows `DLL 0.3.0`. The `KEY|` command needs 0.3.0 |
| Keys are sent but the game does nothing | the game window is not focused and the client ignores posted keys, or the key is not bound | click the game window once (the DLL then uses real `SendInput`), check the Bot log for `minigame keyboard ready`; on a QWERTZ layout the secondary attack is `z`, not `y` |
| `y` does nothing but `z` works (or the reverse) | QWERTY vs QWERTZ keyboard layout | put the other letter in **Key**. The tab accepts `y` and `z` |
| `Focus refused for player N` | the client would not select that player (dead, out of the client's targetable range, or not targetable) | the tab skips that player for 5 s and tries the next one; the key loop keeps running. Move closer or let the raid respawn them |
| No player is focused although players are nearby | those entities are pets/NPCs (kind 2) or monsters/sheep (kind 3); the tab deliberately ignores them | that is the player-only rule; check the map legend and the **Focus nearby players** radius |
| Focus switches constantly | the selected player keeps leaving the radius, or the client refuses them | lower/raise **within (tiles)**, or turn focus off and keep the key spam |
| No keys after Stop | intended | Stop removes the timer; a press already queued to the DLL may still complete within ~25 ms |
| Bot log has no minigame lines | the loop was never started, or the tab is attached to another client window | minigame settings and the loop are per attached game window; check the title/PID and press Start on the correct window |

## NosBazar monitor views

| Symptom | Likely cause | Fix |
|---|---|---|
| **NPC resale** is empty although the item is configured | the item's Cost type is NosMall ND or No cost, so it belongs to the NosMall view | select the item in **Items**, set **Cost** to **NPC gold** and save. For Pet Food (vnum 2077) or Bell of Sweet Home (vnum 2072) leave the Cost at 0; the Item.dat price fills in |
| An NPC item appears under **NosMall** | a hand-edited config has both `nos_cost` and `npc_cost`, and NosMall wins | set the Cost type to NPC gold in the editor (it clears the ND cost), or keep a separate NosMall entry for the ND variant |
| **Markup** is below 1.0x | the cheapest bazaar listing is below the NPC purchase price | no resale profit; the row still shows the listing so you can compare |
| The NPC price did not fill in | the Item vnum is blank with several search vnums, or item names are still loading / missing from the DB | set **Item vnum**, wait for the names to load, run `PYTHONPATH=linux/host python3 -m ntpl db build`, or type the price by hand |
| One Start does not search only the visible tab | by design: one pass searches both lists and each tab shows its own results | use **Monitor Stop/Start** to restart both; use the per-tab **Only priced** filter to hide unanswered rows |

## UI

| Symptom | Likely cause | Fix |
|---|---|---|
| `gui.sh` says GTK4/PyGObject is missing | bindings not installed | `sudo apt install python3-gi gir1.2-gtk-4.0` |
| Window says "not connected" | DLL not injected, game not running, or wrong port | inject first; check `NTPL_PORT` on both sides |
| Need to know what the bot did | the status bar only shows the latest action | open the top-level **Bot log** tab (or `tail -f ~/.config/ntpl/bot-<winpid>.log` for the attached client; `bot.log` is used before attaching; `NTPL_BOT_LOG` overrides) -- every action, TARGET reply and DLL error is kept with a timestamp (Clear log empties the tab, not the file) |
| GUI opens but no packets | filter too narrow, or another host is connected | clear the filter; the DLL accepts one host at a time |
| GUI takes minutes to attach or says “Not Responding” with a long-running client | an old host scanned the whole per-client DLL log several times on the GTK main thread (the file reached 96 MB after hours on a busy map) | update the host (backfill reads the last 8 MB; the skill list reads the first 8 MB) and the DLL (caps its log at 16 MB). Re-inject after a client restart to get the cap |
| Follow-mode bots ignore drops on the ground | an old host skipped the loot branch entirely while following | update the host; with the **Pickup** toggle on, followers collect own/free drops within 10 cells of the followed player and then resume following |
| Follow says `following: X is not in sight` every few seconds | the followed player is not in this client's world: another map, out of the interest area, or they spawned before the DLL was injected (so the client never sent an `in` for them) | make sure both characters are on the same map and move the followed character once (a live `in`/`mv` arrives). The GUI also seeds players from the client's own log (`in`/`mv`), so a player who spawned before the GUI attached is found. The warning is throttled to once per 5 s |
| Bot does not attack in "Only selected" mode | Auto-attack is off; recovery is sitting below the HP threshold; the monster vnum is not selected; or a pre-injection monster has no vnum | check the green/red Auto-attack toggle and the red RECOVERING banner; wait for recovery or turn it off; verify the target list; enable **Also attack monsters with unknown vnum** or let the monster respawn |
| Map does not switch after a portal | old build read the first `c_map` field (type 0) as the map id | update/restart the GUI; the engine reads the second field and clears stale entities |
| Wrong map right after attaching to a running game | the GUI now seeds the map from the DLL log's last `at`, but a log with no `at` falls back to positions, where monsters on blocked cells and same-layout maps make the guess ambiguous (marked `· auto`) | the current build fixes the common case at startup; walk once or cross any portal to get the authoritative `c_map`/`at`. Crossings are remembered as portals |
| Tooltip shows `Monster #id` with no name | the entity spawned before the DLL/GUI and has no `in` vnum | `spawnlog.py` covers pre-GUI spawns; for pre-injection spawns enable the unknown-vnum option or re-enter the area so `in` is sent again |
| Toggles are grey, not green/red | an old GUI process predates the CSS classes, or CSS was installed after the widgets | restart `gui.sh` with the current build |
| Path is still 90° / staircase | the path was built by the old 4-direction A* | restart the GUI and click the waypoints again; paths are rebuilt on click |
| Start walks back to where the route began | an old host planned the route from its stored start point | update the host; Start now joins the route at the nearest leg from the character's position, so Stop/Start (or a manual walk) resumes where it was instead of returning to the start |
| Clicking the map wiped the drawn route | an old host cleared the waypoints when the route had no start point | update the host; a click on a route with no start point adds a waypoint instead of resetting it |
| Bot walks away mid-fight | an old host resumed the drawn path between attacks | update the host; while the engaged monster is in skill range and line the bot keeps fighting in place (no path leg, chase or loot walk). If it steps out of range/cover the path resumes — that is not a bug |
| Bot stands frozen next to a monster it cannot hit | an old host held position through refusal pauses / out of range | update the host; the fixed hold only applies in range with line; otherwise the path/chase moves. The refusal pause also ends at once if a resolvable monster attacks you |
| The bot stops or freezes after you walk the character manually, needing Stop/Start | an old host waited out the interrupted leg and counted it as rejected (three stops the bot) | update the host; a server `tp` to a cell the bot did not command re-plans from the new position and continues the route automatically. Three genuinely rejected legs (the server never answers) still stop it with a status |
| Add spell… picker is empty | `NSgtdData.NOS` is missing or `NTPL_NSGTD` points elsewhere | set `NTPL_NSGTD` / `NTPL_NSTC`; manual vnums still work |
| NosBazar **Inventory** section says `Inventory not received`, `DLL log truncated before the inventory`, or `partial (relog for all)` | the server sends the full `inv` dump only at login; the GUI attached later and the DLL rewrote its 16 MB log in place, dropping the dump (some later `ivn` updates may still be shown, hence `partial`) | relog or change channel **with the GUI window open**; an open scan fills automatically. The complete inventory is then cached per character (`~/.config/ntpl/inventory-<char_id>.json`) and later attaches load that snapshot even after another truncation |
| Inventory rows show `item 1092` instead of names, or no NPC price | the name lookup has not produced a row for that vnum: the local DB is missing/empty, `NSgtdData.NOS` is unreadable, or the item is newer than the client data | the Inventory section loads names from the local DB first (then `Item.dat`) automatically when it opens. If a row still shows `item N`, run `PYTHONPATH=linux/host python3 -m ntpl db build` or set `NTPL_NSGTD`; search packets and adding still work without names |
| The Monitor, Relist or Items list shows my configured shortcut (e.g. `bubbl`) instead of the item name | no vnum of that entry resolved in the local DB or `Item.dat` (or the lookup was still loading) | names load when the NosBazar page opens and the lists update in place; hover the item name to see which shortcut and search vnums it is. If it persists, run `PYTHONPATH=linux/host python3 -m ntpl db build`, then click **Configuration → Reload names** (an empty lookup also retries after switching away and back or reopening Inventory): no GUI restart needed. The editor's **Name** field is only the shortcut; the Monitor uses the real name whenever a vnum resolves |
| **Price all** adds many searches and the pass is slow | every distinct inventory template is one `c_blist`, paced at 2.5 s because the server drops faster searches | expected; a 100-item inventory takes about four minutes per pass. Add only the items you care about by ticking them in **Inventory** |
| Relist item says `not relisting` (or `needs floor` / `needs vnum/tab`) and never sends `c_reg` | the row's **Relist** tick is off, or no search packet / positive Item vnum / Tab / positive Min price | open **Relist**, tick the row (or use **Add from Items** / **Add from Inventory**), select it, set Vnum/Tab/Amount and a positive Min price, then **Save relist settings**; the badge changes to `ready` |
| Relist/Monitor Start opened another NPC dialog (e.g. Carve Runes) | the learned Bibi entity id belongs to another map; ids are per entity instance | walk next to Bibi Basar on the current map and press Start again (the GUI now refuses an id the map knows as a non-Bibi entity and waits instead) |
| Monitor says `could not open NosBazar (NPC …)` although the bazaar is open in the game | the window was opened before the GUI attached, so no new `wopen 32` is sent | current builds accept the verified Bibi dialog (reply 460) plus the listing answer and continue; if the reply dialog is another id, the id is another NPC — walk to Bibi |
| Relist item says `bazaar window not open` | the NosBazar window is not open (a search answer alone does not open it; the server ignores `c_reg` without `wopen 32`) | press **Relist Start** again; with a Bibi nearby the controller opens her, without one it uses the NosBazar medal (vnum 1955) from the inventory, and both wait for the window |
| Start cannot find Bibi Basar (no NPC nearby) | no Bibi entity on this map and no NosBazar medal in the Main/Etc bag | on FrosTale the client's own shortcut (`c_skill`, learned from one manual bazaar opening) opens it with no NPC and no medal; otherwise keep a **NosBazar medal** (vnum 1955, the item shown as NosBazar) in the inventory, or walk to a Bibi |
| GUI says `not connected … 47823` but the DLL's port file has another port | a reused Windows PID left a stale `C:\ntpl-<pid>.port`; the GUI read it before the new DLL rewrote it | it re-reads the port file on every failed connect and retries there by itself; use **Client…** to re-scan if it persists |
| Relist item says `register ignored (no rc_reg)` | the server silently dropped the `c_reg`: the bazaar window was not open, the slot or item constants no longer match the live inventory, or (before 0.7.2) the packet lacked the client's trailing `0` field | check the window first (`wopen 32`); then press **Relist Start** again — it repairs `unk1 = 8 + INDEX[0]` for Main/Etc items (`unk2` stays as configured) and sends the client's exact packet shape; verify the listing in the bazaar |
| Relist Start does nothing or says the character is missing | Character empty, live-relist gate off, item not in that tab/quantity, or the cheapest listing is yours/below the floor | fill the exact character name, tick **Allow live re-listing**, check the result status for `own`, `floor`, `no-slot` or `quantity` |
| A bound/limited inventory item cannot be relisted | its slot carries a negative vnum; `find_inventory` only matches positive templates | monitor/search by the absolute vnum; relisting a bound item is intentionally skipped |
| Equipment row shows `qty 0` | the server reports quantity 0 for occupied equipment rows (not a stack) | expected; the scan counts it as one item and relist uses amount 1 |
| Spell rule never casts | not enough MP, target out of the spell's own range, rule/spell cooldown still running, or a higher rule is always winning | check the rule's description metadata; wait, move closer, raise MP, or reorder/disable the higher rule |
| Rule order is not what I wanted | the list is evaluated 1., 2., … from the top | use the ↑/↓ buttons; the number on the left shows execution priority |
| Character swings at empty space / “attacks on blind” | Search radius selected a monster outside the skill's real range; the client animates but the server rejects or self-targets | the bot caps basic attacks at the equipped SP card's Skill.dat range (Energy Bolt 260 = 6, Holy Attack 866 = 11) shown in the Combat box; with no drawn path it walks to an out-of-range target before attacking and the status shows “chasing monster …” |

## Injection

| Symptom | Likely cause | Fix |
|---|---|---|
| `No NosTale client found` | game not running, different prefix, or executable name changed | check `--list`; confirm `WINEPREFIX` |
| Picker offers **Connect** but the host gets connection refused | stale `C:\ntpl-<pid>.port` from an earlier session with the same Wine PID | the current host ignores ports with no LISTEN socket; on an older build, delete `<prefix>/drive_c/ntpl-*.port` and pick the client again |
| Injection fails with “a packet logger is already loaded” | a `PacketLogger*.dll` is already mapped (a second injection would hook the same functions twice and crash the client) | restart the game client; the module cannot be unloaded, and `QUIT` only removes its hooks |
| `LoadLibraryA returned NULL` | wrong DLL path, 32/64-bit mismatch, missing dependency | pass an absolute DLL path; confirm `file` says PE32 i386; check the client's loader error |
| `OpenProcess: Access is denied` | injector not elevated (Windows) or wrong prefix (Wine) | run as administrator on Windows; use the game's prefix under Wine |
| Injected but no TCP server | bind failed (port in use) or the DLL exited early | read `<prefix>/drive_c/NosTalePacketLog-<pid>.txt` (the client's Windows PID); change `NTPL_PORT` |

## Logs

| Symptom | Likely cause | Fix |
|---|---|---|
| No `C:\NosTalePacketLog-<pid>.txt` | `C:\` not writable | the DLL falls back to `%LOCALAPPDATA%\NosTalePacketLogger\dll.log`, then `%TEMP%`; set `NTPL_LOG` explicitly |
| Log is empty | `NTPL_QUIET=1`, or hooks never installed | unset `NTPL_QUIET`; check `ntpl status` |
| Log has packets but the host does not | host filter, or the host attached later | clear the filter; the host only sees packets after it connects (the file log has everything) |

## Build

| Symptom | Likely cause | Fix |
|---|---|---|
| `Windows.h: No such file or directory` | Linux filesystem is case-sensitive; MinGW headers are lowercase | include `<windows.h>`, `<psapi.h>`, `<tlhelp32.h>`, `<winsock2.h>`, `<ws2tcpip.h>` |
| `i686-w64-mingw32-g++: command not found` | MinGW not installed | `sudo apt install g++-mingw-w64-i686` |
| Host `g++` picked up instead of MinGW | `CXX` was overridden by make's built-in | the Makefile uses `MINGW_CXX`; do not force `CXX` |
| MSVC `C2001: newline in constant` | LF-only C/C++ file | convert to CRLF (`docs/windows.md`) |
| `GetExitCodeThread` returns 259 | the remote thread is still running | the injector waits 10 s; increase if the client is slow to load |

## GUI

| Symptom | Likely cause | Fix |
|---|---|---|
| `ntpl: the GTK4 GUI needs PyGObject and GTK4` | missing bindings | `sudo apt install python3-gi gir1.2-gtk-4.0` |
| `Namespace Gtk not available for version 4.0` | GTK3-only system | install GTK4; or use `ntpl monitor` |
| Window opens but no packets | not connected (status bar) | inject the DLL; check the port |
| GTK warnings about `Gdk` | a different GTK version was loaded first | the GUI pins `Gtk 4.0` and `Gdk 4.0`; do not import GTK3 in the same process |

## Wine

| Symptom | Likely cause | Fix |
|---|---|---|
| `wine: could not load kernel32.dll` | prefix was created by a different Wine version | recreate the prefix with the Wine you intend to use, or reuse the same runner |
| `wineserver: could not save registry branch` during tests | the prefix was deleted while wineserver was still shutting down | harmless; the test waits for `wineserver -w` before cleanup |
| Game launches but injection does nothing | a different prefix is active | export `WINEPREFIX` for both the game and `inject.sh` |
| `NostaleClientX.exe` not visible to the injector | the game is a launcher that spawns a differently named process | add the actual executable name to `kTargets` in `linux/src/injector/processes.cpp` |

## Python

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: ntpl` | not on `PYTHONPATH` | `PYTHONPATH=linux/host python3 -m ntpl ...` or install with `pip install -e linux/host` |
| `ConnectionLost: peer sent more than 64 KiB without a newline` | broken host or wrong port | check what is listening on the port |
| `ProtocolError: unknown message type` | a host sent a non-protocol line | the DLL ignores unknown commands with `STATUS|unknown command: ...`; check the client |
