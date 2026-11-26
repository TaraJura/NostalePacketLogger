import tempfile
import unittest
from pathlib import Path

from ntpl.engine import Entity
from ntpl.minigame import (
    DEFAULT_INTERVAL_MS,
    DEFAULT_KEY,
    DEFAULT_RADIUS,
    MAX_INTERVAL_MS,
    MAX_RADIUS,
    MIN_INTERVAL_MS,
    MIN_RADIUS,
    MinigameConfig,
    clamp_interval,
    clamp_radius,
    load_config,
    nearest_player,
    nearby_players,
    normalize_key,
    save_config,
)


def player(entity_id: int, name: str, x: int, y: int) -> Entity:
    return Entity(entity_id, 1, name=name, x=x, y=y)


class KeyNameTests(unittest.TestCase):
    def test_letters_and_digits(self):
        self.assertEqual(normalize_key("y"), "y")
        self.assertEqual(normalize_key(" Y "), "y")
        self.assertEqual(normalize_key("7"), "7")

    def test_named_keys_and_aliases(self):
        self.assertEqual(normalize_key("space"), "space")
        self.assertEqual(normalize_key("Spacebar"), "space")
        self.assertEqual(normalize_key("ESC"), "esc")
        self.assertEqual(normalize_key("escape"), "esc")
        self.assertEqual(normalize_key("control"), "ctrl")
        self.assertEqual(normalize_key("return"), "enter")
        self.assertEqual(normalize_key("F12"), "f12")

    def test_rejects_what_the_dll_cannot_parse(self):
        for value in ("", "  ", "yy", "!", "page up", "f13", None, 42):
            self.assertIsNone(normalize_key(value), value)


class ClampTests(unittest.TestCase):
    def test_interval(self):
        self.assertEqual(clamp_interval(40), MIN_INTERVAL_MS)
        self.assertEqual(clamp_interval("250"), 250)
        self.assertEqual(clamp_interval(1), MIN_INTERVAL_MS)
        self.assertEqual(clamp_interval(999999), MAX_INTERVAL_MS)
        self.assertEqual(clamp_interval("junk"), DEFAULT_INTERVAL_MS)

    def test_radius(self):
        self.assertEqual(clamp_radius(0), MIN_RADIUS)
        self.assertEqual(clamp_radius(15), 15)
        self.assertEqual(clamp_radius(999), MAX_RADIUS)
        self.assertEqual(clamp_radius(None), DEFAULT_RADIUS)


class ConfigTests(unittest.TestCase):
    def test_normalized_defaults_and_clamps(self):
        config = MinigameConfig(
            key="NOT A KEY", interval_ms=1, focus_players=1, radius=999
        ).normalized()
        self.assertEqual(config.key, DEFAULT_KEY)
        self.assertEqual(config.interval_ms, MIN_INTERVAL_MS)
        self.assertTrue(config.focus_players)
        self.assertEqual(config.radius, MAX_RADIUS)

    def test_round_trip_file(self):
        config = MinigameConfig(
            key="F1", interval_ms=250, focus_players=False, radius=9
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "minigame-1234.json"
            save_config(path, config)
            self.assertTrue(path.is_file())
            loaded = load_config(path)
        self.assertEqual(loaded.to_dict(), config.to_dict())

    def test_broken_files_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            for text in ("", "{", "[]", '{"key": "yy", "interval_ms": "x"}'):
                path = Path(directory) / "minigame.json"
                path.write_text(text, encoding="utf-8")
                loaded = load_config(path)
                self.assertEqual(loaded.key, DEFAULT_KEY)
                self.assertEqual(
                    loaded.interval_ms, DEFAULT_INTERVAL_MS
                )
        self.assertEqual(
            load_config(Path(directory) / "missing.json").to_dict(),
            MinigameConfig().to_dict(),
        )

    def test_from_dict_tolerates_extra_fields(self):
        loaded = MinigameConfig.from_dict(
            {"key": "Y", "interval_ms": 120, "radius": 5, "x": 1}
        )
        self.assertEqual(loaded.to_dict(), {
            "key": "y",
            "interval_ms": 120,
            "focus_players": True,
            "radius": 5,
        })
        # JSON numbers are floats; they must still clamp cleanly.
        self.assertEqual(
            MinigameConfig.from_dict({"interval_ms": 99.9}).interval_ms, 99
        )
        self.assertTrue(
            MinigameConfig.from_dict(
                {"focus_players": "false"}
            ).focus_players
        )
        self.assertFalse(
            MinigameConfig.from_dict({"focus_players": False}).focus_players
        )
        self.assertEqual(
            load_config("/nonexistent/path").focus_players, True
        )


class NearbyPlayerTests(unittest.TestCase):
    def setUp(self):
        self.entities = [
            player(10, "Self", 25, 25),
            player(11, "Alice", 27, 25),       # distance 2
            player(12, "Bob", 30, 27),         # distance 5
            player(13, "Carol", 40, 40),       # distance 15, at the edge
            player(14, "", 25, 30),            # unnamed player, distance 5
            Entity(20, 2, vnum=2119, name="Sheep", x=25, y=26),
            Entity(21, 3, vnum=9, name="Happy Wooly", x=26, y=25),
            Entity(22, 0, name="Unknown", x=25, y=25),
        ]

    def test_only_players_are_considered(self):
        rows = nearby_players(self.entities, 10, (25, 25), 15)
        # Equal distances sort by name; an unnamed player sorts before Bob.
        self.assertEqual(
            [entity.entity_id for entity, _ in rows], [11, 14, 12, 13]
        )

    def test_nearest_and_exclusion(self):
        entity, distance = nearest_player(
            self.entities, 10, (25, 25), 15
        )
        self.assertEqual((entity.entity_id, distance), (11, 2))
        entity, distance = nearest_player(
            self.entities, 10, (25, 25), 15, exclude={11}
        )
        # The unnamed player sorts before Bob at the same distance.
        self.assertEqual((entity.entity_id, distance), (14, 5))
        entity, distance = nearest_player(
            self.entities, 10, (25, 25), 15, exclude={11, 14}
        )
        self.assertEqual((entity.entity_id, distance), (12, 5))

    def test_radius_is_chebyshev_and_inclusive(self):
        self.assertIsNotNone(
            nearest_player(self.entities, 10, (25, 25), 15)
        )
        rows = nearby_players(self.entities, 10, (25, 25), 14)
        self.assertNotIn(13, [entity.entity_id for entity, _ in rows])
        self.assertIsNone(nearest_player(self.entities, 10, (25, 25), 1))

    def test_no_position_means_no_target(self):
        self.assertIsNone(nearest_player(self.entities, 10, None, 15))
        self.assertEqual(nearby_players(self.entities, 10, None, 15), [])

    def test_own_id_none_keeps_every_player(self):
        rows = nearby_players(self.entities, None, (25, 25), 15)
        self.assertIn(10, [entity.entity_id for entity, _ in rows])


if __name__ == "__main__":
    unittest.main()
