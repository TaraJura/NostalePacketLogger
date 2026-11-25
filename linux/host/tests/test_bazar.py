"""Tests for the NosBazar config, packet parsing and monitor/relist loop."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from ntpl.bazar import (
    BazarConfig,
    BazarController,
    BazarError,
    BazarItem,
    BazarQuote,
    SellerEntry,
    build_change_packet,
    build_open_packets,
    build_register_packet,
    build_search_packet,
    calculate_tax,
    load_config,
    normalize_search_packet,
    parse_rc_blist,
    parse_rc_slist,
    monitor_source,
    save_config,
    search_vnums,
)
from ntpl.engine import Engine, Entity
from ntpl.protocol import Kind, Packet


def _response(
    price: int, seller: str = "Seller", vnum: str = "x"
) -> str:
    # vnum stays non-numeric so the controller's cross-talk filter (which
    # compares the answer's vnums with the searched vnums) is a no-op here.
    return (
        f"rc_blist 0 1|5119|{seller}|{vnum}|5|0|{price}|0"
    )


class RcBlistTests(unittest.TestCase):
    def test_ok_reads_price_and_seller(self) -> None:
        response = parse_rc_blist(_response(13798, "†Artemisa†"))
        self.assertEqual(response.status, "ok")
        cheapest = response.cheapest
        self.assertIsNotNone(cheapest)
        assert cheapest is not None
        self.assertEqual(cheapest.price, 13798)
        self.assertEqual(cheapest.seller, "†Artemisa†")
        self.assertEqual(cheapest.raw_fields[1], "5119")

    def test_empty_and_bad(self) -> None:
        self.assertEqual(parse_rc_blist("rc_blist 0").status, "empty")
        self.assertEqual(parse_rc_blist("rc_blist 0  ").status, "empty")
        self.assertEqual(parse_rc_blist("rc_blist 0 1|2|3").status, "bad")
        self.assertEqual(
            parse_rc_blist("rc_blist 0 1|2|3|4|5|6|x|7").status, "bad"
        )

    def test_live_15_field_entry(self) -> None:
        line = (
            "rc_blist 0 8950|74|Storm|9|1|0|50000|750|0|0|5|0|0|0|"
            "1^9^5^0^0^6^48^52^44^2^70^0 13175|1696|Chaotic|1285|45|0|"
            "4495|1300|1|1|0|0|0|0|^0"
        )
        response = parse_rc_blist(line)
        self.assertEqual(response.status, "ok")
        self.assertEqual(response.page, 0)
        cheapest = response.cheapest
        assert cheapest is not None
        # The answer is grouped by vnum; the minimum visible price is the
        # second entry (Nostale-NB would use the first).
        self.assertEqual(cheapest.price, 4495)
        self.assertEqual(cheapest.seller, "Chaotic")
        self.assertEqual(cheapest.vnum, 1285)
        self.assertEqual(cheapest.amount, 45)
        self.assertFalse(response.truncated)

    def test_truncated_tail_is_ignored(self) -> None:
        # The DLL ring copies at most 2048 bytes, cutting the last entry.
        line = (
            "rc_blist 0 1001|2002|Seller|2174|5|0|13798|120|0|0|0|0|0|0|"
            "^0 999|1|"
        )
        response = parse_rc_blist(line)
        self.assertEqual(response.status, "ok")
        self.assertTrue(response.truncated)
        self.assertEqual(len(response.listings), 1)
        self.assertEqual(response.cheapest.price, 13798)

    def test_double_spaces_are_tolerated(self) -> None:
        response = parse_rc_blist(
            "rc_blist  0  1|5119|Seller|2174|5|0|13798|0"
        )
        self.assertEqual(response.status, "ok")
        self.assertEqual(response.page, 0)
        self.assertEqual(response.cheapest.price, 13798)

    def test_not_a_response(self) -> None:
        with self.assertRaises(BazarError):
            parse_rc_blist("c_blist 0 0")


class RcSlistTests(unittest.TestCase):
    def test_parse_live_seller_list(self) -> None:
        entries = parse_rc_slist(
            "rc_slist 0  1 60891|7001|5370|0|5|0|84449|1|1341|2|1|0|0|0|0|^0 "
            "60003|7001|2174|1|10|0|11494|1|1279|2|2|0|0|0|0|^0"
        )
        self.assertEqual(len(entries), 2)
        first = entries[0]
        self.assertEqual(
            (
                first.listing_id,
                first.owner_id,
                first.vnum,
                first.sold,
                first.amount,
                first.price,
            ),
            (60891, 7001, 5370, 0, 5, 84449),
        )
        # Nothing sold → the in-game Change action is available.
        self.assertTrue(first.changeable)
        self.assertEqual(entries[1].sold, 1)
        self.assertFalse(entries[1].changeable)

    def test_truncated_and_bad_tokens_are_skipped(self) -> None:
        entries = parse_rc_slist(
            "rc_slist 0  0 60891|7001|5370|0|5|0|84449|1| 999|x|"
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].listing_id, 60891)
        with self.assertRaises(BazarError):
            parse_rc_slist("rc_blist 0")

    def test_change_packet(self) -> None:
        entry = SellerEntry(60891, 7001, 5370, 0, 5, 84449, 1)
        self.assertEqual(
            build_change_packet(entry, 84448),
            "c_mod 0 1 60891 5370 5 84448",
        )
        with self.assertRaises(BazarError):
            build_change_packet(entry, 0)


class PacketBuilderTests(unittest.TestCase):
    def test_search_packet(self) -> None:
        packet = build_search_packet([1261, 2174, 0, -2])
        self.assertEqual(
            packet, "c_blist 0 0 0 0 0 0 0 0 2 1261 2174"
        )
        self.assertEqual(search_vnums(packet), [1261, 2174])

    def test_normalize(self) -> None:
        packet = normalize_search_packet(
            "  c_blist  0 0 0 0 0 0 0 0 2 1261 2174 "
        )
        self.assertEqual(
            packet, "c_blist 0 0 0 0 0 0 0 0 2 1261 2174"
        )
        with self.assertRaises(BazarError):
            normalize_search_packet("walk 1 2 0 10")
        with self.assertRaises(BazarError):
            normalize_search_packet("c_blist 0 0 0 0 0 0 0 0 2 1")
        with self.assertRaises(BazarError):
            normalize_search_packet("c_blist 0 0 0 0 0 0 0 0 0")

    def test_open_packets(self) -> None:
        self.assertEqual(
            build_open_packets(10188),
            [
                "npc_req 2 10188",
                "n_run 60 0 2 10188",
                "c_blist 0 0 0 0 0 0 0 0 0",
                "c_slist 0 0 0",
            ],
        )

    def test_register_packet_and_tax(self) -> None:
        item = BazarItem(
            "bubbl",
            build_search_packet([2174]),
            vnum=2174,
            inv_tab=2,
            amount=5,
            min_price=43000,
        )
        # 5 x 14,999 = 74,995 -> 37 gold, below the server's 50 minimum.
        self.assertEqual(calculate_tax(14999, 5), 50)
        self.assertEqual(calculate_tax(10999, 10), 54)
        self.assertEqual(
            build_register_packet(item, 7, 14999),
            "c_reg 0 2 7 9 4 1 0 5 14999 50 2 0",
        )
        with self.assertRaises(BazarError):
            build_register_packet(item, 7, 0)


class ItemConfigTests(unittest.TestCase):
    def test_cost_prefers_nd(self) -> None:
        item = BazarItem("x", nos_cost=0.5, npc_cost=300)
        self.assertEqual(item.cost(), (0.5, "ND"))
        self.assertEqual(item.cost_text(), "0.5 ND")
        self.assertAlmostEqual(item.ratio(13798) or 0, 27596.0)
        self.assertEqual(BazarItem("y", npc_cost=300).cost_text(), "300 g")
        self.assertEqual(BazarItem("z").cost_text(), "-")

    def test_monitor_source_follows_the_cost_unit(self) -> None:
        self.assertEqual(
            monitor_source(BazarItem("nd", nos_cost=0.5)), "nosmall"
        )
        self.assertEqual(
            monitor_source(BazarItem("npc", npc_cost=300)), "npc"
        )
        # ND wins when a hand-edited file has both.
        self.assertEqual(
            monitor_source(
                BazarItem("both", nos_cost=1, npc_cost=300)
            ),
            "nosmall",
        )
        self.assertEqual(monitor_source(BazarItem("free")), "nosmall")
        self.assertEqual(
            monitor_source(BazarItem("zero", nos_cost=0, npc_cost=0)),
            "nosmall",
        )

    def test_round_trip(self) -> None:
        config = BazarConfig(
            character="root1",
            refresh=45,
            npc_id=10188,
            items=[
                BazarItem(
                    "bubbl",
                    build_search_packet([2174]),
                    vnum=2174,
                    inv_tab=2,
                    min_price=43000,
                    nos_cost=0.5,
                ),
                BazarItem("free", build_search_packet([1261])),
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bazar.json"
            save_config(path, config)
            loaded = load_config(path)
        self.assertEqual(loaded.character, "root1")
        self.assertEqual(loaded.refresh, 45)
        self.assertEqual(loaded.npc_id, 10188)
        self.assertEqual([item.name for item in loaded.items], ["bubbl", "free"])
        self.assertEqual(loaded.items[0].vnums, [2174])

    def test_relist_flag_defaults_from_the_floor(self) -> None:
        # An old file has no flag: a positive floor meant "relist this".
        with_floor = BazarItem.from_dict(
            {
                "name": "a",
                "search_packet": build_search_packet([1]),
                "vnum": 1,
                "inv_tab": 1,
                "min_price": 500,
            }
        )
        monitor_only = BazarItem.from_dict({"name": "b", "min_price": 0})
        self.assertTrue(with_floor.relist)
        self.assertFalse(monitor_only.relist)
        # An explicit flag wins, so a floor can be kept while paused.
        paused = BazarItem.from_dict(
            {"name": "c", "min_price": 500, "relist": False}
        )
        self.assertFalse(paused.relist)
        self.assertFalse(paused.to_dict()["relist"])

    def test_invalid_config(self) -> None:
        with self.assertRaises(BazarError):
            BazarConfig.from_dict({"items": [{"name": "a"}, {"name": "a"}]})
        with self.assertRaises(BazarError):
            BazarConfig.from_dict({"items": [{"name": ""}]})
        with self.assertRaises(BazarError):
            BazarConfig.from_dict({"items": [{"name": "x", "inv_tab": 9}]})
        disabled = BazarConfig.from_dict({"items": [{"name": "x"}]})
        self.assertIsNone(disabled.items[0].search_packet)


class EngineInventoryTests(unittest.TestCase):
    def test_inventory_and_gold(self) -> None:
        engine = Engine()
        engine.feed_text("RECV", "inv 2 0.2115.5 1.2282.11 2.0.0")
        slot = engine.world.find_inventory(2, 2282)
        self.assertIsNotNone(slot)
        assert slot is not None
        self.assertEqual((slot.index, slot.quantity), (1, 11))
        engine.feed_text("RECV", "ivn 2 1.2282.10.0")
        self.assertEqual(engine.world.find_inventory(2, 2282).quantity, 10)
        # The fourth standard-bag field is an unknown always-zero value; the
        # picker must not show it as an equipment field.
        self.assertEqual(
            engine.world.find_inventory(2, 2282).fields, ()
        )
        engine.feed_text("RECV", "ivn 2 1.2282.0.0")
        self.assertIsNone(engine.world.find_inventory(2, 2282))
        engine.feed_text("RECV", "inv 2")
        self.assertEqual(engine.world.inventory[2], {})
        engine.feed_text("RECV", "gold 34661 0")
        self.assertEqual(engine.world.gold, 34661)

    def test_equipment_slot_keeps_fields_and_negative_vnum(self) -> None:
        engine = Engine()
        # Live capture: bag 0 (Equip) has seven fields per token, and a
        # negative vnum is a limited/bound item.
        engine.feed_text(
            "RECV", "inv 0 42.884.0.0.0.0.0 63.-4962.5.7.1.0.0"
        )
        slot = engine.world.inventory[0][63]
        self.assertEqual((slot.vnum, slot.quantity), (-4962, 5))
        self.assertEqual(slot.amount, 5)
        self.assertEqual(slot.fields, ("7", "1", "0", "0"))
        # Stored for display, but never a relist candidate.
        self.assertIsNone(engine.world.find_inventory(0, -4962))

    def test_equipment_quantity_zero_is_an_occupied_slot(self) -> None:
        engine = Engine()
        engine.feed_text("RECV", "inv 0 30.885.0.0.0.0.0")
        slot = engine.world.find_inventory(0, 885)
        self.assertIsNotNone(slot)
        assert slot is not None
        self.assertEqual((slot.quantity, slot.amount), (0, 1))
        # An equipment removal is vnum 0, not quantity 0.
        engine.feed_text("RECV", "ivn 0 30.0.0.0.0.0.0")
        self.assertIsNone(engine.world.find_inventory(0, 885))
        engine.feed_text("RECV", "ivn 0 30.885.2.1.2.3.4")
        slot = engine.world.find_inventory(0, 885)
        self.assertIsNotNone(slot)
        assert slot is not None
        self.assertEqual((slot.quantity, slot.amount), (2, 2))
        self.assertEqual(slot.fields, ("1", "2", "3", "4"))

    def test_standard_bag_negative_vnum_is_kept_but_not_relistable(self) -> None:
        engine = Engine()
        engine.feed_text("RECV", "inv 1 0.-1247.23")
        slot = engine.world.inventory[1][0]
        self.assertEqual((slot.vnum, slot.quantity), (-1247, 23))
        self.assertEqual(slot.amount, 23)
        self.assertIsNone(engine.world.find_inventory(1, -1247))

    def test_self_name(self) -> None:
        engine = Engine()
        engine.feed_text(
            "RECV",
            "in 1 root1 - 7001 2 2 2 0 0 0 8 3",
        )
        engine.feed_text("RECV", "at 7001 1 10 29 2 0 0 0 0 0")
        self.assertEqual(engine.world.self_name, "root1")

    def test_learning_the_bazaar_npc(self) -> None:
        engine = Engine()
        engine.feed_text("SEND", "npc_req 2 9584")
        engine.feed_text("SEND", "n_run 16 1 2 9584")
        engine.feed_text("SEND", "c_blist 0 0 0 0 0 0 0 0 0")
        engine.feed_text("RECV", _response(1000))
        state = engine.world.bazar
        self.assertTrue(state.open)
        self.assertEqual(state.npc_id, 9584)
        self.assertEqual((state.open_option, state.open_page), (16, 1))
        self.assertTrue(state.option_learned)
        self.assertEqual(state.response_count, 1)

    def test_map_change_resets_bazar(self) -> None:
        engine = Engine()
        engine.feed_text("SEND", "npc_req 2 9584")
        engine.feed_text("RECV", _response(1000))
        engine.feed_text("RECV", "c_map 0 46 1")
        self.assertFalse(engine.world.bazar.open)
        self.assertIsNone(engine.world.bazar.npc_id)


class Clock:
    def __init__(self) -> None:
        # The engine stamps responses with time.monotonic(); start the fake
        # clock on the same domain so freshness checks are meaningful.
        self.now = time.monotonic()

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ControllerTests(unittest.TestCase):
    def _controller(
        self,
        items: list[BazarItem],
        character: str = "",
        vnum_name=None,
    ) -> tuple[
        BazarController, Engine, Clock, list[str]
    ]:
        sent: list[str] = []

        def send(packet: str) -> bool:
            sent.append(packet)
            return True

        clock = Clock()
        controller = BazarController(
            send, clock=clock, vnum_name=vnum_name
        )
        controller.set_items(items, character=character, refresh=30)
        engine = Engine()
        engine.world.bazar.npc_id = 9584
        return controller, engine, clock, sent

    def _open(self, controller, engine, clock, relist: bool = False) -> None:
        if relist:
            controller.start_relist(engine.world)
        else:
            controller.start_monitor(engine.world)
        controller.tick(engine.world)
        engine.feed_text("RECV", "npc_req 2 9584 460")
        clock.advance(0.31)
        controller.tick(engine.world)
        clock.advance(0.31)
        controller.tick(engine.world)
        engine.feed_text("RECV", "wopen 32 2 642")
        engine.feed(
            Packet(Kind.RECV, _response(1000), clock.now)
        )
        controller.tick(engine.world)
        if relist:
            # A relist pass asks the seller tab for our own listings before
            # its first search; answer it so the pass starts immediately.
            engine.feed_text("RECV", "rc_slist 0  0")

    def test_open_sequence_and_one_search(self) -> None:
        item = BazarItem("bubbl", build_search_packet([2174]))
        controller, engine, clock, sent = self._controller([item])
        controller.start_monitor(engine.world)
        controller.tick(engine.world)
        self.assertEqual(sent, ["npc_req 2 9584"])
        engine.feed_text("RECV", "npc_req 2 9584 460")
        clock.advance(0.31)
        controller.tick(engine.world)
        self.assertEqual(sent[-1], "n_run 60 0 2 9584")
        clock.advance(0.31)
        controller.tick(engine.world)
        self.assertEqual(sent[-2:], [
            "c_blist 0 0 0 0 0 0 0 0 0",
            "c_slist 0 0 0",
        ])
        engine.feed_text("RECV", "wopen 32 2 642")
        engine.feed(Packet(Kind.RECV, _response(1000), clock.now))
        controller.tick(engine.world)
        controller.tick(engine.world)
        self.assertEqual(sent[-1], "c_blist 0 0 0 0 0 0 0 0 1 2174")
        engine.feed(Packet(Kind.RECV, _response(13798), clock.now))
        controller.tick(engine.world)
        quote = controller.results["bubbl"]
        self.assertEqual((quote.status, quote.price, quote.seller), (
            "ok", 13798, "Seller"
        ))

    def test_open_requires_the_bazaar_window(self) -> None:
        # npc_req replies with the Carve Runes dialog (11009): its option
        # must never be clicked, and the search answer alone is no proof.
        item = BazarItem("bubbl", build_search_packet([2174]))
        controller, engine, clock, sent = self._controller([item])
        controller.start_monitor(engine.world)
        controller.tick(engine.world)  # npc_req
        engine.feed_text("RECV", "npc_req 2 9584 11009")
        clock.advance(0.31)
        controller.tick(engine.world)
        self.assertEqual(controller.phase, "waiting")
        self.assertIn("dialog", controller.last_error)
        self.assertNotIn("n_run 60 0 2 9584", sent)

    def test_already_open_bazaar_continues_without_wopen(self) -> None:
        # The bazaar was opened before this host attached, so the server
        # sends no fresh wopen 32 (live 2026-11-23). The verified Bibi
        # dialog plus the listing answer must start the pass anyway.
        item = BazarItem("bubbl", build_search_packet([2174]))
        controller, engine, clock, sent = self._controller([item])
        controller.start_monitor(engine.world)
        controller.tick(engine.world)  # npc_req
        engine.feed_text("RECV", "npc_req 2 9584 460")
        clock.advance(0.31)
        controller.tick(engine.world)  # n_run
        clock.advance(0.31)
        controller.tick(engine.world)  # c_blist + c_slist
        engine.feed(Packet(Kind.RECV, _response(1000), clock.now))
        controller.tick(engine.world)
        self.assertEqual(controller.phase, "pass")
        self.assertTrue(engine.world.bazar.window_open)
        controller.tick(engine.world)
        self.assertIn("c_blist 0 0 0 0 0 0 0 0 1 2174", sent)

    def test_register_requires_an_open_bazaar_window(self) -> None:
        item = BazarItem(
            "bubbl",
            build_search_packet([2174]),
            vnum=2174,
            inv_tab=2,
            amount=5,
            min_price=1,
        )
        controller, engine, clock, sent = self._controller(
            [item], character="root1"
        )
        engine.feed_text("RECV", "inv 2 7.2174.5")
        engine.world.bazar.window_open = False
        quote = BazarQuote(item=item, price=44000, seller="Other")
        controller._maybe_relist(engine.world, item, quote, clock.now)
        self.assertEqual(quote.status, "no-window")
        self.assertEqual(
            [packet for packet in sent if packet.startswith("c_reg ")], []
        )

    def test_medal_opens_the_bazaar_without_a_bibi(self) -> None:
        # No Bibi entity: use the portable NosBazar medal from Etc slot 3
        # instead of ever replaying the stale NPC id.
        item = BazarItem("bubbl", build_search_packet([2174]))
        controller, engine, clock, sent = self._controller([item])
        engine.world.self_id = 7001
        engine.feed_text("RECV", "inv 2 3.1955.1.0")
        controller.start_monitor(engine.world)
        self.assertEqual(
            [packet for packet in sent if packet.startswith("npc_req ")], []
        )
        self.assertIn("u_i 1 7001 2 3 0 0", sent)
        # The medal opens the window itself; a fresh wopen 32 is enough.
        engine.feed_text("RECV", "wopen 32 2 642")
        controller.tick(engine.world)
        self.assertEqual(controller.phase, "pass")
        controller.tick(engine.world)
        self.assertIn("c_blist 0 0 0 0 0 0 0 0 1 2174", sent)

    def test_client_shortcut_opens_the_bazaar_without_an_npc(self) -> None:
        # FrosTale: no Bibi entity and no medal; the client's own shortcut
        # (c_skill) is the learned opener and a fresh wopen 32 confirms it.
        item = BazarItem("bubbl", build_search_packet([2174]))
        controller, engine, clock, sent = self._controller([item])
        engine.world.bazar.open_skill = True
        controller.start_monitor(engine.world)
        self.assertIn("c_skill", sent)
        self.assertEqual(controller.phase, "open")
        self.assertTrue(controller.running)
        engine.feed_text("RECV", "wopen 32 2 642")
        controller.tick(engine.world)
        self.assertEqual(controller.phase, "pass")
        controller.tick(engine.world)
        self.assertIn("c_blist 0 0 0 0 0 0 0 0 1 2174", sent)

    def test_visible_bibi_wins_over_the_medal(self) -> None:
        item = BazarItem("bubbl", build_search_packet([2174]))
        controller, engine, clock, sent = self._controller([item])
        engine.world.self_id = 7001
        engine.world.entities[9584] = Entity(
            9584, 2, vnum=793, x=10, y=10
        )
        engine.feed_text("RECV", "inv 2 3.1955.1.0")
        controller.start_monitor(engine.world)
        controller.tick(engine.world)
        self.assertEqual(sent, ["npc_req 2 9584"])

    def test_known_non_bibi_npc_id_is_never_opened(self) -> None:
        # Entity ids are per map: the learned id now points at Carve Runes
        # (live 2026-11-23). Pressing Start must wait, not open that dialog.
        item = BazarItem("bubbl", build_search_packet([2174]))
        controller, engine, clock, sent = self._controller([item])
        controller.config.npc_id = 54483
        engine.world.bazar.npc_id = 54483
        engine.world.entities[54483] = Entity(
            54483, 2, vnum=9999, x=10, y=10
        )
        controller.start_monitor(engine.world)
        self.assertTrue(controller.running)
        self.assertEqual(controller.phase, "waiting")
        self.assertIn("not visible", controller.last_error)
        self.assertEqual(sent, [])

    def test_search_pacing(self) -> None:
        first = BazarItem("a", build_search_packet([1]))
        second = BazarItem("b", build_search_packet([2]))
        controller, engine, clock, sent = self._controller([first, second])
        self._open(controller, engine, clock)
        controller.tick(engine.world)
        searches = [packet for packet in sent if packet.startswith("c_blist 0 0 0 0 0 0 0 0 1 ")]
        self.assertEqual(len(searches), 1)
        engine.feed(Packet(Kind.RECV, _response(100), clock.now))
        controller.tick(engine.world)
        clock.advance(1.0)
        controller.tick(engine.world)
        searches = [packet for packet in sent if packet.startswith("c_blist 0 0 0 0 0 0 0 0 1 ")]
        self.assertEqual(len(searches), 1)
        clock.advance(1.6)
        controller.tick(engine.world)
        searches = [packet for packet in sent if packet.startswith("c_blist 0 0 0 0 0 0 0 0 1 ")]
        self.assertEqual(len(searches), 2)

    def test_two_answers_between_ticks_keep_our_own(self) -> None:
        item = BazarItem("a", build_search_packet([1285]))
        controller, engine, clock, _sent = self._controller([item])
        self._open(controller, engine, clock)
        controller.tick(engine.world)  # our search is pending
        engine.feed(Packet(Kind.RECV, _response(111, vnum="9999"), clock.now))
        engine.feed(Packet(Kind.RECV, _response(222, vnum="1285"), clock.now))
        controller.tick(engine.world)
        self.assertEqual(controller.results["a"].price, 222)

    def test_foreign_bazaar_answer_is_ignored(self) -> None:
        item = BazarItem("a", build_search_packet([1285]))
        controller, engine, clock, _sent = self._controller([item])
        self._open(controller, engine, clock)
        controller.tick(engine.world)  # our search is pending
        engine.feed(
            Packet(
                Kind.RECV,
                _response(111, vnum="9999"),
                clock.now,
            )
        )
        controller.tick(engine.world)
        self.assertEqual(controller.results["a"].status, "idle")
        engine.feed(
            Packet(
                Kind.RECV,
                _response(222, vnum="1285"),
                clock.now,
            )
        )
        controller.tick(engine.world)
        self.assertEqual(controller.results["a"].price, 222)

    #: Item.dat names of the four partner skill ticket vnums. The game's
    #: NosMall data merges the single and all variants under one base vnum,
    #: so a Nostale-NB style config searches all four in one group while the
    #: row is named after the first one.
    TICKET_NAMES = {
        5931: "Partner Skill Ticket (Single)",
        5932: "Partner Skill Ticket (All)",
        9109: "Partner Skill Ticket (Single)",
        9110: "Partner Skill Ticket (All)",
    }

    def test_monitor_narrows_the_search_to_the_rows_item_name(self) -> None:
        item = BazarItem(
            "ner skill ticket",
            build_search_packet([5931, 5932, 9109, 9110]),
            vnum=5931,
        )
        controller, engine, clock, sent = self._controller(
            [item], vnum_name=self.TICKET_NAMES.get
        )
        self._open(controller, engine, clock)
        controller.tick(engine.world)  # send the search
        self.assertEqual(
            sent[-1], "c_blist 0 0 0 0 0 0 0 0 2 5931 9109"
        )

    def test_monitor_ignores_a_cheaper_group_sibling(self) -> None:
        # The (All) ticket is cheaper, but this row is the (Single) ticket.
        item = BazarItem(
            "ner skill ticket",
            build_search_packet([5931, 5932, 9109, 9110]),
            vnum=5931,
        )
        controller, engine, clock, _sent = self._controller(
            [item], vnum_name=self.TICKET_NAMES.get
        )
        self._open(controller, engine, clock)
        controller.tick(engine.world)  # send the narrowed search
        engine.feed(
            Packet(
                Kind.RECV,
                "rc_blist 0 "
                "1|1|AllSeller|5932|5|0|50000|0 "
                "2|2|SingleSeller|9109|5|0|70000|0",
                clock.now,
            )
        )
        controller.tick(engine.world)
        quote = controller.results["ner skill ticket"]
        self.assertEqual(
            (quote.status, quote.price, quote.seller),
            ("ok", 70000, "SingleSeller"),
        )
        assert quote.listing is not None
        self.assertEqual(quote.listing.vnum, 9109)

    def test_monitor_keeps_same_name_aliases_competing(self) -> None:
        # 5931 and 9109 are the same item; the cheaper listing wins.
        item = BazarItem(
            "ner skill ticket",
            build_search_packet([5931, 9109]),
            vnum=5931,
        )
        controller, engine, clock, sent = self._controller(
            [item], vnum_name=self.TICKET_NAMES.get
        )
        self._open(controller, engine, clock)
        controller.tick(engine.world)
        self.assertEqual(
            sent[-1], "c_blist 0 0 0 0 0 0 0 0 2 5931 9109"
        )
        engine.feed(
            Packet(
                Kind.RECV,
                "rc_blist 0 "
                "1|1|A|5931|5|0|70000|0 "
                "2|2|B|9109|5|0|60000|0",
                clock.now,
            )
        )
        controller.tick(engine.world)
        quote = controller.results["ner skill ticket"]
        self.assertEqual((quote.status, quote.price), ("ok", 60000))

    def test_monitor_answer_with_only_another_items_listing_is_empty(
        self,
    ) -> None:
        # Item names load asynchronously. The search went out with the whole
        # saved group; the answer carries only the (All) ticket, so the
        # (Single) row must show "no listings", not the (All) price.
        names: dict[int, str] = {}
        item = BazarItem(
            "ner skill ticket",
            build_search_packet([5931, 5932]),
            vnum=5931,
        )
        controller, engine, clock, sent = self._controller(
            [item], vnum_name=names.get
        )
        self._open(controller, engine, clock)
        controller.tick(engine.world)
        self.assertEqual(
            sent[-1], "c_blist 0 0 0 0 0 0 0 0 2 5931 5932"
        )
        names.update(self.TICKET_NAMES)
        engine.feed(
            Packet(
                Kind.RECV,
                "rc_blist 0 1|1|AllSeller|5932|5|0|50000|0",
                clock.now,
            )
        )
        controller.tick(engine.world)
        quote = controller.results["ner skill ticket"]
        self.assertEqual((quote.status, quote.price), ("empty", None))

    def test_group_minimum_is_kept_without_a_name_resolver(self) -> None:
        # Headless callers without an item-name lookup keep the documented
        # group minimum (the GUI always supplies the resolver).
        item = BazarItem(
            "ner skill ticket",
            build_search_packet([5931, 5932]),
            vnum=5931,
        )
        controller, engine, clock, sent = self._controller([item])
        self._open(controller, engine, clock)
        controller.tick(engine.world)
        self.assertEqual(
            sent[-1], "c_blist 0 0 0 0 0 0 0 0 2 5931 5932"
        )
        engine.feed(
            Packet(
                Kind.RECV,
                "rc_blist 0 "
                "1|1|SingleSeller|5931|5|0|70000|0 "
                "2|2|AllSeller|5932|5|0|50000|0",
                clock.now,
            )
        )
        controller.tick(engine.world)
        quote = controller.results["ner skill ticket"]
        self.assertEqual((quote.status, quote.price), ("ok", 50000))

    def test_total_timeout_drops_to_waiting(self) -> None:
        item = BazarItem("a", build_search_packet([1]))
        controller, engine, clock, _sent = self._controller([item])
        self._open(controller, engine, clock)
        controller.tick(engine.world)  # send search
        clock.advance(5.1)
        controller.tick(engine.world)  # main pass timeout
        controller.tick(engine.world)  # finish -> retry queue
        clock.advance(2.1)
        controller.tick(engine.world)  # retry search
        clock.advance(5.1)
        controller.tick(engine.world)  # retry timeout
        controller.tick(engine.world)  # finish -> all failed
        self.assertEqual(controller.phase, "waiting")
        self.assertFalse(engine.world.bazar.open)
        self.assertEqual(controller.results["a"].status, "timeout")

    def test_relist_mode_searches_only_the_relist_set(self) -> None:
        tracked = BazarItem(
            "bubbl",
            build_search_packet([2174]),
            vnum=2174,
            inv_tab=2,
            amount=5,
            min_price=5000,
        )
        watched = BazarItem(
            "flask", build_search_packet([2282]), relist=False
        )
        controller, engine, clock, sent = self._controller(
            [tracked, watched], character="root1"
        )
        self.assertEqual(
            [item.name for item in controller.relist_items()], ["bubbl"]
        )
        self._open(controller, engine, clock, relist=True)
        controller.tick(engine.world)  # send the first (only) search
        searches = [
            packet
            for packet in sent
            if packet.startswith("c_blist 0 0 0 0 0 0 0 0 1 ")
        ]
        self.assertEqual(searches, ["c_blist 0 0 0 0 0 0 0 0 1 2174"])

    def test_item_out_of_the_relist_set_never_sends_register(self) -> None:
        item = BazarItem(
            "watched",
            build_search_packet([2282]),
            vnum=2282,
            inv_tab=2,
            amount=1,
            min_price=100,
            relist=False,
        )
        controller, engine, clock, sent = self._controller(
            [item], character="root1"
        )
        engine.feed_text("RECV", "inv 2 0.2282.1")
        quote = BazarQuote(item=item, price=5000, seller="Seller")
        controller._maybe_relist(engine.world, item, quote, clock.now)
        self.assertEqual(quote.status, "not-selected")
        self.assertEqual(
            [packet for packet in sent if packet.startswith("c_reg ")], []
        )

    def test_relist_undercuts_and_respects_floor(self) -> None:
        item = BazarItem(
            "bubbl",
            build_search_packet([2174]),
            vnum=2174,
            inv_tab=2,
            amount=5,
            min_price=43000,
        )
        controller, engine, clock, sent = self._controller(
            [item], character="root1"
        )
        engine.feed_text("RECV", "inv 2 7.2174.5")
        self._open(controller, engine, clock, relist=True)
        controller.tick(engine.world)
        engine.feed(
            Packet(Kind.RECV, _response(44000, "Other"), clock.now)
        )
        controller.tick(engine.world)
        register = [p for p in sent if p.startswith("c_reg ")]
        self.assertEqual(
            register, ["c_reg 0 2 7 9 4 1 0 5 43999 109 2 0"]
        )
        # Sent is not accepted: the server answers with rc_reg.
        self.assertEqual(
            controller.results["bubbl"].status, "register-pending"
        )
        engine.feed_text("RECV", "rc_reg 1")
        controller.tick(engine.world)
        self.assertEqual(
            controller.results["bubbl"].status, "register-sent"
        )
        self.assertIn("rc_reg 1", controller.results["bubbl"].detail)

    def test_relist_changes_an_unsold_listing_instead_of_adding(self) -> None:
        # The seller tab shows our 2174 listing (id 73411) with nothing sold:
        # the in-game Change action exists, so repricing uses c_mod and no new
        # listing is added — even with none of the item left in the bag.
        item = BazarItem(
            "bubbl",
            build_search_packet([2174]),
            vnum=2174,
            inv_tab=2,
            amount=5,
            min_price=10000,
        )
        controller, engine, clock, sent = self._controller(
            [item], character="root1"
        )
        engine.world.self_id = 7001
        self._open(controller, engine, clock, relist=True)
        engine.feed_text(
            "RECV",
            "rc_slist 0  0 73411|7001|2174|0|5|0|11496|1|1435|2|2|0",
        )
        controller.tick(engine.world)  # ends the seller-list wait
        engine.feed(
            Packet(Kind.RECV, _response(11000, "Other"), clock.now)
        )
        controller.tick(engine.world)
        self.assertEqual(
            [p for p in sent if p.startswith("c_mod ")],
            ["c_mod 0 1 73411 2174 5 10999"],
        )
        self.assertEqual(
            [p for p in sent if p.startswith("c_reg ")], []
        )
        self.assertEqual(
            controller.results["bubbl"].status, "change-pending"
        )
        # The server answers a c_mod with a refreshed seller list.
        engine.feed_text(
            "RECV",
            "rc_slist 0  0 73411|7001|2174|0|5|0|10999|1|1435|2|2|0",
        )
        controller.tick(engine.world)
        self.assertEqual(controller.results["bubbl"].status, "changed")
        self.assertIn("c_mod", controller.results["bubbl"].detail)

    def test_relist_adds_when_the_listing_has_sold(self) -> None:
        # Something from our listing sold: the Change action is gone, so a
        # new c_reg is the only way to bring the price to the market.
        item = BazarItem(
            "bubbl",
            build_search_packet([2174]),
            vnum=2174,
            inv_tab=2,
            amount=5,
            min_price=10000,
        )
        controller, engine, clock, sent = self._controller(
            [item], character="root1"
        )
        engine.world.self_id = 7001
        engine.feed_text("RECV", "inv 2 7.2174.5")
        self._open(controller, engine, clock, relist=True)
        engine.feed_text(
            "RECV",
            "rc_slist 0  0 73411|7001|2174|2|5|0|11496|1|1435|2|2|0",
        )
        controller.tick(engine.world)
        engine.feed(
            Packet(Kind.RECV, _response(11000, "Other"), clock.now)
        )
        controller.tick(engine.world)
        self.assertEqual([p for p in sent if p.startswith("c_mod ")], [])
        self.assertEqual(
            [p for p in sent if p.startswith("c_reg ")],
            ["c_reg 0 2 7 9 4 1 0 5 10999 50 2 0"],
        )

    def test_missing_rc_reg_marks_the_registration_ignored(self) -> None:
        item = BazarItem(
            "bubbl",
            build_search_packet([2174]),
            vnum=2174,
            inv_tab=2,
            amount=5,
            min_price=43000,
        )
        sent: list[str] = []
        logs: list[str] = []
        clock = Clock()
        controller = BazarController(
            lambda packet: sent.append(packet) or True,
            log=logs.append,
            clock=clock,
        )
        controller.set_items([item], character="root1", refresh=30)
        engine = Engine()
        engine.world.bazar.npc_id = 9584
        engine.feed_text("RECV", "inv 2 7.2174.5")
        self._open(controller, engine, clock, relist=True)
        controller.tick(engine.world)
        engine.feed(
            Packet(Kind.RECV, _response(44000, "Other"), clock.now)
        )
        controller.tick(engine.world)
        self.assertTrue([p for p in sent if p.startswith("c_reg ")])
        self.assertEqual(
            controller.results["bubbl"].status, "register-pending"
        )
        clock.advance(3.1)
        controller.tick(engine.world)
        self.assertEqual(
            controller.results["bubbl"].status, "register-no-ack"
        )
        self.assertTrue(any("ignored" in line for line in logs))

    def test_relist_skips_own_listing_user(self) -> None:
        item = BazarItem(
            "bubbl",
            build_search_packet([2174]),
            vnum=2174,
            inv_tab=2,
            amount=5,
            min_price=1,
        )
        controller, engine, clock, sent = self._controller(
            [item], character="root1"
        )
        engine.feed_text("RECV", "inv 2 7.2174.5")
        self._open(controller, engine, clock, relist=True)
        controller.tick(engine.world)
        engine.feed(
            Packet(Kind.RECV, _response(44000, "root1"), clock.now)
        )
        controller.tick(engine.world)
        self.assertFalse([p for p in sent if p.startswith("c_reg ")])
        self.assertEqual(controller.results["bubbl"].status, "own")

    def test_relist_rejects_insufficient_quantity(self) -> None:
        item = BazarItem(
            "bubbl",
            build_search_packet([2174]),
            vnum=2174,
            inv_tab=2,
            amount=5,
            min_price=1,
        )
        controller, engine, clock, sent = self._controller(
            [item], character="root1"
        )
        engine.feed_text("RECV", "inv 2 7.2174.2")
        self._open(controller, engine, clock, relist=True)
        controller.tick(engine.world)
        engine.feed(Packet(Kind.RECV, _response(44000), clock.now))
        controller.tick(engine.world)
        self.assertFalse([p for p in sent if p.startswith("c_reg ")])
        self.assertEqual(controller.results["bubbl"].status, "quantity")

    def test_relist_equipment_quantity_zero_counts_as_one(self) -> None:
        # Equip-bag rows carry quantity 0; the slot still holds one item and
        # c_reg takes its tab/slot, so amount 1 must be accepted.
        item = BazarItem(
            "hat",
            build_search_packet([885]),
            vnum=885,
            inv_tab=0,
            amount=1,
            min_price=1,
        )
        controller, engine, clock, sent = self._controller(
            [item], character="root1"
        )
        engine.feed_text("RECV", "inv 0 30.885.0.0.0.0.0")
        self._open(controller, engine, clock, relist=True)
        controller.tick(engine.world)
        engine.feed(
            Packet(Kind.RECV, _response(44000, "Other"), clock.now)
        )
        controller.tick(engine.world)
        register = [p for p in sent if p.startswith("c_reg ")]
        self.assertEqual(
            register, ["c_reg 0 0 30 9 4 1 0 1 43999 50 2 0"]
        )
        engine.feed_text("RECV", "rc_reg 1")
        controller.tick(engine.world)
        self.assertEqual(
            controller.results["hat"].status, "register-sent"
        )

    def test_relist_rejects_a_cheapest_foreign_vnum(self) -> None:
        item = BazarItem(
            "bubbl",
            build_search_packet([1261, 2174]),
            vnum=2174,
            inv_tab=2,
            amount=5,
            min_price=1,
        )
        controller, engine, clock, sent = self._controller(
            [item], character="root1"
        )
        engine.feed_text("RECV", "inv 2 7.2174.5")
        self._open(controller, engine, clock, relist=True)
        controller.tick(engine.world)
        raw = (
            "rc_blist 0 1|2|Other|9999|5|0|50|0 "
            "1|3|Other|1261|5|0|100|0"
        )
        engine.feed(Packet(Kind.RECV, raw, clock.now))
        controller.tick(engine.world)
        self.assertFalse([p for p in sent if p.startswith("c_reg ")])
        self.assertEqual(controller.results["bubbl"].status, "error")
        self.assertIn("vnum mismatch", controller.results["bubbl"].detail)

    def test_own_listing_with_replacement_character(self) -> None:
        item = BazarItem(
            "bubbl",
            build_search_packet([2174]),
            vnum=2174,
            inv_tab=2,
            amount=5,
            min_price=1,
        )
        controller, engine, clock, sent = self._controller(
            [item], character="Sérox"
        )
        engine.feed_text("RECV", "inv 2 7.2174.5")
        self._open(controller, engine, clock, relist=True)
        controller.tick(engine.world)
        engine.feed(
            Packet(Kind.RECV, _response(44000, "S�rox"), clock.now)
        )
        controller.tick(engine.world)
        self.assertFalse([p for p in sent if p.startswith("c_reg ")])
        self.assertEqual(controller.results["bubbl"].status, "own")

    def test_relist_floor_and_missing_slot(self) -> None:
        item = BazarItem(
            "bubbl",
            build_search_packet([2174]),
            vnum=2174,
            inv_tab=2,
            amount=5,
            min_price=44000,
        )
        controller, engine, clock, sent = self._controller(
            [item], character="root1"
        )
        engine.feed_text("RECV", "inv 2 7.2174.5")
        self._open(controller, engine, clock, relist=True)
        controller.tick(engine.world)
        engine.feed(Packet(Kind.RECV, _response(44000), clock.now))
        controller.tick(engine.world)
        self.assertFalse([p for p in sent if p.startswith("c_reg ")])
        self.assertEqual(controller.results["bubbl"].status, "floor")

        item.min_price = 1
        controller.start_relist(engine.world)
        controller.tick(engine.world)
        # A new cycle keeps the previous row until the new answer arrives.
        self.assertEqual(controller.results["bubbl"].status, "floor")

    def test_relist_requires_character(self) -> None:
        item = BazarItem("a", build_search_packet([1]))
        controller, engine, _clock, _sent = self._controller([item])
        self.assertFalse(controller.start_relist(engine.world))
        self.assertIn("character name", controller.last_error)

    def test_relist_logs_use_the_display_label(self) -> None:
        # The GUI passes the real Item.dat name; the configured shortcut stays
        # the config/result key but must not leak into user-facing log lines.
        logs: list[str] = []
        clock = Clock()
        item = BazarItem("bubbl", build_search_packet([2174]))
        controller = BazarController(
            lambda packet: True,
            log=logs.append,
            clock=clock,
            item_label=lambda candidate: "Speech Bubble",
        )
        controller.set_items([item], character="root1", refresh=30)
        engine = Engine()
        engine.world.bazar.window_open = True
        controller._maybe_relist(
            engine.world, item, BazarQuote(item=item), clock.now
        )
        self.assertEqual(
            logs,
            ["Speech Bubble: not relisted (no item vnum configured)"],
        )

    def test_open_only_stops_after_confirmation(self) -> None:
        item = BazarItem("a", build_search_packet([1]))
        controller, engine, clock, sent = self._controller([item])
        self.assertTrue(controller.open_bazaar(engine.world))
        controller.tick(engine.world)
        engine.feed_text("RECV", "npc_req 2 9584 460")
        clock.advance(0.31)
        controller.tick(engine.world)
        clock.advance(0.31)
        controller.tick(engine.world)
        engine.feed_text("RECV", "wopen 32 2 642")
        engine.feed(Packet(Kind.RECV, "rc_blist 0", clock.now))
        controller.tick(engine.world)
        self.assertEqual(controller.mode, None)
        self.assertEqual(controller.phase, "idle")
        self.assertFalse([p for p in sent if p.startswith("c_blist 0 0 0 0 0 0 0 0 1 ")])

    def test_new_pass_keeps_the_old_price_until_updated(self) -> None:
        item = BazarItem("a", build_search_packet([1]))
        controller, engine, clock, _sent = self._controller([item])
        controller.config.refresh = 5.0
        self._open(controller, engine, clock)
        controller.tick(engine.world)  # search
        engine.feed(Packet(Kind.RECV, _response(100), clock.now))
        controller.tick(engine.world)
        controller.tick(engine.world)  # queue empty -> cooldown
        self.assertEqual(controller.results["a"].price, 100)
        self.assertEqual(controller.phase, "cooldown")

        clock.advance(5.1)
        controller.tick(engine.world)  # begin the next pass
        self.assertEqual(controller.phase, "pass")
        self.assertEqual(controller.results["a"].price, 100)
        self.assertEqual(controller.results["a"].status, "ok")
        controller.tick(engine.world)  # send the new search
        engine.feed(Packet(Kind.RECV, _response(200), clock.now))
        controller.tick(engine.world)
        self.assertEqual(controller.results["a"].price, 200)

    def test_timeout_keeps_the_last_price_as_stale(self) -> None:
        item = BazarItem("a", build_search_packet([1]))
        controller, engine, clock, _sent = self._controller([item])
        controller.config.refresh = 5.0
        self._open(controller, engine, clock)
        controller.tick(engine.world)
        engine.feed(Packet(Kind.RECV, _response(100), clock.now))
        controller.tick(engine.world)
        controller.tick(engine.world)
        clock.advance(5.1)
        controller.tick(engine.world)  # begin next pass
        controller.tick(engine.world)  # send
        clock.advance(5.1)
        controller.tick(engine.world)  # timeout
        quote = controller.results["a"]
        self.assertEqual(quote.price, 100)
        self.assertEqual(quote.status, "timeout")
        self.assertEqual(quote.detail, "stale price")

    def test_quotes_sort_most_profitable_first(self) -> None:
        controller, _engine, _clock, _sent = self._controller([])
        items = [
            BazarItem("low", build_search_packet([1]), nos_cost=2.0),
            BazarItem("high", build_search_packet([2]), nos_cost=0.5),
            BazarItem("no-cost", build_search_packet([3])),
            BazarItem("no-price", build_search_packet([4]), nos_cost=1.0),
        ]
        controller.set_items(items)
        controller.results = {
            "low": BazarQuote(items[0], price=100, status="ok"),
            "high": BazarQuote(items[1], price=50, status="ok"),
            "no-cost": BazarQuote(items[2], price=10, status="ok"),
            "no-price": BazarQuote(items[3], status="timeout"),
        }
        self.assertEqual(
            [quote.item.name for quote in controller.sorted_quotes()],
            ["high", "low", "no-cost", "no-price"],
        )

    def test_manual_open_resumes_a_waiting_monitor(self) -> None:
        item = BazarItem("a", build_search_packet([1]))
        controller, engine, clock, _sent = self._controller([item])
        engine.world.bazar.npc_id = None
        controller.start_monitor(engine.world)
        controller.tick(engine.world)
        self.assertEqual(controller.phase, "waiting")
        engine.feed_text("RECV", "wopen 32 2 642")
        engine.feed(Packet(Kind.RECV, "rc_blist 0", clock.now))
        controller.tick(engine.world)
        self.assertEqual(controller.phase, "pass")

    def test_live_bibi_entity_wins_over_a_stale_id(self) -> None:
        item = BazarItem("a", build_search_packet([1]))
        controller, engine, _clock, _sent = self._controller([item])
        engine.world.bazar.npc_id = 222
        engine.world.entities[999] = Entity(999, 2, vnum=793, x=10, y=10)
        engine.world.x, engine.world.y = 10, 11
        self.assertEqual(controller._bibi_candidates(engine.world)[0], 999)

    def test_open_requires_known_npc(self) -> None:
        item = BazarItem("a", build_search_packet([1]))
        controller, engine, _clock, _sent = self._controller([item])
        engine.world.bazar.npc_id = None
        controller.start_monitor(engine.world)
        controller.tick(engine.world)
        self.assertEqual(controller.phase, "waiting")
        self.assertIn("Bibi Basar", controller.last_error)


if __name__ == "__main__":
    unittest.main()
