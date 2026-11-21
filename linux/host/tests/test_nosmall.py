"""Tests for the NosMall catalog parser and Bazar export."""

from __future__ import annotations

import unittest

from ntpl.bazar import BazarItem, search_vnums
from ntpl.nosmall import (
    NosMallEntry,
    NosMallGroup,
    bazar_items,
    group_entries,
    importable_groups,
    is_pet_group,
    known_reference_costs,
    load_catalog,
    load_entries,
    load_item_info,
    load_item_names,
)
from ntpl.monsternames import default_monster_data_path


class NosMallCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        if not default_monster_data_path().is_file():
            self.skipTest("NosTale client data is not installed")

    def test_item_names_cover_reference_vnums(self) -> None:
        names = load_item_names()
        self.assertEqual(names.get(2174), "Speech Bubble")
        self.assertEqual(names.get(1362), "Soulstone Blessing")
        self.assertEqual(names.get(5370), "Fairy Experience Potion")

    def test_catalog_groups_variants_by_base(self) -> None:
        groups = {group.base_vnum: group for group in load_catalog()}
        self.assertGreater(len(groups), 1000)
        self.assertEqual(groups[1362].name, "Soulstone Blessing")
        self.assertIn(5195, groups[1362].vnums)
        self.assertIn(9075, groups[1362].vnums)
        self.assertIn(2174, groups[2174].vnums)
        self.assertIn(10029, groups[2174].vnums)

    def test_entries_keep_item_and_base_vnums(self) -> None:
        entries = load_entries()
        self.assertGreater(len(entries), 3000)
        item = next(entry for entry in entries if entry.item_vnum == 5370)
        self.assertEqual(item.base_vnum, 2621)
        self.assertIn(item.mall_vnum, {1334, 2483})

    def test_bazar_items_use_real_search_packets(self) -> None:
        groups = load_catalog()[:5]
        items = bazar_items(groups, known_reference_costs())
        self.assertEqual(len(items), len(groups))
        for item in items:
            self.assertIsInstance(item, BazarItem)
            self.assertTrue(item.search_packet)
            self.assertEqual(
                search_vnums(item.search_packet), list(item.vnums)
            )
            self.assertIsNone(item.inv_tab)

    def test_group_entries_deduplicates_variants(self) -> None:
        entries = [
            NosMallEntry(1, 1362, 1362, name="A"),
            NosMallEntry(2, 5195, 1362, name="A"),
            NosMallEntry(3, 9075, 1362, name="A"),
        ]
        groups = group_entries(entries)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].vnums, (1362, 5195, 9075))
        self.assertEqual(groups[0].records, 3)

    def test_group_entries_keep_record_kinds(self) -> None:
        entries = [
            NosMallEntry(1, 2158, 2158, kind=2, name="Gourmet Pet Food"),
            NosMallEntry(2, 10024, 2158, kind=0, name="Gourmet Pet Food"),
        ]
        groups = group_entries(entries)
        self.assertEqual(groups[0].kinds, (0, 2))


class ItemInfoTests(unittest.TestCase):
    def setUp(self) -> None:
        if not default_monster_data_path().is_file():
            self.skipTest("NosTale client data is not installed")

    def test_item_info_reads_name_price_and_category(self) -> None:
        info = load_item_info()
        self.assertGreater(len(info), 7000)
        self.assertEqual(info[2174].name, "Speech Bubble")
        self.assertEqual(info[2174].npc_price, 3000)
        self.assertEqual(info[1362].npc_price, 100)
        self.assertIsInstance(info[2174].category, int)
        for vnum, item in info.items():
            self.assertGreater(vnum, 0)
            self.assertTrue(item.name)

    def test_item_names_still_resolve_after_record_fix(self) -> None:
        names = load_item_names()
        self.assertEqual(names.get(2174), "Speech Bubble")
        self.assertEqual(names.get(1362), "Soulstone Blessing")


class PetFilterTests(unittest.TestCase):
    def test_pet_names_are_recognized(self):
        for name, kinds in (
            ("Pet Food", (0,)),
            ("Gourmet Pet Food", (0, 2)),
            ("Name Tag for Pets", (0, 2)),
            ("Fairy Booster", (0, 3)),
            ("Fairy Experience Potion", (0,)),
            ("Partner Medicine", (0, 1, 2)),
            ("Partner Skill Ticket (All)", (0,)),
            ("Sellaim", (0, 3)),
            ("Mini Pet 'Gryphon' (Permanent)", (0,)),
        ):
            group = NosMallGroup(1, name, (1,), kinds=kinds)
            self.assertTrue(is_pet_group(group), name)

    def test_pet_record_kind_marks_unusual_names(self):
        group = NosMallGroup(541, "Pii", (541,), kinds=(4,))
        self.assertTrue(is_pet_group(group))

    def test_partner_and_furniture_words_do_not_false_positive(self):
        for name in (
            "Magic Carpet",
            "Petal Wings",
            "Speech Bubble",
            "Wings of Friendship",
            "Soulstone Blessing",
        ):
            group = NosMallGroup(1, name, (1,), kinds=(0,))
            self.assertFalse(is_pet_group(group), name)

    def test_importable_requires_a_cost_and_no_pet(self):
        groups = [
            NosMallGroup(1362, "Soulstone Blessing", (1362,), kinds=(0,)),
            NosMallGroup(2158, "Gourmet Pet Food", (2158,), kinds=(0, 2)),
            NosMallGroup(9999, "Unpriced Item", (9999,), kinds=(0,)),
            NosMallGroup(2621, "Fairy Experience Potion", (5370,), kinds=(0,)),
        ]
        picked = importable_groups(groups, {1362: 1.0, 2158: 2.0, 5370: 2.5})
        self.assertEqual([group.name for group in picked], ["Soulstone Blessing"])

    def test_importable_finds_a_variant_price(self):
        group = NosMallGroup(2620, "Golden Equipment Protection Scroll",
                             (5369, 9459), kinds=(0,))
        self.assertEqual(
            [g.base_vnum for g in importable_groups([group], {9459: 20})],
            [2620],
        )

    def test_split_keeps_only_priced_non_pets(self):
        from ntpl.nosmall import is_importable_item, split_priced_non_pet

        good = BazarItem(
            name="Soulstone Blessing", vnum=1362, nos_cost=1.0
        )
        unpriced = BazarItem(name="Some Scroll", vnum=1, nos_cost=None)
        npc_only = BazarItem(
            name="Some Other Scroll", vnum=2, nos_cost=None, npc_cost=500
        )
        pet_by_name = BazarItem(name="Fairy Booster", vnum=1296, nos_cost=5)
        pet_by_vnum = BazarItem(name="Pii", vnum=541, nos_cost=5)
        keep, removed = split_priced_non_pet(
            [good, unpriced, npc_only, pet_by_name, pet_by_vnum], {541}
        )
        self.assertEqual(keep, [good])
        self.assertEqual(
            removed, [unpriced, npc_only, pet_by_name, pet_by_vnum]
        )
        self.assertTrue(is_importable_item(good))
        self.assertFalse(is_importable_item(pet_by_vnum, {541}))


if __name__ == "__main__":
    unittest.main()
