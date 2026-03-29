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

## `cond` (RECV)

Entity condition/movement state update.

**Format**: `cond <entityType> <entityId> <unknown> <unknown> <speed>`

**Examples**:
```
cond 1 14637447 0 0 10
    — Player 14637447 condition update, speed 10
```

*TODO: Decode the unknown fields (possibly buff/debuff flags, movement lock)*

---

## `lev` (RECV)

Level and experience information.

**Format**: `lev <level> <jobLevel> <jobXP> <xpMax> <jobXPMax> <reputation> <unknown> ...`

*TODO: Capture examples by leveling up or checking status*
