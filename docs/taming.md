# Pet Trainer tab

The **Pet Trainer** tab (ninth GUI page) automates the Pet Trainer specialist
card (item 8569/8580) end to end: tame monsters into the pet slots, train the
tamed pets in the Miniland on the summoned sparring monster, extract their
essence (which releases them and frees the slot) and start taming again.
Everything is the client's own gameplay packets — there is no client hack, but
it is automation and the game rules prohibit that.

The tab has its own 50 ms timer, independent of the Map/Bot bot. Settings are
per client at `~/.config/ntpl/tamer-<winpid>.json` (`NTPL_TAMER_CONFIG`
overrides; `tamer.json` before a client is attached).

## Prerequisites

- The **Pet Trainer Specialist Card** equipped (skill class 76). The live card
  (8580) gives: **Whip** 1786 (the basic attack on slot 0), **Tame** 1787
  (range 2, cast 1.0 s, cooldown 15 s), **Advanced Tame** 1791 (30 s),
  **Summon Weak Sparring Monster** 1788 (self, cast 2 s, cooldown 30 s),
  **Extract Essence** 1789 (range 2, cast 0.6 s, cooldown 2 s).
- Skill-bar slots: the live test used **1** for Tame, **2** for the summon and
  **3** for Extract Essence; all three are configurable.
- The character standing (the tab stands it up).

## Taming (phase `tame`)

The server only tames a monster that is **below 50% HP**, with the character
**higher level** than the monster; Tame handles 1-star pets (100% once
eligible per nosapki), Advanced Tame (1791, with a Lure) 2-star pets. The live
session tamed chickens at **1/156 HP**; the tab stops hitting at its HP limit
(default 25%, already below the server's 50%) and then casts Tame:

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
RECV out 3 3039                                the tamed monster despawns
```

`sc_p <slot> <vnum> <entityId> …` is the pet/partner panel (live slots went
past 7; slots are expandable); `p_clear` empties it before the server
repopulates it. The engine keeps `WorldState.pets` (slot → vnum) and
`WorldState.pet_entities` (slot → entity id).

The `msgi` keys the tab reacts to (Game18N):

| Key | Meaning | Tab action |
|---|---|---|
| 2614 | Taming successful | count it, pick the next monster |
| 1887 | You have reached the maximum number of pet slots | End to end: switch to training; otherwise stop |
| 2631 | Taming failed | retry another monster, stop after 3 |
| 2623 | Monsters cannot be tamed with this skill | stop (wrong vnum or slot) |

The 15 s Tame cooldown is padded to **16 s** (a cast exactly at the boundary
was refused live 2026-11-27 01:40:34). A press the client refuses sends no
`ct`; that is not counted as a failure and is retried in about a second.

## Miniland training (phase `train`)

Live flow (2026-11-27 02:36, client PID 836, map 20001):

```text
SEND u_s 2 1 7001            Summon Weak Sparring Monster (slot 2, self)
RECV ct 1 7001 1 7001 14 8153 1788
RECV in 3 1497 1506236 11 10 …          the sparring monster (vnum 1497)
SEND suctl 20001 1 1502284 3 1506236    pet 1502284 attacks the monster
SEND suctl 20001 1 1502199 3 1506236    … one pair per pet, repeated
SEND suctl 20001 2 1503472 3 1506236 1504173 3 1506236
RECV msgi 0 2618 4 2 0 0 0              the pet's star rating went up
```

`suctl <map> <count> <petId> <targetType> <targetId> …` is the client's own
"attack with the mate" packet; the server accepts several pairs in one packet
(the live client sent up to 10). The tab sends all tamed monsters of the
configured vnum every 1.5 s. The live training lasted about **8 minutes**
between summons and the extraction (the default **Train minutes**); the star
rating message (2618) marks progress.

If the character is not in the Miniland, the tab walks to the **portal** —
live on **map 1 at (49,133)** — and waits for the map change (stepping on the
tile sends `preq`; the server answers `at … 20001` + `c_map 0 20001 1`). All
three portal values are configurable; a character on another map waits for
the user.

## Extraction (phase `extract`)

After the training time the tab casts Extract Essence on each tamed pet:

```text
SEND u_s 3 2 1502346         Extract Essence (slot 3, target type 2 = pet)
RECV ct 1 7001 2 1502346 21 8156 1789   cast 0.6 s, cooldown 2 s
RECV msgi 0 2627 0 0 0 0 0              "essence received and animal released"
```

The released pet frees its slot (`p_clear` + `sc_p`); with no tamed pets left
the tab goes back to taming — the full E2E loop. Extraction needs the pet at
the **maximum training level**: `msgi 0 2625` / `2639` mean it is not maxed;
the tab trains another round (up to 3) and then stops asking for a longer
**Train minutes**.

## Controls

| Control | Meaning |
|---|---|
| **Start / Stop** | Run or stop the tab's own state machine. |
| **Monster vnum** | Template to tame, default **333** (Chicken); the label shows the resolved name. |
| **Tame slot** | Skill-bar slot with Tame, 1–9, default **1**. |
| **Hit until HP ≤** | Stop hitting at this HP percentage, 5–50, default **25**%. |
| **End to end** | After the pet slots fill, train in the Miniland and extract, then tame again. Off: the tab stops at the max-slots message. |
| **Miniland map** | The training map, default **20001**. |
| **Portal** | Map/x/y of the Miniland entrance, default **1 / 49 / 133**. |
| **Summon slot** | Skill-bar slot with Summon Weak Sparring Monster, default **2**. |
| **Sparring vnum** | The summoned monster's template, default **1497**. |
| **Extract slot** | Skill-bar slot with Extract Essence, default **3**. |
| **Train minutes** | Training time before extraction, 1–120, default **8**. |
| **Extract essence after training** | Cast Extract Essence on each pet (releases it). Off stops after training. |
| **pet slots / chickens / tamed / extracted** | Live line of the tab. |

## Limits

- The Pet Trainer card must be equipped and the slots configured; a refused
  cast or a missing result is reported in the tab status and the Bot log.
- **DLL 0.3.3 or newer** for the client-side monster selector (`TARGET`). On
  client 0.9.3.3262 older DLLs answer `client build mismatch`; the host falls
  back to injected `ncif`, which was enough for the live tamer but does not
  set the client's own selection (attacks may be refused).
- Extraction only works on a max-training-level pet; the tab retries the
  training up to 3 rounds and then asks for more **Train minutes**.
- Automation violates the game rules. The account risk belongs to the owner.

## Tests

- `test_engine.py` — `st` HP/max tracking, the death notice, `sc_p` /
  `pet_entities` / `p_clear`, the `msgi` key/counter and the `ct` cast log.
- `test_tamer.py` — the suctl packet, the config defaults/clamps and its
  round trip.
- `gui_tame_smoke.py` — a fake server in the Miniland: three chickens, two
  tames, `msgi 0 1887` switches to training, the summon spawns vnum 1497, the
  tab sends `suctl 20001 2 …` for both pets, the shortened training passes,
  both extract casts get `msgi 0 2627` and the tab goes back to taming.
