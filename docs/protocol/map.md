# Map & World Packets

## `c_map` (RECV)

Map change — client should load a new map.

**Format**: `c_map <unknown> <mapId> <unknown>`

*TODO: Capture examples by changing maps via portals*

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

## Map Navigation Flow (Expected)

1. Player walks to portal → `walk` SEND
2. Server sends map change → `c_map` RECV
3. Server sends new position → `at` RECV
4. Server sends all entities on new map → multiple `in` RECV
