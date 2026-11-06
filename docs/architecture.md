# Architecture

## The constraint that shapes everything

NosTale's client is a **32-bit Windows PE** (a Delphi application). On Linux it
runs under Wine, but it is still a Windows process executing Windows code. To
see the game's plaintext packets, the logger has to run **inside that process**,
after the game has decrypted them and before it encrypts the ones it sends.

That single fact decides the split:

- The **hook DLL is 32-bit Windows code** on both platforms. It is built with
  MinGW-w64 on Linux and with MSVC on Windows.
- Everything **outside** the game is native to the host operating system:
  the transport, the client library, the CLI, the GUI and the state engine.

Trying to make the in-game part "native Linux" would mean reimplementing the
game's own protocol stack and cryptography, or hooking Wine internals. Both are
more fragile than a DLL inside the process.

## Component diagram

```
+---------------------------- game process (32-bit PE, under Wine or Windows) ----------------------------+
|                                                                                                         |
|  NostaleClientX.exe                                                                                     |
|                                                                                                         |
|    recv path   --[5-byte E8 call]--> ntpl_hook_recv --+                                                 |
|                                                       |--> PacketQueue<recv> --+                         |
|    send path   --[6-byte prologue]--> ntpl_hook_send -+                       |                         |
|                                                       |--> PacketQueue<send> --+                         |
|                                                                               |                         |
|    worker thread:  drain queues --> file log  C:\NosTalePacketLog.txt         |                         |
|                    drain queues --> TCP server 127.0.0.1:47821  <-------------+                         |
+----------------------------------------------|----------------------------------------------------------+
                                               |
                                               |  loopback TCP, one UTF-8 line per message
                                               v
+---------------------------------------------------------------------------------------------------------+
|  native host (Linux or Windows)                                                                         |
|                                                                                                         |
|    ntpl.client.PacketClient  -->  ntpl.cli (monitor / send / status / replay)                           |
|                             -->  ntpl.gui (GTK4 window)                                                 |
|                             -->  ntpl.engine.Engine  -->  WorldState (map, position, entities, stats)     |
+---------------------------------------------------------------------------------------------------------+
```

## Why the transport is TCP on loopback

The original Windows build used named pipes. Named pipes are a Windows concept:
a Wine named pipe is implemented inside Wine and is **not** reachable from a
native Linux process. A Winsock socket inside Wine, however, is an ordinary
Linux socket, so `127.0.0.1:47821` is reachable by native Python directly.

TCP on loopback therefore gives one transport that works in all three cases:

1. game under Wine on Linux + native Linux host (the main target);
2. game on Windows + native Windows host;
3. game on Windows + host in WSL, if ever needed.

It also removes the ordering trap of the original two named pipes (the GUI had
to connect the packets pipe before the commands pipe). One connection carries
both directions.

The trade-off is that the DLL must listen on a local port. It binds
`INADDR_LOOPBACK` only, never a public interface. `NTPL_PORT` changes the port.

## Windows and Linux side by side

| | `windows/` (original) | `linux/` (port) |
|---|---|---|
| Build | MSVC v145, Visual Studio solution | MinGW-w64 13, `linux/Makefile` |
| In-game DLL | C++14 + MSVC inline asm | C++17 + GAS assembly stubs |
| Hooks | same two signatures, same offsets | same |
| Transport | two named pipes | one TCP connection |
| GUI | C# WinForms (.NET 8, x86) | Python + GTK4 |
| Automation | GUI + command file `NosTalePacketCmd.txt` | CLI + Python library + engine seed |
| File log | `C:\NosTalePacketLog.txt` | same (falls back to `%LOCALAPPDATA%`) |
| Console | `AllocConsole()` window inside the game (closing it kills the game) | no console; file log + TCP status |
| Injector | C++ console, interactive or `--pid` | C++ console, `--all` by process name |
| Status | GUI status line | `ntpl status`, `STATUS|` lines |

The packet semantics in `docs/protocol/` are identical. Only the plumbing
differs.

## Data flow

1. The game calls its own recv function. The first instruction of that call
   site is overwritten with a jump to `ntpl_hook_recv`.
2. The stub saves registers and flags, passes the packet pointer (`EDX`) to
   `ntpl_on_packet_recv`, then replays the original call and jumps back. The
   game continues as if nothing happened.
3. `ntpl_on_packet_recv` copies the string into a preallocated ring slot. It
   never allocates, never blocks on the game, and drops (with a counter) if the
   ring is full.
4. The worker thread drains the ring, appends the packet to the file log, and
   writes `RECV|<packet>` to the connected host.
5. The host parses the line into `Message` / `Packet`, prints it, feeds the
   `Engine`, and the GUI shows it.

The send path is symmetrical, with a trampoline because the hook overwrites a
6-byte function prologue rather than a 5-byte call.

Host-to-game packets take the reverse path: `SEND|<packet>` over TCP, the DLL
builds a Delphi string, and calls the game's own send function through
`TNTClient`.

The DLL also exposes loopback-only `SCAN`, `FIND` and `READ` debug commands for
locating and reading structures in the game process, plus `MOVE|x y` and `POS`
for movement. `MOVE` calls the client's own MoveTo/pathfinding function, so the
game animates the character and sends its own `walk` packets; injecting a raw
`walk` packet only moves the server and makes the client teleport on the
correction. `POS` reads the local player object. See [ipc.md](ipc.md) and
[live-session-2026-11-06.md](live-session-2026-11-06.md).

## Host state model

The host turns the stream into a `WorldState` (`linux/host/ntpl/engine.py`):
map id, own position, entities keyed by entity id, HP/MP, chat, sit/stand
state, the selected target, the last skill, and a ring of combat events.
Parsing is tolerant: an unknown opcode, a short packet or a non-numeric field
is ignored, never fatal.

| Action | Send | Confirmation |
|---|---|---|
| move | `MOVE\|x y` (client MoveTo) | the client's own `walk` SEND / `world.position` |
| sit/stand | `rest 1 1 <charId>` (toggles) | `rest <type> <charId> <state>` (0 stand, 1 sit) |
| target | `ncif <type> <id>` | no direct reply; used before a skill |
| skill | `u_s …` | `su <attacker> <target> <skill> …` |

Sitting regenerates HP/MP but **blocks movement**; stand before walking. The
client does not reliably echo its own actions, so the confirmation packets are
the only trustworthy signal. See [engine.md](engine.md) for the full API and
command reference.

## Map, path and auto-attack

The client's `NostaleData/NStcData.NOS` contains one walkability grid per map
(779 grids; one byte per cell; `0x00` is open ground). `mapdata.py` parses the
archive, renders the terrain palette, ranks maps against live entity positions
and runs A* over walkable cells.

The GTK4 **Map / Bot** tab shows the selected grid, the live entities and the
own character, lets the user click waypoints, and draws the A* path. The bot
runs on the GLib main loop:

1. stand up if the `rest` state is sitting;
2. auto-attack the nearest monster in range (`ncif` + configurable `u_s`);
3. walk the next path cell through the DLL's `MOVE|x y` command, which calls
   the client's own MoveTo/pathfinding, so the client animates and sends its own
   `walk` packet; confirm from `world.position`.

Everything still goes through the same `PacketClient`; the map is only a view
and a path source. See [map.md](map.md).

## Threading model

| Thread | Owns | Rules |
|---|---|---|
| game network thread | recv hook | only `PacketQueue::push`; no allocation, no logging, no socket |
| game logic thread | send hook | same |
| DLL worker thread | queues, file log, TCP server, packet injection | the only thread that touches the log, the socket, or the game's send function |
| host reader thread | socket → `queue.Queue` | no GTK calls |
| host main thread | CLI/GUI | GTK only here, via `GLib.timeout_add` |

The queue is a fixed-capacity multi-producer ring with a tiny spinlock. Two
game threads can push at once; one worker pops. See
[hooking.md](hooking.md#the-packet-queue) for why it is not `std::queue`.

## Design rules

1. **Never allocate or block inside a hook.** A game thread is holding its own
   locks; a long pause is a freeze or a disconnect.
2. **Bound everything.** 4096 slots × 2048 bytes per direction, 64 KiB per host
   line, 10 s injection wait, 2 s socket send timeout.
3. **Drop and count, never stall.** A full queue increments `drops`; the next
   `STATUS` reports it.
4. **The file log is the source of truth.** It is written even when no host is
   connected, so a session can be replayed or analysed later.
5. **A missing signature is not a crash.** The DLL keeps running, reports
   `NOT-FOUND`, and the host shows it.
6. **The game owns the process.** The DLL does not patch files, does not
   disable checks, and does not try to hide. It restores every byte it changed
   when it disconnects. The trampoline and module stay mapped until the process
   exits, because a game thread may still be inside a stub during unhook.
7. **Confirm actions from the server.** A walk is done when the server sends
   `tp`/`mv` at the target; a skill is done when `su` arrives; sit/stand is
   done when `rest` echoes the new state. Never assume a sent packet worked.
8. **Debug probes are read-only and loopback-only.** `SCAN`/`FIND`/`READ` read
   the game's own memory; they are not part of the play loop and must not be
   exposed beyond `127.0.0.1`.
9. **Animate movement in the client.** A raw `walk` packet only moves the
   server; the correction arrives as `tp` and the character teleports. `MOVE`
   calls the client's own MoveTo so the game pathfinds, animates and sends its
   own `walk`. The call site and player object are client-build-specific:
   validate the prologue/pointer and fail soft after a patch.

## Directory map

```
NostalePacketLogger/
├── docs/                  architecture, engine, hooking, IPC, map, platform
│                          guides, live session, packet wiki
│   └── protocol/          the shared packet documentation
├── linux/                 MinGW DLL + injector + Python host + tests
├── windows/               original MSVC/C# solution, unchanged
├── LICENSE                MIT (original project)
├── CLAUDE.md              AI working context
└── README.md              entry point
```
