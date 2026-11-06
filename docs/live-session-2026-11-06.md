# Live session 2026-11-06

This document records what was actually verified against the running NosTale
client, the bugs the live test exposed, and the game mechanics learned. The
addresses and PIDs are session-specific and change after a client restart.

## Environment

| What | Value |
|---|---|
| Client | `C:\users\novakj\Desktop\nostake\Nostale\NostaleClientX.exe` |
| Linux process | `/home/novakj/Desktop/nostake/Nostale/NostaleClientX.exe`, PID 72393 |
| Windows PID | 1216 (the main process; two `--type=` CEF helpers share the name) |
| Wine | WineHQ `wine-stable` 11.0 runner in `~/games/gameforge/runner/` |
| Prefix | `/home/novakj/games/gameforge/prefix` (the user launched it there) |
| Host | native Python 3.14, GTK 4.22 / PyGObject 3.56 |

The client happened to run in the Gameforge prefix. The injector targeted only
`NostaleClientX.exe`, never `gfclient.exe`. A dedicated prefix is still the
recommended setup.

## What was verified

### Injection and hooks

```bash
cd ~/games/gameforge/NostalePacketLogger/linux
export WINEPREFIX=/home/novakj/games/gameforge/prefix
export WINEDEBUG=-all
W=~/games/gameforge/runner/wine-stable-11.0/opt/wine-stable/bin/wine

"$W" build/Injector.exe --list --no-tag
# [1] PID 1216  NostaleClientX.exe  NosTale
# [2] PID 1072  NostaleClientX.exe  (no window title)
# [3] PID 2220  NostaleClientX.exe  (no window title)

DLL_WIN=$("$W" winepath -w "$PWD/build/PacketLogger.dll")
"$W" build/Injector.exe --pid 1216 --dll "$DLL_WIN"
```

Result:

```
recv=0x004DBB4D  send=0x004EB5C4  tnt=0x0055AA84
hooks recv=ACTIVE send=ACTIVE    drops recv=0
```

The three signatures matched the live client. The recv hook captured the
server stream, the send hook captured the game's `pulse`, `sortopen` and
`rest`, and injected packets went through the game's own send function.

The client's own `walk`/`u_s` SEND packets were **not** captured by the current
send hook; injected packets and `rest`/`sortopen`/`pulse` are. Recv is the
source of truth for confirmation.

### Sit / stand

The user's explanation was decisive: sitting is the HP/MP regeneration state,
and the server rejects movement while seated.

```
[SEND] rest 1 1 14881134        # client, C key: toggle
[RECV] rest 1 14881134 1        # sitting
[RECV] rest 1 14881134 0        # standing
```

`rest 1 1 <charId>` toggles. Two identical packets toggle twice (the client
sent the packet twice in one press). Send it once per desired change.

### Movement

```
[SEND] walk 25 117 0 10
[RECV] tp 1 14881134 25 117 0   # repeated while the server settled
```

The server's authoritative position moved from `(25,118)` to `(25,117)`, the
requested tile. The client does not reliably echo its own `mv`, so `tp` is the
confirmation. Some tiles are rejected: `walk 25 116` produced only
`cond 1 14881134 0 0 10`, no `tp` (blocked/occupied tile or a settling client).
Confirm one move before sending the next.

### Skills

The live log contained real `su` packets from another player:

```
[RECV] su 1 14877915 3 5180 866 9 11 4401 0 0 0 0 6507 0 0 0 4140
[RECV] su 1 14877915 3 5179 866 9 11 4401 0 0 0 0 5720 0 0 0 4140
```

The first five fields are attacker type, attacker id, target type, target id,
skill vnum (866). The damage/HP positions after that are not decoded yet; the
engine keeps the raw args.

The client's `u_s` and `ncif` builders exist in the binary:

- `u_s ` constant at VA `0x72D630`, short `u_s` at `0x72D640`.
- `ncif ` constant at VA `0x730370`.

The exact `u_s` field order is **not verified** on the live server yet.
`ntpl cast` defaults to `u_s <skill> <targetType> <targetId>` and accepts
`--packet` for a literal captured form. A live cast while the user watches the
log is the remaining step.

### Map and walkability

The client ships `NostaleData/NStcData.NOS` (the map archive the client itself
uses). It parses as 779 grids, one byte per cell, `0x00` = walkable. There is no
separate map packet needed to know the geometry: the file is on disk next to the
client.

The live map was identified by ranking every grid against the `mv` positions in
the session log:

```
map 20: 230x260, 37.3 % walkable
173,503 / 173,503 mv samples (100 %) on 0x00 cells
5,849 unique observed positions, all walkable
```

Map 20 is therefore the map the character was on. The parser also renders it
(`docs/assets/map20.png`), which was checked visually: white corridors, gray
blocked area.

The first parser version read the index as `(offset, id)` and loaded the wrong
block; the index is `(map_id, offset)`. With that fixed, rendering map 20 at the
live position `(25,117)` shows the character on open ground.

### Map / bot UI and the loop

The GTK4 window gained a **Map / Bot** tab (`docs/assets/gui-map.png`):

- map id, `Load`, `Auto-detect` (rank by live entities), scale;
- left click adds a waypoint, right click removes the last; the A* path is
  drawn in cyan;
- **Loop**, **Start/Stop**, **Auto-attack**, skill vnum, range, cooldown, char
  id and the `u_s` template.

The bot on the GLib main loop:

1. stands up with `rest` if the state says sitting;
2. if auto-attack is on and a monster is within range, sends `ncif` and the
   skill packet;
3. otherwise sends one `walk` for the next path cell and waits for `tp`/`mv`;
4. on a timeout it skips the cell; **Loop** wraps the path.

Verified headlessly, not live:

```
make gui-test  -> GUI SMOKE: PASS rows=2 status='ready' map=20 surface=True
make bot-test  -> BOT SMOKE: PASS ncif=True skill=True walk=True
                  lines=['SEND|ncif 3 500', 'SEND|u_s 866 3 500',
                         'SEND|walk 25 118 0 10', 'SEND|walk 25 119 0 10']
```

The bot smoke server sends `at 14881134 20 25 117 ...` and
`mv 3 500 25 118 10`, so the bot picks the monster at `(25,118)`, then walks
the two-cell path with a `tp` answer per cell. This proves the send sequence
and the confirmation logic; it does not prove the live server accepts the
skill packet.

## Bugs the live test exposed (all fixed)

| Bug | Symptom | Fix |
|---|---|---|
| Blocking `accept()` | Between host connections the worker stopped draining; the file log froze and thousands of recv packets were dropped | listener polled with a zero-timeout `select`; the worker always drains |
| `pushal` offset | The hook read the packet pointer at `8(%ebp)` instead of `20(%ebp)` (EDX) | corrected in `hooks.S`; covered by `HookStubTest.exe` |
| Small ring | 512 slots overflowed on the injection burst | 4096 slots × 2048 B (8 MiB/direction) |
| Per-packet `fflush` | drain throughput too low | flush once per drain pass |
| Missing header deps | a changed `PacketQueue` capacity linked against a stale ABI | `-MMD -MP` in the Makefile |
| Unload race | `QUIT` could free the trampoline/unload while a game thread was in a stub | trampoline and module stay mapped; the worker returns instead of `FreeLibraryAndExitThread` |
| Failed `CreateThread` ignored | a loaded-but-inert DLL | `DllMain` returns `FALSE` |
| Non-atomic patch | a thread could execute a half-written jump | aligned `InterlockedExchange64` when the 5/6 bytes fit in one qword |
| Scan false positives | the scan matched its own stack/heap parameters | skip the DLL module and the worker thread's stack; stack scan buffer |
| `READ` address buffer | 8-byte buffer truncated the printed address | 16-byte buffer |

## Host engine state at the end of the session

- Character `14881134`; server position `(25,117)`; sitting (`rest` state 1).
- The DLL (`PacketLogger5.dll`) remained loaded in the client with hooks
  active; older `PacketLogger*.dll` copies from the re-injection tests are
  inert until the client restarts.
- The file log is `~/games/gameforge/prefix/drive_c/NosTalePacketLog.txt`.
- The TCP server is `127.0.0.1:47821`.

## Reproduction

```bash
# build and unit tests
make -C linux all test-host
make -C linux host-test        # 48 cases
make -C linux gui-test         # headless GTK4 + map surface
make -C linux bot-test         # path loop + auto-attack, fake server

# with the game running
export WINEPREFIX=/home/novakj/games/gameforge/prefix
linux/scripts/inject.sh
linux/scripts/host.sh status
linux/scripts/gui.sh

# maps
linux/scripts/host.sh map info 20 --at 25,117
linux/scripts/host.sh map render 20 /tmp/map.png --scale 3
linux/scripts/host.sh map best

# actions (confirm from the server)
linux/scripts/host.sh send "rest 1 1 14881134"   # toggle sit/stand
linux/scripts/host.sh walk 25 117                # waits for tp/mv
linux/scripts/host.sh cast 866 --target-id 5180  # waits for su (u_s format pending)
```

## Open items

1. The walkability grid exists (map 20), but `walk 25 116` was rejected even
   though adjacent logic suggests open ground. Add a rejected-tile cache from
   live attempts and never spam targets.
2. Capture the client's own `walk`/`u_s` SEND packets (the send hook misses
   them) to learn the exact checksum and `u_s` field order.
3. Verify one live cast with the user's own skill vnum and target; lock the
   `u_s` layout in `docs/protocol/combat.md`.
4. ~~Build the closed loop~~ — implemented: A* path, one confirmed `walk` per
   cell, loop, auto-attack, stand-first. Smoke-tested with a fake server; the
   live run remains.
5. Run the Map/Bot tab against the live client with a short path and
   auto-attack, then tune the range/cooldown from what the server accepts.
6. Reconcile the protocol wiki with more live captures.

## Safety

Gameforge's terms prohibit automation. This work is on the account owner's own
client and account; the owner accepts the risk. The DLL does not hide itself
or evade detection.
