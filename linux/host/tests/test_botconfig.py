import tempfile
import unittest
from pathlib import Path

from ntpl.botconfig import (
    BotConfig,
    ConfigError,
    SpellRule,
    load_config,
    save_config,
)


class BotConfigTests(unittest.TestCase):
    def test_round_trip_file(self):
        config = BotConfig(
            auto_attack=True,
            monster_mode="only",
            monster_vnums=[206, 200, 206],
            attack_unknown=True,
            spell_rules=[
                SpellRule(
                    spell=10,
                    subject="my_hp",
                    comparator="below",
                    threshold=45,
                    cooldown=7,
                    target="self",
                )
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot.json"
            save_config(path, config)
            loaded = load_config(path)

        self.assertEqual(loaded.to_dict(), config.to_dict())
        self.assertEqual(loaded.monster_vnums, [200, 206])
        self.assertEqual(loaded.spell_rules[0].spell, 10)
        self.assertEqual(loaded.spell_rules[0].target, "self")
        self.assertTrue(loaded.attack_unknown)

    def test_invalid_values_are_clamped_without_crashing(self):
        config = BotConfig.from_dict(
            {
                "attack_range": 999,
                "attack_cooldown": -5,
                "low_hp_pct": 500,
                "low_mp_pct": 500,
                "monster_mode": "weird",
                "monster_vnums": ["206", "x", -1, 0],
                "spell_rules": [
                    {
                        "spell": "7",
                        "subject": "bad",
                        "comparator": "bad",
                        "threshold": 200,
                        "cooldown": -2,
                        "target": "bad",
                    }
                ],
            }
        )
        self.assertEqual(config.attack_range, 40)
        self.assertEqual(config.attack_cooldown, 0.0)
        self.assertEqual(config.low_hp_pct, 90.0)
        self.assertEqual(config.low_mp_pct, 90.0)
        self.assertEqual(config.monster_mode, "all")
        self.assertEqual(config.monster_vnums, [206])
        rule = config.spell_rules[0]
        self.assertEqual(
            (rule.spell, rule.subject, rule.comparator),
            (7, "always", "below"),
        )
        self.assertEqual(rule.threshold, 100.0)
        self.assertEqual(rule.cooldown, 0.0)
        self.assertEqual(rule.target, "monster")

    def test_block_portals_defaults_on_and_round_trips(self):
        self.assertTrue(BotConfig().block_portals)
        self.assertTrue(
            BotConfig.from_dict({}).block_portals,
            "old configs must not lose portal protection",
        )
        self.assertFalse(
            BotConfig.from_dict({"block_portals": False}).block_portals
        )
        self.assertTrue(
            BotConfig.from_dict({"block_portals": "yes"}).block_portals
        )
        config = BotConfig(block_portals=False)
        loaded = BotConfig.from_dict(config.to_dict())
        self.assertFalse(loaded.block_portals)

    def test_spell_rule_order_and_conditions_are_preserved(self):
        config = BotConfig(
            spell_rules=[
                SpellRule(
                    spell=876,
                    subject="always",
                    cooldown=90,
                    target="monster",
                ),
                SpellRule(
                    spell=867,
                    subject="my_hp",
                    comparator="below",
                    threshold=50,
                    cooldown=3,
                    target="self",
                ),
                SpellRule(
                    spell=866,
                    subject="always",
                    cooldown=1,
                    target="monster",
                ),
            ]
        )
        loaded = BotConfig.from_dict(config.to_dict())
        self.assertEqual(
            [rule.spell for rule in loaded.spell_rules], [876, 867, 866]
        )
        self.assertEqual(
            [rule.subject for rule in loaded.spell_rules],
            ["always", "my_hp", "always"],
        )
        self.assertEqual(
            [rule.target for rule in loaded.spell_rules],
            ["monster", "self", "monster"],
        )

    def test_invalid_json_raises_config_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_missing_file_raises_config_error(self):
        with self.assertRaises(ConfigError):
            load_config("/nonexistent/ntpl-bot.json")

    def test_non_object_root_raises_config_error(self):
        with self.assertRaises(ConfigError):
            BotConfig.from_dict(["not", "an", "object"])


if __name__ == "__main__":
    unittest.main()
