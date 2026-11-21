"""Tests for the per-character inventory snapshot cache."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ntpl.engine import InventorySlot
from ntpl.spawnlog import (
    inventory_cache_path,
    load_inventory_cache,
    save_inventory_cache,
)


class InventoryCacheTests(unittest.TestCase):
    def test_round_trip_keeps_fields_and_special_slots(self) -> None:
        bags = {
            0: {
                27: InventorySlot(0, 27, -4962, 5, ("7", "1", "0", "0")),
                30: InventorySlot(0, 30, 885, 0, ("0", "0", "0", "0")),
            },
            1: {0: InventorySlot(1, 0, 2174, 5)},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.json"
            target = save_inventory_cache(bags, 10449, path)
            self.assertEqual(target, path)
            loaded = load_inventory_cache(path)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        restored, saved_at = loaded
        self.assertEqual(restored, bags)
        self.assertGreater(saved_at, 0.0)

    def test_character_mismatch_is_rejected(self) -> None:
        bags = {1: {0: InventorySlot(1, 0, 2174, 5)}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.json"
            save_inventory_cache(bags, 111, path)
            self.assertIsNone(load_inventory_cache(path, 222))
            self.assertIsNotNone(load_inventory_cache(path, 111))

    def test_missing_and_malformed_files_are_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            self.assertIsNone(load_inventory_cache(path))
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(load_inventory_cache(path))
            path.write_text('{"version": 99, "bags": {}}', encoding="utf-8")
            self.assertIsNone(load_inventory_cache(path))

    def test_bad_slots_are_skipped_but_good_slots_survive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.json"
            path.write_text(
                '{"version": 1, "char_id": 5, "saved_at": 10, '
                '"bags": {"2": {"0": {"vnum": 2282, "quantity": 11}, '
                '"1": {"vnum": 0, "quantity": 5}, '
                '"2": {"vnum": 1007, "quantity": -1}, '
                '"3": {"vnum": 1012, "quantity": 40, '
                '"fields": ["5", "0"]}}}}',
                encoding="utf-8",
            )
            loaded = load_inventory_cache(path)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        bags, saved_at = loaded
        self.assertEqual(saved_at, 10.0)
        self.assertEqual(
            bags,
            {
                2: {
                    0: InventorySlot(2, 0, 2282, 11),
                    3: InventorySlot(2, 3, 1012, 40, ("5", "0")),
                }
            },
        )

    def test_cache_path_uses_the_character_and_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"NTPL_INVENTORY_DIR": tmp}):
                self.assertEqual(
                    inventory_cache_path(10449),
                    Path(tmp) / "inventory-10449.json",
                )


if __name__ == "__main__":
    unittest.main()
