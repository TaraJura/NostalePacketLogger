# Bot configuration: targets, spells and export/import

The Map/Bot sidebar contains three configuration sections that are saved
together as JSON: **Targets** (which monsters to fight), **Spells** (conditional
cast rules) and **Configuration** (Save/Load). All of it is optional: with an
empty target list and no spell rules the bot behaves as before (attack every
monster in range, use the Skill from Combat).

## Targets — which monsters to attack

The mode dropdown selects how the target list is interpreted:

| Mode | Behaviour |
|---|---|
| All monsters | attack every monster in range (default) |
| Only selected | attack only monsters whose template vnum is selected (or, with the unknown option, monsters whose vnum is unknown) |
| All except selected | attack every monster except the selected templates |

The list under the dropdown is searchable by monster name or vnum (max 60
matches). Click a checkbox to add/remove a template; selected monsters are
shown first when the search box is empty. **Add vnum** inserts a template that
is missing from the client's name table, **Clear** empties the selection.

The filter uses the monster **template vnum** (`in 3 <vnum> <entityId>`), not
the per-session entity id. This works for monsters that spawned before the GUI
attached because `spawnlog.py` backfills the vnum from the DLL log; see
[map.md](map.md#entities-that-spawned-before-the-host-attached).

Retaliate applies the same filter: a monster that is hitting you is ignored if
it is excluded. In "only selected" mode an entity whose vnum cannot be
resolved is skipped by default; in "all except selected" it is allowed because
it cannot be on the exclusion list.

**Also attack monsters with unknown vnum** handles the remaining gap: a
monster that was already alive when the DLL was injected has no `in` packet in
the log, so neither the live stream nor the log backfill knows its template.
The bot shows a status warning when such monsters are in range and no allowed
target was found; enable the checkbox to attack them anyway. This is also
saved as `attack_unknown` in the JSON configuration.

The UI makes toggle state explicit: active toggles (Loop, Auto-attack,
Retaliate, Move first, Pickup, Auto-recover) are **green**, inactive ones are
**red**. While Auto-recover is active and HP is below the threshold, a red
**RECOVERING** banner explains that attacks and movement are paused; the
recovery check runs before targeting and spell rules.

## Spells — scanned catalog, rules and execution order

![Spell rules and scanned metadata](assets/gui-spells.png)

The **Add spell…** button opens a searchable picker built from the game's own
`NSgtdData.NOS` → `Skill.dat`. Each entry shows the readable name/description
from `_code_<lang>_Skill.txt`, the vnum, MP cost, target mode and cooldown.
**Blank rule** adds an empty row for a manually entered vnum. Each rule also
has a `…` picker button to replace its spell.

The catalog records `Skill.dat` fields: `VNUM`, `NAME` (a `zts<key>e` text
key), `TYPE` (active/passive/system), `TARGET` (target type, hit type, range)
and `DATA` (cast time, cooldown, MP cost). Only `TYPE[0] == 1` records are
offered as active spells. Examples from the live client:

| vnum | name | `u_s` slot (this character) | MP | target | cooldown | cast |
|---|---|---|---|---|---|---|
| 866 | Holy Attack | 0 | 0 | monster | 0.9 s | 0.2 s |
| 867 | Healing | 1 | 50 | monster/any | 3 s | 0.2 s |
| 869 | Mana Shield | 3 | 300 | self | 60 s | 1.0 s |
| 873 | Group Healing | 7 | 250 | self/any | 20 s | 0.4 s |
| 876 | Holy Hammer | 10 | 145 | monster | 90 s | 0.7 s |
| 264 | Magic Missile | depends on class | 35 | monster | 20 s | 1.0 s |

**Important:** the first `u_s` field is the **zero-based slot in the
character's learned skill list**, not the Skill.dat vnum. The client sends
`u_s 10 3 <id>` for Holy Hammer and the server answers `su ... 876 ...`; the
live `ski 0 866 866 866 867 ... 876 ...` list gives the slot. The bot stores
the **vnum** in the rule (portable) and resolves it to the slot before
sending, using the live `ski` packet or the DLL-log backfill from startup
(`spawnlog.load_skill_list`). The rule text shows both the vnum and slot; the
picker lists the actual learned skills (or all active spells when no skill
list is known).

Each rule has:

| Field | Meaning |
|---|---|
| order | execution priority, shown as 1., 2., …; up/down arrows change it |
| enabled | rule participates in selection |
| spell | spell vnum sent in `u_s`; the picker fills it from the catalog; 0 is skipped |
| subject | **Always**, **My HP %** or **My MP %** (HP/MP from `stat`) |
| comparator | **below** = strictly lower than the threshold, **above** = strictly higher; disabled for Always |
| threshold | percentage, 0–100 |
| cooldown | minimum seconds before this rule can fire again; the spell's catalog cooldown is also enforced |
| target | **Monster** (nearest/Retaliate target) or **Self** (own char id); self-target spells from Skill.dat override this |

Rules are evaluated **top to bottom** every bot tick. The first enabled rule
that is off cooldown, in spell range, affordable in MP and whose condition is
true wins. With **Always**, the order + cooldowns form a rotation: rule 1
casts, is blocked for its cooldown, rule 2 casts, and so on. Up/down arrows
change priority; the number on the left shows the current order.

Data-driven checks before a cast:

- **range**: basic attacks use the Skill field's skill range (default basic
  attack 866 = 11 cells) even when the UI Range is larger. Out-of-range
  attacks are not sent; the status bar reports the distance and the bot walks
  the path instead. Use a longer-range spell rule or walk closer.
- **MP**: if `DATA[8] > current MP`, the rule is skipped (next rule may fit).
- **range**: a monster spell is skipped when the target is farther than
  `TARGET[2]` (`DATA[11]`) cells.
- **spell cooldown**: the rule's own cooldown is `max(rule.cooldown, catalog
  cooldown)`, so a 90 s spell is not re-cast every few seconds.
- **cast time**: the walk pause after the cast is at least `DATA[4]`.

Casting still obeys the global **Cooldown** in Combat. Monster rules require a
monster in range and send:

```
ncif 3 <entityId>
u_s <spell> 3 <entityId>
```

Self rules require the char id and send:

```
u_s <spell> 1 <charId>
```

The monster shape (`u_s <slot> 3 <id>`) and the self-target shape
(`u_s <slot> 1 <charId>`) both match live client captures. Live:
`u_s 10 3 5161` → `su ... 876` (Holy Hammer) and
`u_s 7 1 14881134` → `su 1 14881134 1 14881134 873 ...` (Group Healing).

Only spells whose Skill.dat target type is 1 default to the Self target in the
picker. The old name heuristic was wrong for **Healing 867** (target type 0,
range 10): a self cast is answered with `cancel 0 1 -1`. Use **Group Healing
873** (type 1) for a live-verified self heal; Healing 867 is for another
entity/party target and is not auto-selected as self.

If no rule matches, the bot uses the **Skill** field from Combat (0 = normal
attack). Recovery runs before spell rules: below **Auto-recover → Below HP %**
the bot sits and waits for full HP, so a heal rule with a lower threshold does
not fire while recovery is active. Spell rules require **Auto-attack** to be
on.

The catalog is loaded in a background thread at startup (Skill.dat decodes
about 1.2 MB); the picker shows a loading row until it is ready. If the file is
missing, the picker is empty and manual vnums still work.

## Save and load

**Save config…** writes every setting below to a JSON file; **Load config…**
reads one back. Loading stops a running bot (paths and cooldowns belong to the
old session), validates and clamps every value, and reports errors in the
status bar. Waypoints, the current path and live entity state are not saved.

Example:

```json
{
  "version": 1,
  "auto_attack": true,
  "retaliate": true,
  "move_first": true,
  "skill": 0,
  "skill_template": "u_s {skill} {type} {target}",
  "attack_range": 10,
  "attack_cooldown": 3.0,
  "pickup": true,
  "loot_range": 3,
  "recover": true,
  "low_hp_pct": 30.0,
  "loop": true,
  "monster_mode": "only",
  "monster_vnums": [200, 206],
  "attack_unknown": false,
  "spell_rules": [
    {
      "enabled": true,
      "spell": 876,
      "subject": "always",
      "comparator": "below",
      "threshold": 50.0,
      "cooldown": 90.0,
      "target": "monster"
    },
    {
      "enabled": true,
      "spell": 867,
      "subject": "my_hp",
      "comparator": "below",
      "threshold": 50.0,
      "cooldown": 3.0,
      "target": "self"
    },
    {
      "enabled": true,
      "spell": 866,
      "subject": "always",
      "comparator": "below",
      "threshold": 50.0,
      "cooldown": 1.0,
      "target": "monster"
    }
  ]
}
```

The format lives in `linux/host/ntpl/botconfig.py` (`BotConfig`, `SpellRule`,
`save_config`, `load_config`). Unknown fields are ignored; out-of-range numbers
are clamped; a non-object root or invalid JSON raises `ConfigError` instead of
touching the UI.

## Code and tests

- `botconfig.py` — dataclasses, validation, atomic save, load.
- `gui.py` — `_collect_config` / `_apply_config`, the Targets/Spells frames,
  `_monster_allowed`, `_select_spell_rule`, `_cast_spell`.
- `spells.py` — `NSgtdData.NOS` container reader, nibble decode, Skill.dat
  parser and `vnum → SpellInfo` catalog.
- `monsternames.py` — `load_skill_descriptions` provides the readable names.
- `linux/host/tests/test_botconfig.py` — schema round trip, clamping, errors,
  and preservation of spell-rule order/conditions.
- `linux/host/tests/test_spells.py` — parser, self target, known live spells.
- `linux/test/gui_bot_rules_smoke.py` — fake server with two monsters: filter
  selects vnum 200 and ignores the nearer vnum 201; HP 40 % triggers a spell
  rule (867 → slot 1, `u_s 1 3 500`), no basic attack; catalog/picker dialog,
  vnum→slot mapping, MP/range gating and save/load.
- `linux/test/gui_spell_order_smoke.py` — scanned spells 876 (Holy Hammer,
  90 s cooldown) and 866 (Holy Attack): 876 must cast first, then 866 twice
  while 876 is on cooldown; no raw vnums.
- `linux/test/gui_out_of_range_smoke.py` — UI Range 40, monster 35 cells
  away, basic skill range 11: no ncif/u_s is sent and the status shows the
  out-of-range target.
