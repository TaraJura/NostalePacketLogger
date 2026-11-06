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
| Only selected | attack only monsters whose template vnum is selected |
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
resolved is skipped; in "all except selected" it is allowed because it cannot
be on the exclusion list.

## Spells — conditional cast rules

Each rule has:

| Field | Meaning |
|---|---|
| enabled | rule participates in selection |
| spell | spell vnum sent in `u_s`; 0 is skipped (basic attack is the fallback, not a rule) |
| subject | **My HP %** or **My MP %** (from the server's `stat` packet) |
| comparator | **below** = strictly lower than the threshold, **above** = strictly higher |
| threshold | percentage, 0–100 |
| cooldown | minimum seconds before this rule can fire again |
| target | **Monster** (nearest/Retaliate target) or **Self** (own char id) |

Rules are evaluated **top to bottom** every bot tick; the first enabled rule
that is off cooldown and whose condition is true wins. Up/down arrows change
priority; the description next to the spell vnum comes from the client's
`_code_<lang>_Skill.txt` (tooltip text, not necessarily a display name).

Casting still obeys the global **Cooldown** in Combat and sets the same walk
pause as a normal attack. Monster rules require a monster in range and send:

```
ncif 3 <entityId>
u_s <spell> 3 <entityId>
```

Self rules require the char id and send:

```
u_s <spell> 1 <charId>
```

The monster shape (`u_s <spell> 3 <id>`) matches the live client captures.
The self-target shape (target type 1 + char id) is the protocol-consistent
guess; verify one benign self cast live before relying on a timed buff/heal.

If no rule matches, the bot uses the **Skill** field from Combat (0 = normal
attack). Recovery runs before spell rules: below **Auto-recover → Below HP %**
the bot sits and waits for full HP, so a heal rule with a lower threshold does
not fire while recovery is active. Spell rules require **Auto-attack** to be
on.

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
  "spell_rules": [
    {
      "enabled": true,
      "spell": 7,
      "subject": "my_hp",
      "comparator": "below",
      "threshold": 50.0,
      "cooldown": 10.0,
      "target": "monster"
    },
    {
      "enabled": true,
      "spell": 20,
      "subject": "my_mp",
      "comparator": "above",
      "threshold": 80.0,
      "cooldown": 60.0,
      "target": "self"
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
- `monsternames.py` — `load_skill_descriptions` for the rule hint.
- `linux/host/tests/test_botconfig.py` — schema round trip, clamping, errors.
- `linux/test/gui_bot_rules_smoke.py` — fake server with two monsters: filter
  selects vnum 200 and ignores the nearer vnum 201; HP 40 % triggers a spell
  rule (`u_s 7 3 500`), no basic attack; save/load restores the configuration.
