"""Tests for the local SQLite game/packet database."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ntpl import database
from ntpl.bazar import load_config
from ntpl.monsternames import default_monster_data_path


class PacketExtractionTests(unittest.TestCase):
    def test_csharp_header_and_fields(self) -> None:
        text = """
[PacketHeader("n_run", PacketSource.Client)]
public record NRunPacket
(
    [PacketIndex(0)] NRunType Type,
    [PacketIndex(1)] short SubType,
    [PacketIndex(2)] EntityType EntityType,
    [PacketIndex(3)] long EntityId
);
"""
        packets = database.extract_packets(text, Path("NRunPacket.cs"))
        self.assertTrue(packets)
        packet = packets[0]
        self.assertEqual(packet["opcode"], "n_run")
        self.assertEqual(packet["direction"], "C2S")
        self.assertEqual(
            [field["name"] for field in packet["fields"]],
            ["Type", "SubType", "EntityType", "EntityId"],
        )

    def test_go_name_method(self) -> None:
        text = """
func (p NPCRequest) Name() string {
    return "npc_req"
}
func (p NPCRequest) Type() packets.PacketType { return packets.CLIENT }
"""
        packets = database.extract_packets(
            text, Path("packets/client/npc_req.go")
        )
        self.assertEqual(packets[0]["opcode"], "npc_req")
        self.assertEqual(packets[0]["direction"], "C2S")


class LocalDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not default_monster_data_path().is_file():
            raise unittest.SkipTest("NosTale client data is not installed")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls._tmp.name) / "nostale.db"
        cls.connection = database.connect(cls.path)
        cls.local = database.import_local_game_data(cls.connection)
        cls.wiki = database.import_protocol_docs(cls.connection)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()
        cls._tmp.cleanup()

    def test_local_counts(self) -> None:
        self.assertGreater(self.local["items"], 7000)
        self.assertGreater(self.local["monsters"], 2000)
        self.assertGreater(self.local["skills"], 1900)
        self.assertGreater(self.local["maps"], 700)
        self.assertGreater(self.local["nosmall_records"], 3000)
        self.assertGreater(self.local["nosmall_groups"], 1000)

    def test_item_lookup(self) -> None:
        rows = database.search_items(self.connection, vnum=2174)
        self.assertEqual(rows[0]["name"], "Speech Bubble")

    def test_item_info_names_and_prices(self) -> None:
        info = database.item_info(self.connection)
        self.assertGreater(len(info), 7000)
        self.assertEqual(info[1092].name, "Group Time-Space Piece")
        self.assertEqual(info[1092].npc_price, 3000)
        self.assertEqual(info[2174].name, "Speech Bubble")

    def test_nosmall_group_and_export(self) -> None:
        groups = database.nosmall_groups(
            self.connection, query="Soulstone Blessing", limit=10
        )
        group = next(group for group in groups if group.base_vnum == 1362)
        self.assertIn(5195, group.vnums)
        path = Path(self._tmp.name) / "bazar.json"
        count = database.export_bazar(self.connection, path)
        self.assertGreater(count, 1000)
        config = load_config(path)
        self.assertEqual(len(config.items), count)
        self.assertTrue(config.items[0].search_packet)

    def test_protocol_wiki_packets(self) -> None:
        self.assertGreater(self.wiki, 10)
        rows = database.search_packets(self.connection, opcode="rc_blist")
        self.assertTrue(rows)

    def test_stats(self) -> None:
        counts = database.stats(self.connection)
        self.assertEqual(counts["items"], self.local["items"])
        self.assertEqual(counts["nosmall_groups"], self.local["nosmall_groups"])


if __name__ == "__main__":
    unittest.main()
