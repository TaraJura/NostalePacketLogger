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
| `toria` | SEND | Bushi King Defence raid (map 9307, 2026-11-23): one hit on a Bushi; the raid sends no `u_s`. Preceded by `ncif 3 <id>` | `toria 3 2490820` |
| `toria_o` | RECV | Bushi King Defence raid start, with `evnt` and `toris_o` | `toria_o 1266` |
| `toris_o` | RECV | Bushi King Defence raid start | `toris_o 1267` |
| `trboss` | RECV | Bushi King Defence raid: HP of the Bushi King (vnum 839) at (6, 36), every ~10 s: `trboss 3 <id> <hp> <maxHp>` | `trboss 3 2490803 193262 193262` |
| `tbboss` | RECV | Bushi King Defence raid: HP of the Bushi King at (66, 36), same format | `tbboss 3 2490802 193262 193262` |
| `evnt` | RECV | Bushi King Defence raid start (timer?) | `evnt 1 0 -48000 -48000` |

---

## Investigation Queue

- [ ] `gidx` — appears on map load, seems related to family/guild info
- [x] Walk packet checksum — solved 2026-11-06: it is `1 if (x + y) % 3 == 1 else 0`; the server rejects the wrong value. See [movement.md](movement.md).
- [ ] `cond` unknown fields — what do the middle fields mean?
