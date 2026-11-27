# Pet Trainer tamer

The Map/Bot page's **Pet tamer** frame automates the **Pet Trainer**
specialist card (item 8569/8580): it finds the chosen monster (Chicken, vnum
333, by default), whips it down to the configured HP limit and uses **Tame**
on it, then repeats. Everything is the client's own gameplay packets — there
is no client hack, but it is automation and the game rules prohibit that.

## Prerequisites

- The **Pet Trainer Specialist Card** equipped (skill class 76). The card's
  skills: **Whip** 1786 (the basic attack on slot 0), **Tame** 1787 (range 2,
  cast 1.0 s, cooldown 15 s), Advanced Tame 1791 (30 s), Summon Weak Sparring
  Monster 1788, Extract Essence 1789.
- **Tame on a skill-bar slot.** The live test had it on slot 1; the frame's
  **Tame slot** spin defaults to 1. Slot 0 is always the card's basic attack.
- The character standing (the tamer stands it up) and the Pet Trainer card
  giving Whip range 2.

## Live flow (2026-11-27, client PID 836, char root1 7001)

The server only tames a monster that is **below 50% HP**, with the character
**higher level** than the monster; **Tame** handles 1-star pets (100% once
eligible per nosapki), **Advanced Tame** (1791, with a Lure) 2-star pets.
The live session tamed chickens at **1/156 HP**; the tamer stops hitting at
its HP limit (default 25%, already below the server's 50%) and then casts
Tame:

```text
SEND u_s 0 3 3039            basic attack / Whip (slot 0)
RECV st 3 3039 1 0 100 100 156 10 156 10 0   100% HP
RECV st 3 3039 1 0 1 100 1 10 156 10 0        1 HP
SEND u_s 1 3 3039            Tame (slot 1)
RECV ct 1 7001 3 3039 20 8153 1787             cast started, 1.0 s
RECV msgi 0 2614 0 0 0 0 0                     "Taming successful."
RECV ctl 2 1453987 3 0                         the new pet is controllable
RECV p_clear                                   the pet panel is rebuilt
RECV sc_p 7 333 1453987 ...                    the pet occupies slot 7
RECV sc_p 6 333 1452945 ...                    (the other chickens stay)
RECV out 3 3039                                the tamed monster despawns
```

The `msgi` keys the tamer reacts to (Game18N):

| Key | Meaning | Tamer action |
|---|---|---|
| 2614 | Taming successful | count it, pick the next monster |
| 1887 | You have reached the maximum number of pet slots | stop with a clear message |
| 2631 | Taming failed | retry another monster, stop after 3 |
| 2623 | Monsters cannot be tamed with this skill | stop (wrong vnum or slot) |

`sc_p <slot> <vnum> <entityId> …` is the pet/partner panel (live slots 0–7;
`p_clear` empties it before the server repopulates it after a tame). The frame
shows the occupied slots and the run's tame count.

## Controls

| Control | Meaning |
|---|---|
| **Tame monsters** | Enables tamer mode. The Path **Start**/**Stop** buttons run it; with it on, the bot hunts the chosen monster instead of its normal combat/path logic. |
| **Monster vnum** | Template to tame, default **333** (Chicken). |
| **Tame slot** | Skill-bar slot with Tame, 1–9, default **1**. |
| **Hit until HP ≤** | Stop hitting at this HP percentage, 5–50, default **25**%. |
| **pet slots / tamed** | Live line: occupied pet slots and monsters tamed this run. |

The settings are saved in the bot JSON as `tame`, `tame_vnum`, `tame_slot`
and `tame_hp_pct`.

## Behaviour

Per tick (50 ms) the tamer:

1. Consumes the server's `msgi` answer to a cast; nothing else counts as a
   result. An unrelated message is ignored.
2. Waits for a pending walk to be confirmed by the position or the server's
   `mv`.
3. Keeps the current target while it lives, matches the vnum and is within
   15 cells; otherwise picks the nearest one.
4. Walks into the Tame range (2 cells) through the map A* when the map data
   is loaded, or a three-cell client `MoveTo` leg on a map without it
   (Miniland).
5. In range, uses **Whip** (slot 0) at the skill's own pace until the
   target's `st` HP is at or below the limit. During the 15 s Tame cooldown
   it pre-damages the next chicken.
6. Casts **Tame** and waits for the result. The next target can be damaged
   during the cooldown; only Tame is gated by it.

It stops on Stop, the max-pet-slots message, three failed tames, three casts
with no answer, or a skill that cannot tame the monster (with a message and
Bot-log line for each). Pet slots can be expanded in game (the server's
"permanently increase your pet slots" item); the tamer only reports what the
server says.

## Limits

- The Pet Trainer card must be equipped and Tame must be on the chosen slot;
  otherwise the server sends no taming result and the tamer stops with
  "Tame got no answer".
- The HP limit only decides when the tamer stops hitting. The server decides
  whether the tame is allowed: the monster must be below 50% HP and the
  character a higher level (Tame = 1-star pets; Advanced Tame 1791 with a
  Lure = 2-star).
- Automation violates the game rules. The account risk belongs to the owner.

## Tests

- `test_engine.py` — `st` HP/max tracking, the death notice, `sc_p`/`p_clear`
  and the `msgi` key/counter.
- `test_botconfig.py` — `tame` defaults and clamping.
- `gui_tame_smoke.py` — a fake server with two chickens: the tamer whips the
  first to 20% HP, Tames it (2614 + `ctl`/`sc_p`), whips and Tames the
  second, gets `msgi 0 1887` and stops with "pet slots are full".
