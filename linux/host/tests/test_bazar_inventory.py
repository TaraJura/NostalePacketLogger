"""Tests for the inventory-to-NosBazar search planner."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from ntpl.bazar import (
    BazarItem,
    InventoryPlan,
    build_search_packet,
    inventory_search_items,
    search_vnums,
    tracked_vnums,
)


def _slot(
    bag: int, index: int, vnum: int, quantity: int = 1
) -> SimpleNamespace:
    """The duck-typed shape of engine.InventorySlot."""
    return SimpleNamespace(bag=bag, index=index, vnum=vnum, quantity=quantity)


class TrackedVnumsTests(unittest.TestCase):
    def test_packet_and_item_vnums_are_collected_by_magnitude(self) -> None:
        items = [
            BazarItem("a", build_search_packet([1, 2]), vnum=-3),
            BazarItem("b", vnum=0),
            BazarItem("c", vnum=4),
        ]
        self.assertEqual(tracked_vnums(items), {1, 2, 3, 4})

    def test_plan_defaults(self) -> None:
        plan = InventoryPlan()
        self.assertEqual((plan.items, plan.tracked, plan.slots), ([], 0, 0))


class InventorySearchTests(unittest.TestCase):
    def test_duplicates_collapse_and_slots_count_every_slot(self) -> None:
        slots = [
            _slot(2, 0, 2282, 1),
            _slot(2, 5, 2174, 5),
            _slot(1, 3, 2174, 7),
        ]
        plan = inventory_search_items(
            slots, {2174: "bubbl", 2282: "flask"}, []
        )
        self.assertEqual(plan.slots, 3)
        self.assertEqual(plan.tracked, 0)
        self.assertEqual([item.vnum for item in plan.items], [2174, 2282])
        bubbl = plan.items[0]
        # Main (bag 1) wins over Etc (bag 2); amount is the Main stack.
        self.assertEqual((bubbl.inv_tab, bubbl.amount), (1, 7))
        self.assertEqual(bubbl.search_packet, build_search_packet([2174]))
        self.assertTrue(bubbl.enabled)
        # Inventory scan is monitor-first; joining relisting is explicit.
        self.assertFalse(bubbl.relist)
        self.assertEqual(bubbl.min_price, 0)
        self.assertEqual(plan.items[1].inv_tab, 2)

    def test_negative_vnum_groups_under_its_magnitude(self) -> None:
        plan = inventory_search_items(
            [_slot(0, 27, -4962, 5)], {4962: "Chaotic"}, []
        )
        self.assertEqual(len(plan.items), 1)
        item = plan.items[0]
        self.assertEqual(item.vnum, 4962)
        self.assertEqual(item.name, "Chaotic")
        self.assertEqual(search_vnums(item.search_packet), [4962])
        self.assertIsNone(item.inv_tab)
        self.assertEqual(item.amount, 1)

    def test_positive_main_slot_sets_tab_and_amount(self) -> None:
        plan = inventory_search_items(
            [_slot(0, 2, 885, 100), _slot(1, 4, 885, 11)], {885: "card"}, []
        )
        item = plan.items[0]
        self.assertEqual((item.inv_tab, item.amount), (1, 11))

    def test_already_tracked_vnums_are_skipped_and_counted(self) -> None:
        existing = [
            BazarItem("in packet", build_search_packet([1261])),
            BazarItem("negative vnum", vnum=-2174),
        ]
        slots = [
            _slot(1, 0, 1261, 1),
            _slot(1, 1, 2174, 1),
            _slot(1, 2, 1285, 1),
            _slot(1, 3, 0, 0),
        ]
        plan = inventory_search_items(slots, {}, existing)
        self.assertEqual(plan.tracked, 2)
        self.assertEqual([item.vnum for item in plan.items], [1285])
        self.assertEqual(plan.slots, 4)

    def test_names_stay_unique(self) -> None:
        plan = inventory_search_items(
            [_slot(2, 0, 1000, 1), _slot(2, 1, 2000, 1)],
            {1000: "Same", 2000: "Same"},
            [],
        )
        self.assertEqual(
            [item.name for item in plan.items], ["Same", "Same [2000]"]
        )

        existing = [BazarItem("Same", build_search_packet([3000]))]
        plan = inventory_search_items(
            [_slot(2, 0, 4000, 1)], {4000: "Same"}, existing
        )
        self.assertEqual(plan.items[0].name, "Same [4000]")

    def test_costs_prefer_nd_and_fall_back_to_npc(self) -> None:
        slots = [
            _slot(1, 0, 111, 1),
            _slot(1, 1, 222, 1),
            _slot(1, 2, 333, 1),
            _slot(1, 3, 444, 1),
        ]
        plan = inventory_search_items(
            slots,
            {},
            [],
            costs={111: 0.5, 222: 0, 333: 0},
            npc_prices={111: 9999, 222: 300, 333: 0, 444: -5},
        )
        items = {item.vnum: item for item in plan.items}
        self.assertEqual(items[111].nos_cost, 0.5)
        self.assertIsNone(items[111].npc_cost)
        self.assertIsNone(items[222].nos_cost)
        self.assertEqual(items[222].npc_cost, 300.0)
        self.assertIsNone(items[333].nos_cost)
        self.assertIsNone(items[333].npc_cost)
        self.assertIsNone(items[444].npc_cost)

    def test_vnum_zero_is_skipped_and_name_falls_back(self) -> None:
        plan = inventory_search_items(
            [_slot(2, 0, 0, 5), _slot(2, 1, 1234, 2)], {}, []
        )
        self.assertEqual(plan.slots, 2)
        self.assertEqual([item.vnum for item in plan.items], [1234])
        self.assertEqual(plan.items[0].name, "item 1234")

    def test_amount_clamps_to_1000(self) -> None:
        plan = inventory_search_items([_slot(1, 0, 555, 2500)], {}, [])
        self.assertEqual(plan.items[0].amount, 1000)
        plan = inventory_search_items([_slot(1, 0, 555, 0)], {}, [])
        self.assertEqual(plan.items[0].amount, 1)


if __name__ == "__main__":
    unittest.main()
