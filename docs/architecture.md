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
   when it disconnects.

## Directory map

```
NostalePacketLogger/
├── docs/                  architecture, hooking, IPC, platform guides, packet wiki
│   └── protocol/          the shared packet documentation
├── linux/                 MinGW DLL + injector + Python host + tests
├── windows/               original MSVC/C# solution, unchanged
├── LICENSE                MIT (original project)
├── CLAUDE.md              AI working context
└── README.md              entry point
```
