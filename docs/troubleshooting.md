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
