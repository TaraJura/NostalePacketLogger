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

**Format**: `u_s <unknown> <targetType> <targetId> ...`

The client's builder concatenates the constant `u_s ` with numeric fields
(found in the binary at VA `0x72D630`; the short `u_s` form is at `0x72D640`).
The exact field order is not verified on the live server yet.

`ntpl cast` defaults to `u_s <skillVnum> <targetType> <targetId>` and can send
a literal packet with `--packet` once the live client shows the real layout:

```bash
linux/scripts/host.sh cast 866 --target-id 5180 --target-type 3
linux/scripts/host.sh cast --packet "u_s 866 3 5180"
```

The character must be **standing** (sitting blocks actions), the target must be
in range, and the server confirms a successful cast with an `su` packet. The
client does not echo its own `u_s` reliably, so `su` is the confirmation.

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

Verified so far: the server sends `su` for every hit (live examples above),
and the engine parses attacker/target/skill. The injected `u_s` path still
needs one live cast to lock the exact field order.
