# Feature review and test matrix — 2026-11-06 (late session)

This page is the checklist for the current feature set: what exists, how it is
covered by automated tests, and what was actually exercised against the live
client. It complements the chronological notes in
[live-session-2026-11-06.md](live-session-2026-11-06.md) and the current-state
handoff.

Character used for the live pass: **14881134**, map **20**. At the end of the
pass the character was **3209/3209 HP**, standing, with no bot or test host
left running.

## Feature inventory

| Area | What it does | Automated coverage | Live status |
|---|---|---|---|
| Injector / DLL | Loads the 32-bit DLL into the main `NostaleClientX.exe` | hook stubs, fake-target injector test | injected under Wine; not re-run in this pass |
| recv/send hooks | Copies client-decrypted packets to the host | protocol/engine tests, fake-target end-to-end | `recv`, `send`, `skill` ACTIVE; drops 0 |
| TCP host protocol | `STATUS`, `RECV`, `SEND`, `POS`, `MOVE`, debug `SCAN/FIND/READ` | protocol/client tests | `status`, `POS`, `SEND`, `MOVE` exercised live |
| Packet log | `C:\NosTalePacketLog.txt` with all packets | used by spawn/skill backfill tests | live log read back for `in`/`ski` |
| World state | Map, position, entities, stats, rest, combat, chat, drops | 84 host tests | position, stats, entities, drops observed live |
| Map parser | `NStcData.NOS` terrain, 779 grids, A* ranking | synthetic NOS + local archive tests | map 20 used for all live pathing |
| Auto map switch | `c_map <type> <mapId> <state>` + `at`; second field is the map id | engine + GUI smoke replay of live 20↔85 capture | verified live earlier (20↔85) |
| Movement | `MOVE|x y` → client MoveTo → client `walk`; 8-direction A*; 3-cell legs; Euclidean pacing | A* diagonal/corner tests, bot smoke | `MOVE 48 187` → client `walk 48 187 1 10` |
| Sit/stand | `rest 1 1 <charId>` toggle | recover smoke | toggled live `rest … 1` then `rest … 0` |
| Basic auto-attack | `ncif 3 <id>` + `u_s 0 3 <id>`; Retaliate target first | bot + rules + out-of-range smokes | `su 1 14881134 3 5030 866 …`, damage 1056 |
| Move first | Attacks between path legs, keeps walking; off = clear area | bot smoke with Move first on | path+spell combo ran with it on |
| Target filters | All / Only selected / All except selected by vnum; unknown-vnum opt-in | rules smoke (allowed 200 vs nearer 201) + unknown toggle test | only-vnum-200 live test attacked 5030, no other target |
| Monster names | `NSlangData_UK.NOS` → `vnum → name`, 2,795 entries | text-codec/parser tests | mapping used for live entities (5030 vnum 200 etc.) |
| Tooltips | Own/player/monster/NPC/drop details; name, ids, distance | GUI smoke (incl. DLL-log backfill) | data path verified; visual on the live window |
| Spawn backfill | Entity vnum/player name from `[RECV] in` log lines | spawnlog tests + GUI smoke | used at GUI startup |
| Skill list | `ski` pages → deduplicated ordered list; `u_s` slots | engine + spawnlog tests, rules/spell-order smokes | live `ski` gives 866→slot 0 … 876→slot 10 |
| Spell catalog | `NSgtdData.NOS` → `Skill.dat`; 1,964 records, 791 active | spell parser + installed-data tests | names/MP/range/cooldown used live |
| Spell picker | Searchable learned-spell list; vnum + slot + metadata | rules smoke opens the real dialog | not visually opened on the live display |
| Spell rules | Always / my HP% / my MP%; order 1., 2., …; up/down | config round-trip + spell-order smoke | one rule (Mana Shield) ran in the live bot combo |
| Spell rotation | Always + per-rule/catalog cooldowns | spell-order smoke: 876 slot 10 → 866 slot 0 ×2 | not repeated live (single rule used) |
| Slot resolution | Stored vnum → `u_s` slot from `ski`/log | rules + order smokes | Blessing 875/slot 9, Group Healing 873/slot 7, Mana Shield 869/slot 3 live |
| Spell safety | MP cost, spell range, catalog cooldown, cast-time walk pause | rules smoke (MP/range gating) | Mana Shield cast only at full MP; Healing self-cancel observed |
| Out-of-range guard | Basic attack capped by Skill.dat range (866 = 11) | dedicated out-of-range smoke | bug observed live (35-cell target/self-hit) and fixed |
| Pickup | `get 1 <charId> <dropId>` for own/unowned drops in radius | bot smoke | live: drop 16830502 → `get … 0` + inventory update |
| Recovery | sit below HP%, wait full, stand | recover smoke | not forced live; no safe way without risking death |
| Config save/load | Validated JSON of all settings and rule order | botconfig tests + rules smoke round-trip | methods tested headlessly; file dialogs not live-clicked |
| UI state | green/red toggles, recovery banner, responsive map, whole-map view | GUI smoke checks classes + banner | visually verified in the headless screenshot |
| Safety | Stop, no detection evasion, loopback-only host | design rules/docs | one near-death event during live combo, healed to full |

## Automated tests (last run)

```bash
make -C linux host-test   # 84 tests: protocol, client, engine, map/A*, config,
                          # spawn/skill lists, monster/skill tables, spells
make -C linux gui-test    # GUI: rows, status, map switch, tooltips, toggle
                          # classes, recovery banner, unknown-position guard
make -C linux bot-test    # 6 smokes:
                          #   combat + path movement
                          #   targets + rules + config round-trip
                          #   spell order 876→866→866
                          #   out-of-range guard
                          #   rejected walks
                          #   sit/wait-full-HP recovery
```

## Live combinations tested

### Path + self spell + movement

Headless GUI bot against the live DLL: path `(49,187) → (52,187)`, Auto-attack,
Move first, Pickup, Auto-recover off, rule **Mana Shield 869 Always/Self**.

```
u_s 3 1 14881134                  # slot 3 = Mana Shield
su 1 14881134 1 14881134 869 ...  # accepted, MP -300
MOVE 52 187                       # client walk 52 187 0 12
```

The finite path ended at the destination and stopped the bot (expected; use
Loop or start with no path for stationary combat).

### Target filter + spell + auto-attack + recovery safety

Headless GUI bot, no path, target mode **Only selected vnum 200**, range 10,
Auto-attack + Retaliate + Move first + Pickup, Auto-recover threshold 75 %,
rule Mana Shield 869.

```
u_s 3 1 14881134                  # Mana Shield
su 1 14881134 1 14881134 869 ...
ncif 3 5030
u_s 0 3 5030
su 1 14881134 3 5030 866 ...      # 993 damage
su 1 14881134 3 5030 866 ...      # 710 damage
su 1 14881134 3 5030 866 ...      # 678 damage
su 3 5030 1 14881134 0 ...        # turtle hits back: HP 3209 → 2844
```

No other monster was targeted (the filter held). The script stopped at 9 s;
the turtle kept attacking after the bot stopped, so **stopping the bot does not
disengage**. HP reached 1177 before a Group Healing 873 cast landed and
restored 3209/3209. This is the “don’t die” incident: the character survived,
but future live combat tests must move away or finish the target before
stopping the bot.

### Pickup

The turtle dropped item 5965 (drop id 16830502) owned by the character:

```
[RECV] drop 5965 16830502 46 187 1 0 14881134
[SEND] get 1 14881134 16830502
[RECV] get 1 14881134 16830502 0
[RECV] ivn 10  2.5965.2.0
[RECV] sayi 1 14881134 12 975 2 5965 1 0 0
```

### Single-feature live checks

- **Spell slots:** slot 9 → `su … 875` (Blessing, MP 4401 → 4251); slot 7 →
  `su … 873` (Group Healing, HP 2761 → 3209); slot 1 (Healing 867) →
  `cancel 0 1 -1` because 867 is target type 0 and not self-castable.
- **Basic attack:** `u_s 0 3 5030` → `su … 3 5030 866 …`, not a self hit.
- **Sit/stand, POS, MoveTo** as listed in the table.
- **Out-of-range bug:** earlier live packets showed `ncif/u_s 3 5087` while the
  bee was 35+ cells away; the server answered a self-target. Fixed by capping
  basic attacks at the scanned skill range (866 = 11) and adding a regression
  smoke.

## Not exercised live (headless only)

- Forced low-HP recovery (would require taking damage to the threshold).
- A portal crossing in this pass; map switching was verified live earlier in
  the day (20↔85).
- Clicking the Save/Load file dialogs; the `_save_config_to`/`_load_config_from`
  paths and validation are covered by tests.
- Opening the spell picker on the live display; the real dialog is opened and
  checked in the rules smoke.

## Safety rules reinforced

1. Never stop a live bot while a monster is attacking it; finish the target or
   move out of range first.
2. Keep Auto-recover enabled with a high threshold (≥75 %) during live combat
   tests, and stop the script if HP drops below ~70 %.
3. Group Healing 873 (slot 7) is the live-verified self heal; Healing 867 is
   not self-castable.
4. A finite path stops the bot at the last waypoint; use Loop or no path.
5. The host is loopback-only; only the main game process is touched.
