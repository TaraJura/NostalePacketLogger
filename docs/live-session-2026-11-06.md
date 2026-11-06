# Live session 2026-11-06

This document records what was actually verified against the running NosTale
client, the bugs the live test exposed, and the game mechanics learned. The
addresses and PIDs are session-specific and change after a client restart.

> The first sections describe the morning/afternoon session (through the skill
> hook fix). The same client stayed up and the work continued well past 17:00;
> see [Later same-day work](#later-same-day-work-16001750) at the end for map
> switching, monster names, target filters, spell rules, config export/import,
> 8-direction pathing and the final UI state. Test counts in the older
> paragraphs are snapshot values; the current count is **78**.

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

The GTK4 window gained a **Map / Bot** tab (`docs/assets/gui-map.png`). This
was the **initial** control set; the later section lists the current UI (the
map/scale/char-id controls were removed, and Targets/Spells/Configuration were
added):

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
- The host test count was 59 at that point (78 now; see the later section); `make bot-test` also runs the
  rejected-move regression test. The DLL in the client is `PacketLogger8.dll`
  (hooks ACTIVE, `MOVE`/`POS` available).

## Reproduction

```bash
# build and unit tests
make -C linux all test-host
make -C linux host-test        # 84 cases
make -C linux gui-test         # headless GTK4 + map surface
make -C linux bot-test         # 6 smokes: combat, rules/config, spell order, out-of-range, rejection, recovery

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

## Later same-day work (16:00–17:50)

The live client stayed up after the skill-hook fix. The GUI was restarted
many times as features landed; every restart reconnects to the DLL and reloads
the session log.

### Map switching

Live capture around a portal crossing:

```
16:28:42.110 [RECV] c_map 0 20 0
16:28:42.110 [RECV] mapout
16:28:42.115 [RECV] at 14881134 85 6 8 2 0 13 1 -1 0 0
16:28:42.132 [RECV] c_map 0 85 1
```

`c_map` is `<type> <mapId> <state>`; the **second** field is the map id.
Reading the first field set `WorldState.map_id` to 0 after `at`, so the UI
never switched maps. The engine now reads the second field, clears
entities/drops on `c_map`/`mapout`, and the GUI switches immediately on
`c_map`/`at` (the 2 s tick remains a fallback). The GUI regression test
replays the live 20→85→20 sequence.

### Auto-attack and Move first

- Normal attack is `ncif 3 <id>` + `u_s 0 3 <id>`; the server reports the
  basic skill 866/868 in `su`. Skill 866 is not castable (`cancel 0 866 -1`).
- **Move first** (default on) now keeps auto-attack enabled: the bot attacks
  when the cooldown is ready and resumes walking after the attack animation.
  With it off the bot holds position and clears nearby monsters before moving.
- No path is a valid stationary combat/pickup/recovery mode.

### Monster and skill names

`NSlangData_UK.NOS` is a text archive (`count`, then per entry: number, name
length, name, isDat, size, data). `_code_uk_monster.txt` is nibble-coded and
decrypts to `zts<vnum>e<TAB><name>` records: **2,795** names. Examples from the
live session:

| vnum | name | live entity observed |
|---|---|---|
| 200 | Mountain Turtle | 5032 |
| 201 | Rock Turtle | 5068 |
| 206 | Angry Queen Bee | 5122 |
| 208 | Weak Spider | 5148 |
| 217 | Small Wasp | (wasps near the player) |
| 218 | Wasp | |
| 219 | Large Wasp | |
| 223 | Small Wiggler | 5161/5162/5163/5164 |

`_code_uk_Skill.txt` uses the same record shape and supplies the spell
description shown in the spell-rule editor. `monsternames.py` implements the
archive reader, nibble codec and both tables.

The `in 3 <vnum> <entityId> ...` packet carries the template vnum first and
the per-session entity id second. `ncif`/`u_s`/`su` use the entity id.

### Tooltips and spawn-log backfill

Map tooltips describe the own marker, players, NPCs/pets, monsters (name,
entity id, vnum, position, distance, “attacking you”) and drops (item vnum,
amount, drop id, owner). `spawnlog.py` scans the DLL log's `[RECV] in ...`
lines at GUI startup so entities that spawned **before the GUI connected**
still get their vnum/name. A monster that was alive before the **DLL was
injected** has no `in` line at all; it will show `#entityId` until it respawns
or re-enters view. The Targets section has an explicit **Also attack monsters
with unknown vnum** option for that case, and the status bar warns when such
monsters are in range.

### Targets, spell rules and configuration

- Targets: All / Only selected / All except selected by monster template
  vnum; search by name or vnum, manual vnum entry, and the unknown-vnum
  option. The filter applies to Retaliate too.
- Spells: the **Add spell…** picker scans `NSgtdData.NOS` → `Skill.dat` and
  shows the active spell catalog (Holy Attack 866, Healing 867, Mana Shield
  869, Holy Hammer 876, ...) with MP cost, range, cast time and cooldown.
  The `ski 0 866 866 866 867 ... 876 ...` packet is the learned skill list;
  `u_s` uses a zero-based slot into it, not the vnum. Live mapping:
  slot 0→866, 2→868, 7→873, 8→874, 9→875, 10→876. The bot resolves the rule's
  vnum to this slot (live `ski` or the log backfill) before sending.
  Rules are ordered 1., 2., … and use **Always**, My HP % or My MP %; the
  first eligible rule wins. Always + cooldowns forms a rotation. Static MP,
  range, cooldown and cast-time checks come from Skill.dat. Requires
  Auto-attack; recovery runs first. The order smoke casts 876 first, then
  866 twice while 876 is on cooldown.
- Configuration: **Save config… / Load config…** export/import all bot
  settings as validated JSON (`botconfig.py`). Loading stops the bot;
  waypoints/path are session state and are not saved. Full schema and caveats
  in [bot-config.md](bot-config.md).

### Pathing

The original path was 4-direction BFS, so every route had 90° corners. It is
now **8-direction A\***: diagonal cost √2, octile heuristic, and a diagonal is
only allowed when both orthogonal cells are walkable. The overlay is a
continuous cyan polyline through cell centres. Diagonal legs are paced and
timed out by Euclidean travel distance. Tests cover an open-ground diagonal
shortcut and the no-corner-cutting rule.

### UI state

- Toggle buttons are **green when active** and **red when inactive** (Loop,
  Auto-attack, Retaliate, Move first, Pickup, Auto-recover).
- A red **RECOVERING** banner shows the HP percentage and that attacks and
  movement are paused. The banner is what explained a live report of “only
  selected is not working”: the character was at 24 % HP, below the 30 %
  recovery threshold, so the bot was sitting.
- The sidebar width is capped so a wide child cannot steal half the window
  and shrink the map.

### Verification and state

- **84 host tests**, `make gui-test` (map switch, tooltips, toggle classes,
  recovery banner), and **6 bot smokes** (combat, target filter + spell rules
  + config round-trip, rejected walks, recovery) pass.
- Headless rules smoke: two monsters (vnum 201 nearer, vnum 200 farther),
  allow only 200, HP 40 %, rule spell 7 below 50 % → `ncif 3 500` +
  `u_s 7 3 500`, no traffic to 501, no basic attack; save/load restores it.
- Live path/auto-attack/pickup/map-switch captures above; GUI restarted with
  the final build and connected (`recv/send/skill ACTIVE`, drops 0).
- Self-target spells are live-captured: `u_s 7 1 14881134` →
  `su 1 14881134 1 14881134 873 ...` (Group Healing). Remaining caveat:
  diagonal MoveTo is handled by the client's own pathfinder and should be
  watched on the first live run.


### Live feature pass (late, ~22:47–23:36)

A full live pass was run against character 14881134 on map 20, with the GUI
stopped so the test harness could own the DLL host. The character started at
(45,187), 3209/3209 HP and 4401/4401 MP. No client or game process was
restarted or killed.

| Feature | Live result |
|---|---|
| Hooks / status | `recv`, `send` and `skill` ACTIVE; drops 0. |
| POS | `45 187 14881134`, matching the client's local player object. |
| MoveTo | `MOVE|48 187` → status `MOVE 48 187`, client `[SEND] walk 48 187 1 10`; position updated. |
| Sit/stand | `rest 1 1 14881134` → `rest … 1` (sitting), second toggle → `rest … 0` (standing). |
| Basic attack | `ncif 3 5030` + `u_s 0 3 5030` → `su 1 14881134 3 5030 866 …`, damage 1056, no self-target. |
| Spell slot 9 | `u_s 9 1 14881134` → `su 1 14881134 1 14881134 875 …` (Blessing); MP 4401 → 4251 (150 cost). |
| Spell slot 7 | `u_s 7 1 14881134` → `su … 873` (Group Healing); HP 2761 → 3209 (full). |
| Spell slot 1 | `u_s 1 1 14881134` (Healing 867) → `cancel 0 1 -1`; 867 is not self-castable. The picker heuristic was fixed to default only Skill.dat target type 1 to Self. |
| Bot combo (path + spell) | Path (49,187)→(52,187), Auto-attack + Move first + Pickup on, Auto-recover off, rule Mana Shield 869 Always/Self. The bot cast `u_s 3 1 14881134` → `su … 869` (MP -300), then `MOVE 52 187` → client `walk 52 187`. |
| Bot combo (filter + attack) | No path, target mode Only selected vnum 200, Auto-attack/Retaliate/Move first/Pickup on, Auto-recover 75 %, rule Mana Shield 869. It cast 869, then attacked only entity 5030 (`su … 3 5030 866 …` three times); other monsters were ignored. |
| Pickup | The turtle dropped item 5965 (drop 16830502, owner 14881134) at (46,187): `get 1 14881134 16830502` → `get … 0`, `ivn`/`sayi` confirmation. |
| Out-of-range guard | Earlier live packet: bot targeted 5087 at 35+ cells and the server answered self-target; fixed to cap basic attacks at the scanned skill range and covered by a smoke. |

**Safety incident:** the filter/attack combo ran for 9 s and was stopped while
the turtle was still hitting. Stopping the bot does not disengage, and HP fell
from 3209 to 1177 before a delayed Group Healing 873 cast landed and restored
3209/3209. The character never died; the lesson is recorded in
[feature-review-2026-11-06.md](feature-review-2026-11-06.md): finish the target
or move away before stopping a live bot, and keep a high recovery threshold.

Not exercised live in this pass: forced low-HP recovery (no safe way without
risking the character) and a portal crossing (map switching was verified live
earlier in the day). Their headless smokes pass. The live pickup is now
verified.

## Safety

Gameforge's terms prohibit automation. This work is on the account owner's own
client and account; the owner accepts the risk. The DLL does not hide itself
or evade detection.
