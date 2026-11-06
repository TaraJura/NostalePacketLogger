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

**Format**: `u_s <skillVnum> <targetType> <targetId> ...`

The client's builder concatenates the constant `u_s ` with numeric fields
(found in the binary at VA `0x72D630`; the short `u_s` form is at `0x72D640`).
Real client packets captured by the skill hook on 2026-11-06:

```
u_s 0 3 5160
    — normal/basic attack on monster 5160; the server answered
      su 1 14881134 3 5160 866 ...
u_s 10 3 5161
    — a different (cast) skill; the server answered su 1 14881134 3 5161 876 ...
```

So **skill 0 is the normal auto-attack**; the server reports the character's
actual basic-attack skill (866 here) in `su`. Skill 866 is not a castable
`u_s` skill: sending `u_s 866 ...` is answered with `cancel 0 866 -1`. The
server also sends `cancel 0 <skill> -1` when a cast skill is not usable.
`<skill> <targetType> <targetId>` is the verified prefix; later fields are
still unknown.

Target types observed or used by the bot:

| targetType | targetId | Meaning |
|---|---|---|
| 3 | monster `entityId` | monster/normal attack or offensive spell |
| 1 | own `charId` | self-target spell/buff (protocol-consistent shape; one benign live cast still recommended) |

The GUI's ordered **Spells** rules use this format: monster rules send
`ncif 3 <entityId>` then `u_s <spell> 3 <entityId>`; self rules send
`u_s <spell> 1 <charId>`. The first enabled rule whose My HP/MP condition is
true (and whose per-rule cooldown is over) wins; otherwise the Combat Skill
is used. See [../bot-config.md](../bot-config.md).

`ntpl cast` defaults to `u_s <skillVnum> <targetType> <targetId>`:

```bash
linux/scripts/host.sh cast 0 --target-id 5160 --target-type 3   # normal attack
linux/scripts/host.sh cast 10 --target-id 5161 --target-type 3  # other skill
linux/scripts/host.sh cast --packet "u_s 0 3 5160"
```

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
path is therefore confirmed. Self-target `u_s <spell> 1 <charId>` remains to
be verified with one benign live cast.
