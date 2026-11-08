# Wire protocol (DLL ↔ host)

The Linux port uses one TCP connection on loopback. The original Windows tree
uses two named pipes; the message format is the same. This document describes
the TCP transport; the legacy mapping is at the end.

## Transport

| Property | Value |
|---|---|
| Server | the injected DLL |
| Address | `127.0.0.1` only (never a public interface) |
| Port | `47821`, or the first free port from `NTPL_PORT` upward (see below) |
| Connections | one host at a time |
| Discovery | each DLL writes `C:\ntpl-<pid>.port` with the port it bound |
| Framing | one UTF-8 line per message, `\n` terminated, `\r` tolerated |
| Maximum line | 64 KiB (a longer line without a newline disconnects the host) |
| Encoding | UTF-8; invalid bytes are replaced, not rejected |

The host connects with a retry loop (the DLL may not have started listening
yet). The DLL accepts one connection; when the host disconnects, the DLL goes
back to `accept()` and keeps capturing to the file log.

## Messages

The first `|` separates the type from the payload. The payload may contain
more `|` characters.

| Type | Direction | Meaning |
|---|---|---|
| `RECV|<packet>` | DLL → host | packet received from the game server, already decrypted by the game |
| `SEND|<packet>` | both | DLL → host: the game sent this packet. Host → DLL: inject this packet into the game |
| `STATUS|<text>` | both | human-readable state, hook addresses, drop counters, errors |
| `MOVE|<x> <y>` | host → DLL | run the client's own MoveTo/pathfinding, so it walks visibly |
| `POS` | host → DLL | reply `STATUS|POS <x> <y> <charId> <resting>` from the local player object |
| `TARGET\|<entityId>` | host → DLL | select the entity in the client (target ring + the client's own `ncif`) |
| `QUIT|` | host → DLL | unhook and stop the server; the module stays mapped until the game exits |
| `PORTALS\|1` / `PORTALS\|0` | host → DLL | drop or allow the client's own `preq` portal requests; reply `STATUS\|PORTALS BLOCKED` / `ALLOWED` |

The `SEND` overloading is inherited from the original two-pipe design and is
kept so the packet log and the GUI behaviour stay familiar. Direction is
decided by which side you are, not by the message.

### Handshake

1. Host connects.
2. DLL sends one `STATUS|...` line immediately. It contains the version, the
   three resolved addresses, hook state, and drop counters. Example:

   ```
   STATUS|ntpl 0.2.1 | recv=0x004A1B20 send=0x004A3C40 tnt=0x004B00A4 | hooks recv=ACTIVE send=ACTIVE | drops recv=0 send=0
   ```

3. The host may send `STATUS` at any time to ask again.
4. Packets flow. `RECV` and `SEND` are separate queues, so ordering **across**
   directions is not guaranteed; ordering within one direction is FIFO.
5. `QUIT` makes the DLL disable capture, wait 100 ms for in-flight hooks,
   restore the original bytes and stop the socket. The module and its
   trampoline stay mapped until the game exits (a game thread may still be in a
   stub during unhook). A fresh injection after `QUIT` needs a new DLL filename
   or a client restart.

### Status text

The status payload is free-form but starts with the same fields the Windows
build reported, so a host can parse it or just display it:

- `ntpl <version>`
- `recv=0x... send=0x... tnt=0x...` — resolved addresses; `0x00000000` means
  the signature was not found
- `hooks recv=ACTIVE|FAILED|NOT-FOUND send=... skill=...` — the skill hook
  captures the client's own `u_s` builder output
- `drops recv=<n> send=<n>`
- `portalblock=off|BLOCKED` — whether `preq` is currently dropped

### Movement commands

`MOVE|<x> <y>` calls the client's own pathfinding/MoveTo function
(`0x565E88` in build 2026-11-03) with `x | (y << 16)`; the client computes a
path, animates it and sends its own `walk` packets. This is deliberately not a
packet injection: an injected `walk` moves only the server and the client
teleports when the correction arrives.

`POS` reads the local player object (`[[0x792468]]`): int16 x at +4, int16 y
at +6, uint32 char id at +0x24, rest state at +0xA9 (1 sitting, 0 standing).
The reply is `STATUS|POS <x> <y> <charId> <resting>`; `<resting>` is 1 sitting,
0 standing, -1 when the client cannot be read. A host that attaches after the
player sat down otherwise cannot know why `MOVE` does nothing.

Both commands validate the expected client-build prologue/pointers and return
`STATUS|MOVE: client build mismatch` or `STATUS|POS: ... not readable` instead
of crashing after a client patch. `MOVE` also refuses while the character is
sitting: `STATUS|MOVE: character is sitting (stand up first)`.

### Targeting command

`TARGET|<entityId>` selects a monster through the client's own selector
(`0x56964C` in this build, `EAX` = the selection controller from
`[[0x795200]]`, `EDX` = the entity object). The client then highlights the
target and **sends its own `ncif`**, which is the part the server validates:
injecting `ncif` alone tells the server about a target the client never
selected, and the next `u_s` is redirected to (or cancelled because of) the
client's real selection.

The entity object is found by scanning for the id and validating every
occurrence *during* the scan (not just the first N hits): in-module vtable at
+0, kind 1-2 at +4, the id at +8, a live flag at +0x33, and the targetable
type byte at +0xA9. This matters because a small id such as 3546 occurs in
thousands of table entries before the object's heap address; a hit limit
would report "entity not found" for a monster standing right next to the
player. A small cache keeps the last eight ids, so only the first selection
of each monster pays for the scan (~0.5 s). The reply is:

```
STATUS|TARGET 8684 OK 0x089305D0 0x087B4DD8  # id, entity object, controller
STATUS|TARGET 8684 ERR entity not found in client memory
STATUS|TARGET 8684 ERR client refused the selection  # dead/off-map
STATUS|TARGET 8684 ERR client build mismatch
```

The command is synchronous: when the reply arrives, the client has already
selected the entity (or refused it). A host does not have to wait for the
reply before sending `u_s` -- the DLL processes commands in order on the same
connection, so `TARGET|id` followed by `SEND|u_s ...` selects first and then
attacks. Waiting for the reply only adds latency when the packet stream is
busy; the Python host uses the reply just to block an entity the client
refused.

The client's own `ncif` appears in the normal `SEND|ncif ...` stream, so a host
can confirm the selection from packets alone.

`FAILED` means the signature was found but the byte patch failed; `NOT-FOUND`
means the client build does not match the signatures. Both are non-fatal: the
DLL stays alive and keeps reporting.

### Portal blocking

`preq` is the client's own **portal request**: it is sent when the character
walks on a warp tile and the server answers by moving it to another map. There
is no negative reply, so `PORTALS|1` makes the DLL drop the packet instead:
the send hook still copies it to the stream and the file log (`SEND|preq`), so
a blocked attempt is visible, but the game's send function is skipped and the
server never sees it. The client sends `preq` through its generic packet
builder, so the skill call-site hook applies the same rule; both hooks return
to their caller without replaying the original send on a drop.

`PORTALS|0` restores normal crossing. The GUI sends the command on every
connect and whenever **Block portals** is toggled, so the state survives a GUI
restart but resets to off when the DLL is reinjected.

### Debug / reverse-engineering commands

These are host → DLL only and exist so the engine can find the player's state
without guessing at Delphi object layouts. They are loopback-only and read the
game process's own memory.

| Command | Reply | Meaning |
|---|---|---|
| `SCAN\|<x> <y>` | `STATUS\|SCAN <x> <y> \| 1:<addr...> \| 2:<addr...> \| 4:<addr...>` | find a coordinate pair as 1-, 2- and 4-byte integers |
| `FIND\|<value>` | `STATUS\|FIND <value> <addr...>` | find a single 32-bit value, e.g. a charId |
| `READ\|0x<addr> <len>` | `STATUS\|READ 0x<addr> <hex>` | dump up to 256 bytes |

The scan reads committed writable pages, skips the DLL's own module and the
worker thread's stack, and caps itself at 512 MiB so it cannot stall the game
for long. It is a debugging tool: the authoritative player position comes from
the server's `tp`/`mv` packets, not from these addresses.

### Backpressure

Each direction has a ring of 4096 slots × 2048 bytes (8 MiB). When the ring is
full, the **newest** packet is dropped and `drops` increments. The game thread
is never asked to wait. A slow host therefore loses the tail of a burst but
cannot freeze the client. The file log still receives whatever made it into
the ring; packets dropped before the ring are not logged.

The listener is polled with a zero-timeout `select`, never with a blocking
`accept`. That is deliberate: the worker must keep draining the rings and
writing the file log while **no host is attached**. A blocking accept froze the
log between host connections and overflowed the ring in the first live test.

### Reconnection

The DLL does not require a host to capture. Hooks are installed before the
server starts, and the worker drains the ring into the file log even with no
client connected. A host can attach at any time, disconnect, and reattach; it
misses whatever was logged while it was away, but it can read the file log.

## Several clients at once

Every DLL tries `NTPL_PORT` (default 47821), then 47822, 47823 and so on until
`bind()` succeeds (ten attempts). A second client therefore gets its own port
instead of waiting forever for one that is already taken. This also handles the
stale listener Wine sometimes leaves behind on the base port after a client
restart: the new DLL simply takes the next free port.

After `listen()` succeeds the DLL writes `C:\ntpl-<pid>.port` (inside the
prefix: `<prefix>/drive_c/ntpl-<pid>.port`) containing the port number, where
`<pid>` is the game's **Windows** PID. It deletes the file on `QUIT`.

The native host finds clients without probing the ports, because a probe would
take over the DLL's single host slot and kick whoever is connected:

1. `Injector.exe --list --no-tag`, run with the Wine binary and prefix taken
   from `/proc/<linux-pid>/exe` and `/proc/<linux-pid>/environ`, reports the
   Windows PID and window title of every `NostaleClientX.exe` window.
2. `ntpl.clients` pairs each PID with its port file.
3. The GUI (or the `Client…` button) lists them; a client with no port file is
   injected with `Injector.exe --pid <winpid> --dll <dll>` and its port file is
   awaited.

A process with the client's executable name but a `--type=` switch is a CEF
helper (gpu/renderer/utility), not a game window, and is never injected. A
client without `WINEPREFIX` in its environment is ignored so the tool can never
guess `~/.wine`.

## Minimal clients

Python (the packaged client does this and more):

```python
import socket

sock = socket.create_connection(("127.0.0.1", 47821))
sock.sendall(b"STATUS\n")
for line in sock.makefile("r", encoding="utf-8"):
    print(line.rstrip())
```

Shell, to watch a stream:

```bash
socat - TCP:127.0.0.1:47821
```

To inject one packet:

```bash
printf 'SEND|walk 19 146 0 10\n' | socat - TCP:127.0.0.1:47821
```

A one-shot client must remember that the DLL accepts one host at a time. Do not
run `ntpl send` while `ntpl monitor` or the GUI is connected: the second
connection waits in the listen backlog until the first closes.

## Legacy named-pipe mapping (Windows tree)

| TCP message | Named pipe |
|---|---|
| `RECV|...` | `\\.\pipe\NosTalePacketLogger_packets`, `RECV|...` |
| `SEND|...` (DLL → host) | packets pipe, `SEND|...` |
| `SEND|...` (host → DLL) | `\\.\pipe\NosTalePacketLogger_commands`, `SEND|...` |
| `STATUS|...` | both pipes, `STATUS|...` |
| `QUIT|` | commands pipe, `QUIT|` |

Messages are identical; only the transport and the two-pipe split differ. The
C# GUI in `windows/` speaks the named-pipe variant, not this TCP variant.
