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

The first injected walks used `walk 25 117 0 10` and got
`tp 1 14881134 25 117 0`. That looked like movement, but it was the server
**correcting** the client to its authoritative position after an invalid walk
(the same `tp` came back for targets like `121 23`). The actual walk packet
needs a checksum, and the server silently ignores the wrong one.

The client's own walk builder is at VA `0x72DDD8`. It computes the third field
as:

```
checksum = 1 if (x + y) % 3 == 1 else 0
```

The client's own walks match this (e.g. `walk 22 123 1 10`, sum 145). Live
tests confirmed the server enforces it:

```
walk 19 138 1 10   -> probe -> tp 1 14881134 19 138 0   (accepted)
walk 19 138 0 10   -> no tp                              (ignored)
walk 20 140 1 10   -> probe -> tp 1 14881134 20 140 0   (accepted)
```

The server does **not** echo an accepted walk. Its `tp` appears only when it
wants to correct the client, and an impossible probe (`walk -1 -1 0 10`)
reliably triggers that correction at the authoritative position. But that
correction is exactly what made the character **teleport**: the client did not
animate the injected walk, then snapped to the server's `tp`.

The fix is the client's own pathfinding entry point, found at VA `0x565E88`:

```
__fastcall bool MoveTo(PlayerObject *this /*EAX*/,
                       uint32_t packedXY /*EDX*/,  // x | (y << 16)
                       int flags /*ECX*/, int run /*stack*/)
```

The player object is `[[0x792468]]`; int16 x at +4, y at +6, char id at
+0x24. The DLL now exposes:

- `MOVE|x y` — call MoveTo; the client computes a path, animates, and sends
  its own `walk` packet (with the correct checksum).
- `POS` — read the local player position/char id.

Live tests:

```
MOVE|25 133   -> client [SEND] walk 25 133 0 10; local position became (25,133)
GUI bot       -> (26,133) -> (23,130), six client [SEND] walk packets
                 (25,133 0; 24,133 1; 23,133 0; 23,132 0; 23,131 1; 23,130 0)
```

No MANUAL_SEND walks, no probes and no `tp` corrections: the character walked
and animated normally. `ntpl walk` and the GUI bot now use `MOVE`; the earlier
checksum/probe path remains available as raw packet tooling.

Some targets are still rejected (blocked tile/entity). The bot stops after
three consecutive unconfirmed moves instead of walking away from a rejected
position.

### Phoenix Bot

The user installed the third-party Phoenix Bot (Qt5, 9.5 MB) to compare. Its
PE has `.UPX0/.UPX1` sections but the `UPX!` magic is removed; `upx -d` reports
`NotPackedException`, and it crashes under the system Wine 10 before yielding
useful behaviour. No code or data from it was reused. The client-side MoveTo
approach above was found directly in `NostaleClientX.exe`.

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

The exact `u_s` field order is now captured from the client's own skill hook.
Two kinds of packets appeared:

```
[SEND] u_s 0 3 5160
[RECV] su 1 14881134 3 5160 866 ...     # normal/basic auto-attack
[SEND] u_s 10 3 5161
[RECV] su 1 14881134 3 5161 876 ...     # a cast skill
```

So **skill 0 is the normal attack**; the server reports the character's actual
basic-attack skill (866, or 868 for a different attack) in `su`. Skill 866 is
not a castable `u_s` skill: `u_s 866 ...` is answered with
`cancel 0 866 -1`. The bot now defaults to skill 0. `ntpl cast 0 --target-id
5160 --target-type 3` is the verified normal-attack command; skill 10 is a
different skill the client can also send.

The first version of the skill hook replayed the original call and then used
`ret`; because `Memory::Hook` patches the call site with a **JMP**, no return
address was on the stack and the client crashed at 15:32:29 right after
capturing the packet. The stub now ends with `jmp <call_site+5>`. The game
process exited, which is why the Gameforge client kept showing the stale
"Ve hře" state; a game restart is needed to clear it and to inject the fixed
DLL.

### Loot

The server announces ground items with

```
[RECV] drop 5193 13979116 49 169 1 0 14881134
```

(`itemVnum dropId x y amount unknown ownerCharId`; owner 0 = unowned). The
client picks one up with

```
[SEND] get 1 14881134 13979116
[RECV] get 1 14881134 13979116 0
```

The bot tracks `WorldState.drops` and sends `get` for its own/unowned drops
within the GUI's Loot radius (default 3). Live verification: the drop above
produced the `MANUAL_SEND get` and the server's four-field `get`
confirmation at 15:57:11. It retries each drop up to three times; a server
confirmation removes it. Distant drops are not auto-approached yet.

### Facing

The protocol has no packet that states the local player's facing. The only
explicit direction field is in the `in` spawn packet (`in 1 <name> - <charId>
<x> <y> <direction> ...` and `in 2|3 ... <x> <y> <direction> ...`). The log
analysis found 63 players; the 25 who spawned more than once kept the same
direction value at different positions, so that field is a spawn/appearance
constant, not a live facing. `mv` has no direction and the client's own `tp`
is a correction.

The GUI therefore derives the facing arrow from the last `walk` delta: it
points in map coordinates (`+x` right, `+y` down) and supports all eight
directions, including diagonals. If the character turns in place, the arrow
does not change until the next move — that is the honest limit of the packet
stream.

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

- Character `14881134`; after the final live bot run the position was
  `(23,130)`, standing. Movement now goes through the client's own MoveTo, so
  the local and server positions stay in sync without teleport corrections.
- The game process **exited at 15:32:29** when the first skill-hook build used
  `ret` after a JMP-patched call. The fixed build is in `linux/build`; the
  client must be restarted, then the fixed DLL injected under a new filename.
  The Gameforge client still shows the stale "Ve hře" state until the game is
  started again (or the Gameforge client is restarted).
- The DLL (`PacketLogger9.dll`) was the crashing build; older
  `PacketLogger*.dll` copies are inert until the client restarts. The fixed
  build reports `skill=ACTIVE` and captures `u_s 0 3 <target>` (normal attack)
  as well as other cast skills.
- The file log is `~/games/gameforge/prefix/drive_c/NosTalePacketLog.txt`.
- The TCP server is `127.0.0.1:47821`.
- The host test count is now 59; `make bot-test` also runs the
  rejected-move regression test. The DLL in the client is `PacketLogger8.dll`
  (hooks ACTIVE, `MOVE`/`POS` available).

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
linux/scripts/host.sh cast 0 --target-id 5160    # normal attack (u_s 0 3 id)
```

## Open items

1. The walkability grid exists (map 20), but some adjacent `0x00` cells are
   still rejected (an entity or object occupies them). The bot skips a cell
   after a timeout and stops after three consecutive rejections; a
   rejected-tile cache would make the path smarter.
2. Movement is solved through the client's `MoveTo`; the remaining unknown is
   the `u_s` skill layout. The client's `walk` SEND packets are captured. Its
   own `u_s`/`ncif` skill packets are not, or have not been observed; capture
   them to confirm the field order.
3. Verify one live cast with the user's own skill vnum and target; lock the
   `u_s` layout in `docs/protocol/combat.md`.
4. ~~Build the closed loop~~ — implemented and **run live**: A* path,
   one client-animated `MOVE` per cell, loop, stand-first, auto-attack. The
   `(21,141)→(18,138)` and `(26,133)→(23,130)` runs passed.
5. Run the Map/Bot tab against the live client with auto-attack enabled and
   tune the range/cooldown from what the server accepts.
6. Reconcile the protocol wiki with more live captures.

## Safety

Gameforge's terms prohibit automation. This work is on the account owner's own
client and account; the owner accepts the risk. The DLL does not hide itself
or evade detection.
