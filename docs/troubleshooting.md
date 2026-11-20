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
| Cast speed ON but manual casting is unchanged | the switch paces the **bot**, not key presses; the server's own cast time cannot be shortened | press **Start** with your spell rules/Auto-attack set. Manual casts are refused if sent before the server is ready |
| Cast speed does not make spells land faster | the server enforces each skill's own cast time and cooldown | with Cast speed on the bot sends the next spell the moment the server answers the current one (`su`/`cancel`) and re-casts when `sr <castSlot>` arrives; a `cancel 0 <castSlot> -1` means the server refused a cast that was early. Test: `gui_cast_ready_smoke.py` shows 0.63 s between casts when the server's cast time is 0.6 s. That is the floor |
| `CAST\|1` answers `unknown command` | the injected DLL is older than 0.2.8 | harmless: Cast speed is host-side now and the GUI only sends `CAST\|0` to undo the old zeroing; an old DLL can ignore it |
| Skill cooldowns disappeared from the skill bar | an earlier 0.2.8 build zeroed them | reopen the GUI: it sends `CAST\|0` on attach and the server cooldowns come back; no game restart needed |
| `Injector` says a logger is already loaded after `QUIT` | the old module stays mapped, and the guard refuses a second injection | reload with a new DLL filename and `Injector.exe --pid <pid> --dll <name>.dll --force` (only after that logger's QUIT and its `C:\ntpl-<pid>.port` file has disappeared; `--force` refuses while the port file exists), or restart the game client |
| Client crashes after injecting a DLL | a second live logger was injected (two send/recv hooks) | restart the game client; never `--force` while the old logger is still listening. The injector now refuses if the old port file exists |
| Bot cannot reach a target near a map edge | the only route crosses a warp tile and blocking is on | that is the protection working; walk there manually with blocking off, or fight from range |

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
