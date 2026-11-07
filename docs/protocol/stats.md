# Stats & Status Packets

## `stat` (RECV)

Character HP, MP, and XP update.

**Format**: `stat <hp> <hpMax> <mp> <mpMax> <unknown> <xp>`

| Field | Type | Description |
|-------|------|-------------|
| hp | int | Current health points |
| hpMax | int | Maximum health points |
| mp | int | Current mana points |
| mpMax | int | Maximum mana points |
| unknown | int | Unknown (0 observed) |
| xp | int | Current experience points |

**Examples**:
```
stat 2991 2991 3727 3727 0 99328
    — Full HP (2991/2991), full MP (3727/3727), 99328 XP
```

**Notes**:
- Sent frequently — on any HP/MP/XP change
- Also sent periodically as a heartbeat

---

## `st` (RECV)

Entity status, used by the server to report monster HP. Observed live on
2026-11-07 (map 3); the client does not send `out` for a killed monster, so
this packet is the only death notice.

**Format**: `st <entityType> <entityId> <level> <unknown> <state> <unknown> <hp> <unknown> <hpMax> <unknown> <unknown>`

**Live examples**:
```
st 3 3481 14 0 100 100 481 108 481 108 0   # alive: current HP 481
st 3 3481 14 0 1 100 0 108 481 108 0       # dead:  current HP 0
```

| Field | Observed |
|-------|----------|
| entityType | `3` for monsters |
| entityId | matches `mv`/`in` |
| level | 14 or 15 for the map-3 monsters |
| state | `1` on the dead packet, `100` while alive (inferred) |
| hp | current HP; **0 means the monster died** |
| hpMax | matches the alive `hp` |

The host uses only the type, entity id and `hp` fields: a zero current HP
removes the entity from `WorldState.entities`, so the bot stops selecting a
dead monster.

---

## `rest` (SEND and RECV)

Sit/stand toggle and its server state.

**SEND** (client → server, the C key in the game):
```
rest 1 1 <charId>          — toggle the rest state
```
The server toggles on every packet: two identical packets in a row produce
stand then sit. Send it once per desired state change.

**RECV** (server → client):
```
rest <entityType> <charId> <state>
rest 1 14881134 0          — standing
rest 1 14881134 1          — sitting
```

Sitting regenerates HP/MP, but **movement is rejected while sitting**. Stand
up before sending a `walk`.

---

## `cond` (RECV)

Entity condition/movement state update.

**Format**: `cond <entityType> <entityId> <unknown> <unknown> <speed>`

**Examples**:
```
cond 1 14881134 0 0 10
    — Player 14881134 condition update, speed 10 (observed live)
```

*TODO: Decode the unknown fields (possibly buff/debuff flags, movement lock)*

---

## `lev` (RECV)

Level and experience information.

**Format**: `lev <level> <jobLevel> <jobXP> <xpMax> <jobXPMax> <reputation> <unknown> ...`

*TODO: Capture examples by leveling up or checking status*
