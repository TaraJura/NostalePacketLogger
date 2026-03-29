# Unknown / Undocumented Packets

Packets we've seen in logs but haven't fully documented yet. When investigating, move them to their proper category file.

## How to Investigate

1. Have the user perform a specific action in-game
2. Read the packet log before and after
3. Correlate the new packets with the action
4. Document format, fields, and behavior

## Seen But Not Yet Documented

*Add packets here as we encounter them during gameplay observation*

| Opcode | Dir | Context | Raw Example |
|--------|-----|---------|-------------|
| `gidx` | RECV | On spawn/map load | `gidx 1 4165102 682.916 Fallen_Angels 5 0\|0\|0` |

---

## Investigation Queue

- [ ] `gidx` — appears on map load, seems related to family/guild info
- [ ] Walk packet checksum — the 3rd field in `walk` alternates 0/1, need to understand the algorithm
- [ ] `cond` unknown fields — what do the middle fields mean?
