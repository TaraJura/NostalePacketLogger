# Combat Packets

## `su` (RECV)

A skill was used / damage was dealt. One of the most important packets for combat.

**Format**: `su <attackerType> <attackerId> <targetType> <targetId> <skillVnum> <unknown> <unknown> <unknown> <unknown> <damage> <unknown> <unknown> <unknown> <hpPercent> ...`

*TODO: Capture examples during combat*

**Notes**:
- Sent for every hit in combat
- Contains attacker, target, skill used, and damage dealt
- HP percentage of target after hit is included

---

## `u_s` (SEND)

Use a skill on a target.

**Format**: `u_s <unknown> <targetType> <targetId> ...`

*TODO: Capture examples by using skills*

---

## `ncif` (SEND)

Select/target an entity (click on it).

**Format**: `ncif <entityType> <entityId>`

*TODO: Capture examples by clicking on entities*

---

## Combat Flow (Expected)

1. Player clicks enemy → `ncif` SEND (select target)
2. Player uses skill → `u_s` SEND (use skill)
3. Server responds → `su` RECV (damage dealt, HP update)
4. If enemy dies → `out` RECV (entity despawned)
5. Loot drops → `in` RECV (drop entity spawned) or `drop` RECV

*TODO: Verify this flow by fighting monsters*
