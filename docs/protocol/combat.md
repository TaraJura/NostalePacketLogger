# Combat Packets

## `su` (RECV)

A skill was used / damage was dealt. One of the most important packets for combat.

**Format**: `su <attackerType> <attackerId> <targetType> <targetId> <skillVnum> <unknown> <unknown> <unknown> <unknown> <damage> <unknown> <unknown> <unknown> <hpPercent> ...`

**Live examples** (2026-11-06):
```
su 1 14877915 3 5180 866 9 11 4401 0 0 0 0 6507 0 0 0 4140
su 1 14877915 3 5179 866 9 11 4401 0 0 0 0 5720 0 0 0 4140
    — player 14877915 used skill 866 on monster 5180/5179
```
The first five fields (attacker, target, skill) are parsed by the engine.
Damage/HP positions are not decoded yet; the engine keeps the raw args.

**Notes**:
- Sent for every hit in combat
- Contains attacker, target, skill used, and damage dealt
- HP percentage of target after hit is included

---

## `u_s` (SEND)

Use a skill on a target.

**Format**: `u_s <skillSlot> <targetType> <targetId> ...`

The first field is a **zero-based slot in the character's learned skill list**
(the `ski` packet), not the Skill.dat vnum. The server reports the resolved
skill vnum in `su`.

The client's builder concatenates the constant `u_s ` with numeric fields
(found in the binary at VA `0x72D630`; the short `u_s` form is at `0x72D640`).
Real client packets captured by the skill hook on 2026-11-06:

```
u_s 0 3 5160
    — normal/basic attack on monster 5160; the server answered
      su 1 14881134 3 5160 866 ...
u_s 10 3 5161
    — Holy Hammer (slot 10); the server answered
      su 1 14881134 3 5161 876 ...
u_s 7 1 14881134
    — Group Healing (slot 7, self); the server answered
      su 1 14881134 1 14881134 873 ...
```

Live slot map for character 14881134 (from
`ski 0 866 866 866 867 868 869 870 871 872 873 874 875 876 ...`, duplicates
collapsed in order):

| `u_s` slot | Skill.dat vnum | name |
|---|---|---|
| 0 | 866 | Holy Attack / basic attack |
| 1 | 867 | Healing |
| 2 | 868 | Holy Light |
| 7 | 873 | Group Healing |
| 8 | 874 | Holy Weapon |
| 9 | 875 | Blessing |
| 10 | 876 | Holy Hammer |

So **slot 0 is the normal auto-attack**; the server reports the character's
actual basic-attack skill (866 here) in `su`. Sending the vnum instead of the
slot (`u_s 866 ...`) is answered with `cancel 0 866 -1`. The server also sends
`cancel 0 <slot> -1` when a cast is not usable. The
`<skillSlot> <targetType> <targetId>` prefix is verified; later fields are
still unknown. The bot stores Skill.dat vnums and resolves them to slots via
the `ski` list (live or backfilled from the DLL log).

Target types observed or used by the bot:

| targetType | targetId | Meaning |
|---|---|---|
| 3 | monster `entityId` | monster/normal attack or offensive spell |
| 1 | own `charId` | self-target spell/buff; live `u_s 7 1 14881134` → `su ... 873` |

The GUI's ordered **Spells** rules use this format: monster rules send
`ncif 3 <entityId>` then `u_s <spell> 3 <entityId>`; self rules send
`u_s <spell> 1 <charId>`. Rules run top to bottom; the first enabled rule
whose condition (Always, My HP % or My MP %) is true, whose cooldown is over,
and which passes the scanned MP/range checks is cast; otherwise the Combat
Skill is used. See [../bot-config.md](../bot-config.md).

### Spell metadata (`NSgtdData.NOS` → `Skill.dat`)

The client's `Skill.dat` provides the per-spell data the bot uses before a
cast. Records are nibble-coded text separated by `#====...`; the relevant
fields are `VNUM`, `NAME` (a `zts<key>e` string-table key), `TYPE[0]` (1 =
active), `TARGET` (target type, hit type, range) and `DATA` (cast time,
cooldown, MP cost; deciseconds). Live examples:

| vnum | name | TYPE[0] | target type | MP | cast | cooldown | range |
|---|---|---|---|---|---|---|---|
| 866 | Holy Attack | 1 | 0 (monster/any) | 0 | 0.2 s | 0.9 s | 11 |
| 867 | Healing | 1 | 0 (monster/any) | 50 | 0.2 s | 3 s | 10 |
| 869 | Mana Shield | 1 | 1 (self) | 300 | 1.0 s | 60 s | 0 |
| 876 | Holy Hammer | 1 | 0 (monster/any) | 145 | 0.7 s | 90 s | 11 |

`TARGET` type 1 is self, 2 is an ally/party target (not addressed by the bot
yet; it falls back to self), and 0 is an entity/monster target. The readable
names come from `_code_<lang>_Skill.txt` via the `NAME` key, not from the vnum
itself. See `linux/host/ntpl/spells.py`.

`ntpl cast` defaults to `u_s <slot> <targetType> <targetId>`; pass the slot,
not the Skill.dat vnum:

```bash
linux/scripts/host.sh cast 0 --target-id 5160 --target-type 3   # normal attack
linux/scripts/host.sh cast 10 --target-id 5161 --target-type 3  # Holy Hammer
linux/scripts/host.sh cast 7 --target-id 14881134 --target-type 1  # self Group Healing
linux/scripts/host.sh cast --packet "u_s 0 3 5160"
```

The GUI **Skill** field accepts a Skill.dat vnum and maps it to the slot when
the `ski` list is known; 0 stays the basic attack. The spell rules always store
the vnum and resolve the slot before sending.

The character must be **standing** (sitting and recovery block actions), the
target must be in range, and the server confirms a successful cast with an
`su` packet. The client does not echo its own `u_s` reliably in the packet
stream, so `su` is the confirmation; the skill hook does capture the client's
own builder.

---

## `ncif` (SEND)

Select/target an entity (click on it).

**Format**: `ncif <entityType> <entityId>`

`ntpl cast` sends this automatically before `u_s` when `--target-id` is given;
use `--no-select` to skip it. The client string is in the binary at VA
`0x730370` (`ncif `).

---

## Combat Flow (Expected)

1. Player clicks enemy → `ncif` SEND (select target)
2. Player uses skill → `u_s` SEND (use skill)
3. Server responds → `su` RECV (damage dealt, HP update)
4. If enemy dies → `out` RECV (entity despawned)
5. Loot drops → `in` RECV (drop entity spawned) or `drop` RECV

Verified live: the server sends `su` for every hit; the engine parses
attacker/target/skill; `u_s 0 3 <id>` is the normal attack and produced
`su ... 866/868 ...`; skill 10 produced `su ... 876 ...`. The injected `u_s`
path is therefore confirmed. The self-target shape
`u_s <slot> 1 <charId>` is also live-captured (`u_s 7 1 14881134` → `su ...
873`, Group Healing).
