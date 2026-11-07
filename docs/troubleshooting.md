# Troubleshooting

## Connection

| Symptom | Likely cause | Fix |
|---|---|---|
| `ntpl: cannot connect to 127.0.0.1:47821 after N attempt(s)` | DLL not injected, game not running, wrong prefix, or wrong port | `scripts/inject.sh`; confirm `pgrep -af NostaleClientX`; make sure `NTPL_PORT` is the same in the game's environment and the host |
| Connects, then immediately disconnects | another host is connected (one at a time) | close the GUI/monitor before using `ntpl send`; or use the monitor's `/send` |
| Port already in use | a previous DLL instance, or another process | `ss -ltnp | grep 47821`; set `NTPL_PORT` for both sides |
| `ntpl status` times out | connected but the DLL is stuck before its status line | check the DLL log; re-inject |

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
| `MOVE` sent, but the character does not move | character is sitting, or the target is blocked | `send "rest 1 1 <charId>"` once; try another adjacent tile |
| Character teleports instead of walking | a raw `walk` packet was injected; the client did not animate it | use `MOVE` / `ntpl walk`, which calls the client's own MoveTo |
| `MOVE` returns `client build mismatch` | the client build moved the MoveTo function | re-derive the address (see `docs/protocol/movement.md`); raw packets still work |
| `POS` returns "not readable" | the client build moved the player object | re-derive `0x792468` and the +4/+6/+0x24 layout |
| `cast` sent, but no `su` | character sitting, target out of range, or the `u_s` field order is wrong | stand, move into range, and use `--packet "u_s …"` with a layout captured from the live client |
| `rest` toggles twice | two identical packets were sent | send `rest 1 1 <charId>` once per state change |
| Client looks stuck after many injected walks | injected walks can desync the client | use `MOVE`; if already desynced, relog/restart the client |
| Spell vnum unknown | the client's hotbar mapping is not documented | cast once manually and read `su 1 <charId> ...` from the log |

## UI

| Symptom | Likely cause | Fix |
|---|---|---|
| `gui.sh` says GTK4/PyGObject is missing | bindings not installed | `sudo apt install python3-gi gir1.2-gtk-4.0` |
| Window says "not connected" | DLL not injected, game not running, or wrong port | inject first; check `NTPL_PORT` on both sides |
| GUI opens but no packets | filter too narrow, or another host is connected | clear the filter; the DLL accepts one host at a time |
| Bot does not attack in "Only selected" mode | Auto-attack is off; recovery is sitting below the HP threshold; the monster vnum is not selected; or a pre-injection monster has no vnum | check the green/red Auto-attack toggle and the red RECOVERING banner; wait for recovery or turn it off; verify the target list; enable **Also attack monsters with unknown vnum** or let the monster respawn |
| Map does not switch after a portal | old build read the first `c_map` field (type 0) as the map id | update/restart the GUI; the engine reads the second field and clears stale entities |
| Wrong map right after attaching to a running game | the GUI never saw `c_map`/`at` and guessed from positions; a few maps (or a same-layout twin) tie at 100 % | restart the GUI with the current build: the auto-detect uses every live entity and prefers the tightest perfect fit; walk once or cross any portal to get the authoritative id |
| Tooltip shows `Monster #id` with no name | the entity spawned before the DLL/GUI and has no `in` vnum | `spawnlog.py` covers pre-GUI spawns; for pre-injection spawns enable the unknown-vnum option or re-enter the area so `in` is sent again |
| Toggles are grey, not green/red | an old GUI process predates the CSS classes, or CSS was installed after the widgets | restart `gui.sh` with the current build |
| Path is still 90° / staircase | the path was built by the old 4-direction A* | restart the GUI and click the waypoints again; paths are rebuilt on click |
| Add spell… picker is empty | `NSgtdData.NOS` is missing or `NTPL_NSGTD` points elsewhere | set `NTPL_NSGTD` / `NTPL_NSTC`; manual vnums still work |
| Spell rule never casts | not enough MP, target out of the spell's own range, rule/spell cooldown still running, or a higher rule is always winning | check the rule's description metadata; wait, move closer, raise MP, or reorder/disable the higher rule |
| Rule order is not what I wanted | the list is evaluated 1., 2., … from the top | use the ↑/↓ buttons; the number on the left shows execution priority |
| Character swings at empty space / “attacks on blind” | UI Range selected a monster outside the skill's real range; the client animates but the server rejects or self-targets | the bot now caps basic attacks at the scanned skill range (866 = 11); use a longer-range spell rule or walk closer. A status message shows the distance |

## Injection

| Symptom | Likely cause | Fix |
|---|---|---|
| `No NosTale client found` | game not running, different prefix, or executable name changed | check `--list`; confirm `WINEPREFIX` |
| `LoadLibraryA returned NULL` | wrong DLL path, 32/64-bit mismatch, missing dependency | pass an absolute DLL path; confirm `file` says PE32 i386; check the client's loader error |
| `OpenProcess: Access is denied` | injector not elevated (Windows) or wrong prefix (Wine) | run as administrator on Windows; use the game's prefix under Wine |
| Injected but no TCP server | bind failed (port in use) or the DLL exited early | read `<prefix>/drive_c/NosTalePacketLog.txt`; change `NTPL_PORT` |

## Logs

| Symptom | Likely cause | Fix |
|---|---|---|
| No `C:\NosTalePacketLog.txt` | `C:\` not writable | the DLL falls back to `%LOCALAPPDATA%\NosTalePacketLogger\dll.log`, then `%TEMP%`; set `NTPL_LOG` explicitly |
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
