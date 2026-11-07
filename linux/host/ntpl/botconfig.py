"""Serialisable bot configuration: targets, spell rules and combat settings.

The GUI edits live widgets; this module is the pure, testable format used by
the Save/Load buttons. Values are validated and clamped on import so a
hand-edited or old file cannot crash the bot.

Schema (version 1)::

    {
      "version": 1,
      "auto_attack": true,
      "retaliate": true,
      "skill": 0,
      "skill_template": "u_s {skill} {type} {target}",
      "attack_range": 10,
      "attack_cooldown": 0.0,
      "pickup": true,
      "loot_range": 3,
      "recover": true,
      "low_hp_pct": 30,
      "loop": true,
      "monster_mode": "only",       # all | only | except
      "monster_vnums": [200, 206],
      "attack_unknown": false,      # only mode: allow vnum 0
      "spell_rules": [
        {
          "enabled": true,
          "spell": 10,
          "subject": "always",      # always | my_hp | my_mp
          "comparator": "below",    # below | above
          "threshold": 50,
          "cooldown": 5,
          "target": "monster"       # monster | self
        }
      ]
    }
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_VERSION = 1

MONSTER_MODES = ("all", "only", "except")
SUBJECTS = ("always", "my_hp", "my_mp")
COMPARATORS = ("below", "above")
SPELL_TARGETS = ("monster", "self")

MAX_MONSTER_VNUMS = 5000
MAX_SPELL_RULES = 100


class ConfigError(ValueError):
    """The file is not a readable bot configuration."""


@dataclass
class SpellRule:
    enabled: bool = True
    spell: int = 0
    subject: str = "always"
    comparator: str = "below"
    threshold: float = 50.0
    cooldown: float = 5.0
    target: str = "monster"

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "spell": self.spell,
            "subject": self.subject,
            "comparator": self.comparator,
            "threshold": self.threshold,
            "cooldown": self.cooldown,
            "target": self.target,
        }

    @classmethod
    def from_dict(cls, data: object) -> "SpellRule":
        if not isinstance(data, dict):
            raise ConfigError("spell rule is not an object")
        subject = data.get("subject", "always")
        comparator = data.get("comparator", "below")
        target = data.get("target", "monster")
        return cls(
            enabled=_as_bool(data.get("enabled"), True),
            spell=_as_int(data.get("spell"), 0, 0, 10**9),
            subject=subject if subject in SUBJECTS else "always",
            comparator=(
                comparator if comparator in COMPARATORS else "below"
            ),
            threshold=_as_float(data.get("threshold"), 50.0, 0.0, 100.0),
            cooldown=_as_float(data.get("cooldown"), 5.0, 0.0, 3600.0),
            target=target if target in SPELL_TARGETS else "monster",
        )


@dataclass
class BotConfig:
    auto_attack: bool = False
    retaliate: bool = True
    skill: int = 0
    skill_template: str = "u_s {skill} {type} {target}"
    attack_range: int = 10
    attack_cooldown: float = 0.0
    pickup: bool = True
    loot_range: int = 3
    recover: bool = True
    low_hp_pct: float = 30.0
    loop: bool = True
    monster_mode: str = "all"
    monster_vnums: list[int] = field(default_factory=list)
    attack_unknown: bool = False
    spell_rules: list[SpellRule] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": CONFIG_VERSION,
            "auto_attack": self.auto_attack,
            "retaliate": self.retaliate,
            "skill": self.skill,
            "skill_template": self.skill_template,
            "attack_range": self.attack_range,
            "attack_cooldown": self.attack_cooldown,
            "pickup": self.pickup,
            "loot_range": self.loot_range,
            "recover": self.recover,
            "low_hp_pct": self.low_hp_pct,
            "loop": self.loop,
            "monster_mode": self.monster_mode,
            "monster_vnums": sorted(set(self.monster_vnums)),
            "attack_unknown": self.attack_unknown,
            "spell_rules": [rule.to_dict() for rule in self.spell_rules],
        }

    @classmethod
    def from_dict(cls, data: object) -> "BotConfig":
        if not isinstance(data, dict):
            raise ConfigError("configuration root is not an object")

        mode = data.get("monster_mode", "all")
        vnums: list[int] = []
        raw_vnums = data.get("monster_vnums", [])
        if isinstance(raw_vnums, (list, tuple)):
            for value in raw_vnums[:MAX_MONSTER_VNUMS]:
                try:
                    vnum = int(value)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
                if 0 < vnum <= 10**9:
                    vnums.append(vnum)

        raw_rules = data.get("spell_rules", [])
        rules: list[SpellRule] = []
        if isinstance(raw_rules, (list, tuple)):
            for value in raw_rules[:MAX_SPELL_RULES]:
                rules.append(SpellRule.from_dict(value))

        return cls(
            auto_attack=_as_bool(data.get("auto_attack"), False),
            retaliate=_as_bool(data.get("retaliate"), True),
            skill=_as_int(data.get("skill"), 0, 0, 10**9),
            skill_template=_as_text(
                data.get("skill_template"),
                "u_s {skill} {type} {target}",
            ),
            attack_range=_as_int(data.get("attack_range"), 10, 1, 40),
            attack_cooldown=_as_float(
                data.get("attack_cooldown"), 0.0, 0.0, 30.0
            ),
            pickup=_as_bool(data.get("pickup"), True),
            loot_range=_as_int(data.get("loot_range"), 3, 1, 10),
            recover=_as_bool(data.get("recover"), True),
            low_hp_pct=_as_float(data.get("low_hp_pct"), 30.0, 5.0, 90.0),
            loop=_as_bool(data.get("loop"), True),
            monster_mode=mode if mode in MONSTER_MODES else "all",
            monster_vnums=sorted(set(vnums)),
            attack_unknown=_as_bool(data.get("attack_unknown"), False),
            spell_rules=rules,
        )


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def _as_int(value: object, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _as_float(value: object, default: float, low: float, high: float) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _as_text(value: object, default: str, limit: int = 200) -> str:
    if not isinstance(value, str):
        return default
    text = value.strip()
    if not text:
        return default
    return text[:limit]


def save_config(path: str | Path, config: BotConfig) -> Path:
    """Write the configuration atomically and return the target path."""
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"cannot create {target.parent}: {exc}") from exc

    payload = json.dumps(config.to_dict(), indent=2) + "\n"
    try:
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=target.name + ".",
            suffix=".tmp",
            delete=False,
        )
        with handle:
            handle.write(payload)
        os.replace(handle.name, target)
    except OSError as exc:
        raise ConfigError(f"cannot write {target}: {exc}") from exc
    return target


def load_config(path: str | Path) -> BotConfig:
    """Read and validate a configuration file."""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {source}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{source}: invalid JSON: {exc}") from exc
    return BotConfig.from_dict(data)
