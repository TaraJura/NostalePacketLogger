"""Pet Trainer settings and the suctl packet."""

import os
import tempfile
import unittest

from ntpl.tamer import SPARRING_VNUM
from ntpl.tamer import TamerConfig
from ntpl.tamer import build_suctl_packet
from ntpl.tamer import load_config
from ntpl.tamer import save_config


class SuctlPacketTests(unittest.TestCase):
    def test_one_pet(self):
        self.assertEqual(
            build_suctl_packet(20001, [1501938], 1506236),
            "suctl 20001 1 1501938 3 1506236",
        )

    def test_several_pets_in_one_packet(self):
        self.assertEqual(
            build_suctl_packet(20001, [1501938, 1502199], 1506236),
            "suctl 20001 2 1501938 3 1506236 1502199 3 1506236",
        )

    def test_live_sparring_vnum(self):
        self.assertEqual(SPARRING_VNUM, 1497)


class TamerConfigTests(unittest.TestCase):
    def test_defaults(self):
        config = TamerConfig()
        self.assertEqual(config.tame_vnum, 333)
        self.assertEqual(config.tame_slot, 1)
        self.assertEqual(config.tame_hp_pct, 25.0)
        self.assertFalse(config.e2e)
        self.assertEqual(config.miniland_map, 20001)
        self.assertEqual(config.portal_map, 1)
        self.assertEqual((config.portal_x, config.portal_y), (49, 133))
        self.assertEqual(config.summon_slot, 2)
        self.assertEqual(config.sparring_vnum, 1497)
        self.assertEqual(config.extract_slot, 3)
        self.assertEqual(config.train_seconds, 480)
        self.assertTrue(config.extract)

    def test_values_are_clamped(self):
        loaded = TamerConfig.from_dict(
            {
                "tame_slot": 0,
                "tame_hp_pct": 99,
                "train_seconds": 1,
                "summon_slot": 42,
                "e2e": True,
            }
        )
        self.assertEqual(loaded.tame_slot, 1)
        self.assertEqual(loaded.tame_hp_pct, 50.0)
        self.assertEqual(loaded.train_seconds, 30)
        self.assertEqual(loaded.summon_slot, 9)
        self.assertTrue(loaded.e2e)

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tamer.json")
            config = TamerConfig(e2e=True, train_seconds=600)
            save_config(path, config)
            self.assertEqual(load_config(path).to_dict(), config.to_dict())
            self.assertEqual(
                load_config(os.path.join(tmp, "missing.json")).to_dict(),
                TamerConfig().to_dict(),
            )


if __name__ == "__main__":
    unittest.main()
