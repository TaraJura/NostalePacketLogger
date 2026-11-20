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

    def test_speed_boost_defaults_to_off_and_is_clamped(self):
        self.assertEqual(BotConfig().speed_boost, 0)
        self.assertEqual(
            BotConfig.from_dict({"speed_boost": 35}).speed_boost, 35
        )
        self.assertEqual(
            BotConfig.from_dict({"speed_boost": -5}).speed_boost, 0
        )
        self.assertEqual(
            BotConfig.from_dict({"speed_boost": 999}).speed_boost, 999
        )
        self.assertEqual(
            BotConfig.from_dict({"speed_boost": 5000000}).speed_boost,
            1000000,
        )
        config = BotConfig(speed_boost=35)
        self.assertEqual(config.to_dict()["speed_boost"], 35)
        self.assertEqual(
            BotConfig.from_dict(config.to_dict()).speed_boost, 35
        )

    def test_cast_boost_defaults_off_and_migrates_the_percentage(self):
        self.assertFalse(BotConfig().cast_boost)
        self.assertTrue(
            BotConfig.from_dict({"cast_boost": True}).cast_boost
        )
        self.assertFalse(
            BotConfig.from_dict({"cast_boost": False}).cast_boost
        )
        # The first build saved a percentage; any raised value means "on".
        self.assertTrue(
            BotConfig.from_dict({"cast_speed": 200}).cast_boost
        )
        self.assertFalse(
            BotConfig.from_dict({"cast_speed": 100}).cast_boost
        )
        config = BotConfig(cast_boost=True)
        self.assertIs(config.to_dict()["cast_boost"], True)
        self.assertTrue(
            BotConfig.from_dict(config.to_dict()).cast_boost
        )

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

    def test_block_portals_defaults_off_and_round_trips(self):
        self.assertFalse(BotConfig().block_portals)
        self.assertFalse(
            BotConfig.from_dict({}).block_portals,
            "portals must stay usable unless the user opts in",
        )
        self.assertTrue(
            BotConfig.from_dict({"block_portals": True}).block_portals
        )
        self.assertTrue(
            BotConfig.from_dict({"block_portals": "yes"}).block_portals
        )
        config = BotConfig(block_portals=True)
        loaded = BotConfig.from_dict(config.to_dict())
        self.assertTrue(loaded.block_portals)

    def test_follow_defaults_off_and_round_trips(self):
        self.assertFalse(BotConfig().follow)
        self.assertEqual(BotConfig().follow_player, "")
        self.assertFalse(BotConfig().follow_attack)
        self.assertFalse(BotConfig().follow_mirror)
        loaded = BotConfig.from_dict(
            {
                "follow": True,
                "follow_player": "  Follower Friend  ",
                "follow_attack": True,
                "follow_mirror": True,
            }
        )
        self.assertTrue(loaded.follow)
        self.assertEqual(loaded.follow_player, "Follower Friend")
        self.assertTrue(loaded.follow_attack)
        self.assertTrue(loaded.follow_mirror)
        self.assertEqual(
            BotConfig.from_dict({"follow": "yes"}).follow, True
        )
        self.assertTrue(
            BotConfig.from_dict({"follow_mirror": 1}).follow_mirror
        )
        self.assertFalse(
            BotConfig.from_dict({"follow_mirror": "off"}).follow_mirror
        )
        config = BotConfig(
            follow=True,
            follow_player="Mule",
            follow_attack=True,
            follow_mirror=True,
        )
        self.assertEqual(config.to_dict()["follow_player"], "Mule")
        self.assertTrue(config.to_dict()["follow_mirror"])
        loaded = BotConfig.from_dict(config.to_dict())
        self.assertTrue(loaded.follow)
        self.assertTrue(loaded.follow_attack)
        self.assertTrue(loaded.follow_mirror)

    def test_revive_on_death_defaults_off_and_round_trips(self):
        self.assertFalse(BotConfig().revive_on_death)
        self.assertFalse(BotConfig.from_dict({}).revive_on_death)
        self.assertTrue(
            BotConfig.from_dict({"revive_on_death": True}).revive_on_death
        )
        self.assertTrue(
            BotConfig.from_dict(
                BotConfig(revive_on_death=True).to_dict()
            ).revive_on_death
        )

    def test_waypoints_map_and_draw_mode_round_trip(self):
        config = BotConfig(
            draw_path=True,
            map_id=20,
            waypoints=[(25, 118), (30, 120)],
            path_start=(24, 117),
        )
        loaded = BotConfig.from_dict(config.to_dict())
        self.assertTrue(loaded.draw_path)
        self.assertEqual(loaded.map_id, 20)
        self.assertEqual(loaded.waypoints, [(25, 118), (30, 120)])
        self.assertEqual(loaded.path_start, (24, 117))

    def test_invalid_waypoints_are_dropped_and_capped(self):
        loaded = BotConfig.from_dict(
            {
                "map_id": "20",
                "waypoints": [
                    [1, 2],
                    ["3", "4"],
                    [5],
                    "bad",
                    [-1, 2],
                    [40000, 1],
                ],
                "path_start": [7],
            }
        )
        self.assertEqual(loaded.waypoints, [(1, 2), (3, 4)])
        self.assertIsNone(loaded.map_id)  # strings are not a map id
        self.assertIsNone(loaded.path_start)

        many = [[i, i] for i in range(700)]
        loaded = BotConfig.from_dict({"waypoints": many})
        self.assertEqual(len(loaded.waypoints), 500)

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
