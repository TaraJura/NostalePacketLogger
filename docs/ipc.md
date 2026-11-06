# Wire protocol (DLL ↔ host)

The Linux port uses one TCP connection on loopback. The original Windows tree
uses two named pipes; the message format is the same. This document describes
the TCP transport; the legacy mapping is at the end.

## Transport

| Property | Value |
|---|---|
| Server | the injected DLL |
| Address | `127.0.0.1` only (never a public interface) |
| Port | `47821`, or `NTPL_PORT` from the environment of the game process |
| Connections | one host at a time |
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
| `QUIT|` | host → DLL | unhook, stop the server and unload the DLL |

The `SEND` overloading is inherited from the original two-pipe design and is
kept so the packet log and the GUI behaviour stay familiar. Direction is
decided by which side you are, not by the message.

### Handshake

1. Host connects.
2. DLL sends one `STATUS|...` line immediately. It contains the version, the
   three resolved addresses, hook state, and drop counters. Example:

   ```
   STATUS|ntpl 0.1.0 | recv=0x004A1B20 send=0x004A3C40 tnt=0x004B00A4 | hooks recv=ACTIVE send=ACTIVE | drops recv=0 send=0
   ```

3. The host may send `STATUS` at any time to ask again.
4. Packets flow. `RECV` and `SEND` are separate queues, so ordering **across**
   directions is not guaranteed; ordering within one direction is FIFO.
5. `QUIT` makes the DLL disable capture, wait 100 ms for in-flight hooks,
   restore the original bytes, stop the socket and unload itself.

### Status text

The status payload is free-form but starts with the same fields the Windows
build reported, so a host can parse it or just display it:

- `ntpl <version>`
- `recv=0x... send=0x... tnt=0x...` — resolved addresses; `0x00000000` means
  the signature was not found
- `hooks recv=ACTIVE|FAILED|NOT-FOUND send=...`
- `drops recv=<n> send=<n>`

`FAILED` means the signature was found but the byte patch failed; `NOT-FOUND`
means the client build does not match the signatures. Both are non-fatal: the
DLL stays alive and keeps reporting.

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
