# Map & World Packets

## `c_map` (RECV)

Map change — client should load a new map.

**Format**: `c_map <type> <mapId> <state>`

`type` was `0` in every capture. `state` is `1` when entering the map and `0`
when leaving it. The **map id is the second field**; reading the first field
pins the world to map 0.

Captured live on 2026-11-06 (portal crossing, map 20 → 85):

```
16:28:42.110 [RECV] c_map 0 20 0       # leaving map 20
16:28:42.110 [RECV] mapout
16:28:42.115 [RECV] at 14881134 85 6 8 2 0 13 1 -1 0 0
16:28:42.132 [RECV] c_map 0 85 1       # entered map 85
```

And back (85 → 20):

```
16:29:02.819 [RECV] c_map 0 85 0
16:29:02.819 [RECV] mapout
16:29:02.820 [RECV] at 14881134 20 18 190 2 0 11 1 -1 0 0
16:29:02.826 [RECV] c_map 0 20 1
```

The engine updates `WorldState.map_id` from the second field and clears the
old map's entities/drops on `c_map` and `mapout`. The GUI switches maps
immediately on `c_map`/`at` (and also polls every 2 s as a fallback).

---

## `at` (RECV)

Character position after map load. Tells the client where the character is on the new map.

**Format**: `at <charId> <mapId> <x> <y> <direction> <unknown> <unknown> <unknown> <unknown> <unknown> <unknown>`

Live examples:

```
at 14881134 20 14 87 2 0 11 1 -1 0 0   — logged in on map 20 at (14, 87)
at 14881134 85 6 8 2 0 13 1 -1 0 0    — portal crossing: map 85 at (6, 8)
at 14881134 20 18 190 2 0 11 1 -1 0 0 — back on map 20 at (18, 190)
```

The engine uses the second, third and fourth fields for `map_id`, `x` and
`y`, learns `self_id` from the first, clears entities/drops when the map id
changes, and the GUI selects the new map immediately.

---

## `gp` (RECV)

The map's **portal list**, sent right after entering a map (after `at` and
the entering `c_map`). This is the authoritative portal source; no client
data file contains it.

**Format**: `gp <x> <y> <destinationMapId> <unknown> <portalIndex> <unknown>`

`<portalIndex>` is a small zero-based counter (0, 1, 2, …) within the map's
list; the other unknown fields were `-1` and `0` in every capture. The packet
carries **no source map id** — it is the map that was just entered.

Captured live on 2026-11-07:

```
16:41:46.966 [RECV] gp 20 20 104 -1 0 0     # on map 103
16:41:46.966 [RECV] gp 42 32 3 -1 1 0       # on map 103
16:42:46.570 [RECV] gp 10 208 6 -1 0 0      # on map 3 (Mine Plains)
16:42:46.570 [RECV] gp 19 7 7 -1 1 0
16:42:46.570 [RECV] gp 45 12 103 -1 2 0
16:42:46.570 [RECV] gp 156 2 4 -1 3 0
16:42:46.570 [RECV] gp 178 189 2 -1 4 0
```

The positions match the observed crossings exactly: map 103's `(20,20)` is
the portal to 104 and `(42,32)` the one to 3; map 3's `(45,12)` is the
return to 103. The engine stores these in `WorldState.gates`; crossing
learning (`portals`) is only the fallback when a `gp` was missed.

---

## Map IDs (Known)

Only ids observed in packets are listed; more are added by visiting maps.
Names come from `NSlangData_UK.NOS` → `_code_uk_MapIDData.txt`
(`zts<id>e<TAB><name>`, 374 entries) and match the in-game minimap label.

| Map ID | Name | Notes |
|--------|------|-------|
| 1 | NosVille | town; 160x180 |
| 2 | NosVille Meadows | live 2026-11-07; 150x150; the attach-time auto-detect case |
| 20 | Mountain Cave 1 | live starting map 2026-11-06; 230x260, 37.3 % walkable |
| 85 | Path to Altar | visited through a portal on 2026-11-06; 70x104 |
| 300 | Spirit Forest Edge | 240x190; shares its walkable layout with 5613 |
| 5613 | — | no name entry; a same-layout twin of 300 (an old auto-detect wrong pick) |

---

## Map Navigation Flow (Captured)

1. Player walks into a portal → client sends `walk`; server teleports.
2. Server sends the leaving `c_map 0 <oldMap> 0`, then `mapout`.
3. Server sends `at <charId> <newMap> <x> <y> ...` with the new position.
4. Server sends the entering `c_map 0 <newMap> 1`.
5. Server sends the new map's entities as `in`/`mv` packets.

The GUI/engine switch on the `at`/`c_map` pair; stale entities from the old
map are dropped on `c_map` and `mapout`. The cell the character held when the
leaving `c_map`/`mapout` arrived is where the portal is; the following `at`
supplies the destination map, so the engine records it as a portal (the
client has no static portal table).

Captured live on 2026-11-07 (NosVille Meadows → NosVille and back):

```
13:43:02.880 [RECV] c_map 0 2 0
13:43:02.881 [RECV] at 14881134 1 79 2 2 0 111 1 -1 0 0
13:43:02.888 [RECV] c_map 0 1 1
13:43:07.863 [RECV] c_map 0 1 0
13:43:07.863 [RECV] at 14881134 2 140 148 2 0 1 1 -1 0 0
13:43:07.876 [RECV] c_map 0 2 1
```
