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

    def test_invalid_values_are_clamped_without_crashing(self):
        config = BotConfig.from_dict(
            {
                "attack_range": 999,
                "attack_cooldown": -5,
                "low_hp_pct": 500,
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
        self.assertEqual(config.attack_cooldown, 1.0)
        self.assertEqual(config.low_hp_pct, 90.0)
        self.assertEqual(config.monster_mode, "all")
        self.assertEqual(config.monster_vnums, [206])
        rule = config.spell_rules[0]
        self.assertEqual(
            (rule.spell, rule.subject, rule.comparator),
            (7, "my_hp", "below"),
        )
        self.assertEqual(rule.threshold, 100.0)
        self.assertEqual(rule.cooldown, 0.0)
        self.assertEqual(rule.target, "monster")

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
