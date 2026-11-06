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

*TODO: Capture examples*

---

## Map IDs (Known)

*TODO: Build this list by visiting different maps*

| Map ID | Name | Notes |
|--------|------|-------|
| | | |

---

## Map Navigation Flow (Captured)

1. Player walks into a portal → client sends `walk`; server teleports.
2. Server sends the leaving `c_map 0 <oldMap> 0`, then `mapout`.
3. Server sends `at <charId> <newMap> <x> <y> ...` with the new position.
4. Server sends the entering `c_map 0 <newMap> 1`.
5. Server sends the new map's entities as `in`/`mv` packets.

The GUI/engine switch on the `at`/`c_map` pair; stale entities from the old
map are dropped on `c_map` and `mapout`.
