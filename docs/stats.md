# Stats tab: player DPS meter

The **Stats** tab (page 8) turns the server's combat broadcasts into a live
**DPS meter for players**.

## What is counted

Only `su` packets (RECV) with attacker type **1** (player characters) and
positive damage. Partner/monster attackers are excluded, and zero-damage
skills (buffs, heals) add nothing. This includes the local character and the
other players whose hits the server broadcasts to this client (usually
everyone in view).

Live fields (2026-11-23):

```
su <attackerType> <attackerId> <targetType> <targetId> <skill> ... \
   <hpPercent> <damage> ... <targetHp> <targetHpMax>
      args[0]       args[1]       args[2]     args[3]     args[4]   args[11]  args[12]   args[15]   args[16]
```

Example: `su 1 7001 3 58489 934 60 25 3803 0 0 1 7 5852 5 0 10736 149353`
— player 7001 dealt **5,852** to monster 58489, which is left at
**10,736 / 149,353 HP** (7 %). The neighbouring packet for the same monster
showed 10,736 HP, and 10,736 + 5,852 = 16,588 is the earlier HP, so the
damage field is confirmed.

Older (2026-11-06) `su` captures had a different tail; a sample is only
accepted when the packet has at least 17 numeric fields.

## The table

Columns: **Player**, **DPS**, **Damage**, **Hits**, **Share**, **Last hit**,
**Session**. Rows are sorted by DPS, highest first.

- **Window**: 5 s, 10 s (default), 30 s, 60 s or **Session**.
- Windowed DPS = damage in the window ÷ window seconds.
- Session DPS = total damage ÷ time since the first counted hit (or Reset).
- **Share** is the row's part of the window damage.
- **Session** is the total damage since the last Reset; session totals
  survive the rolling window.
- **Reset** clears every total and starts over.
- The table refreshes twice a second while the tab is visible; the meter
  keeps collecting while another tab is open.
- Names come from the player `in 1` spawn packets; a player who spawned
  before the GUI attached may show as `char <id>` until their next spawn.
  Names are cached, so a row keeps its name after the player leaves.

## Limits

- The client only knows hits the server broadcasts to it.
- Damage is the server's per-hit value; overkill is included (the target HP
  can reach 0 while the damage number is larger).
- Damage that arrives without a `su` (some passive/DoT ticks) is not counted.
- The row of a player who left the map stays until the window ages out or
  Reset is pressed.
- The meter is host-side and read-only: it never sends a packet.

## Proof

`linux/host/tests/test_dpsmeter.py` covers the sample decoding, the
window/session math, totals and Reset. `linux/test/gui_stats_smoke.py` runs
the real window against a fake server with the self character and a second
player, a partner hit and a zero-damage buff that must be ignored, name
resolution, the Session window and Reset.
