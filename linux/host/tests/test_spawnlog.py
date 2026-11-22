import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ntpl.engine import InventorySlot
from ntpl.spawnlog import (
    _log_head_lines,
    _open_log_tail,
    client_log_path,
    default_log_path,
    load_bazaar_open,
    load_entities,
    load_gates,
    load_inventory,
    load_last_position,
    log_inventory_bags,
    log_was_truncated,
    load_player_families,
    load_player_metadata,
    load_player_spawns,
    load_portals,
    load_skill_list,
    load_skill_slots,
    load_spawns,
    load_timespaces,
)


class SpawnLogTests(unittest.TestCase):
    def _write(self, text: str) -> Path:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="latin1"
        )
        with handle:
            handle.write(text)
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return Path(handle.name)

    def test_tail_read_skips_the_old_part(self):
        log = self._write("OLD\n" * 1000 + "NEW\n")
        with _open_log_tail(log, tail_bytes=200) as handle:
            text = handle.read()
        self.assertIn("NEW", text)
        self.assertLess(len(text), 210)

    def test_head_read_returns_the_start_only(self):
        log = self._write("FIRST\n" * 100)
        self.assertEqual(_log_head_lines(log, head_bytes=6), ["FIRST"])

    def test_reads_player_positions_from_spawn_and_moves(self):
        log = self._write(
            "17:07:20.798 [RECV] in 1 Guder - 4165102 105 23 2 0 0 0\r\n"
            "17:07:21.000 [RECV] mv 1 4165102 106 24 6\r\n"
            "17:07:21.000 [RECV] mv 3 5114 50 166 4\r\n"
        )
        self.assertEqual(
            load_player_spawns(log), {4165102: ("Guder", 106, 24)}
        )

    def test_player_name_does_not_swallow_later_dash_fields(self):
        # Live map-20 line: the stat/equipment tail contains spaces and -1
        # fields; a greedy name match used to capture almost the whole packet.
        log = self._write(
            "17:07:20.798 [RECV] in 1 Goddess - 12771 116 109 2 0 1 0 6 2 "
            "-1.769.404.763.-1.885.-1.-1.-1.-1.-1 100 100 0 50 4 2 0 0 1 4 "
            "75 75 -1 - -2 0 0 0 0 81 0 0|0|0 0 0 100 0 0\r\n"
            "17:07:21.000 [RECV] mv 1 12771 5 6 6\r\n"
        )
        self.assertEqual(
            load_player_spawns(log), {12771: ("Goddess", 5, 6)}
        )

    def test_reads_player_families_from_in_and_gidx(self):
        log = self._write(
            "1 [RECV] in 1 Goddess - 12771 116 109 2 0 1 0 6 2 "
            "-1.769.404.763.-1.885.-1.-1.-1.-1.-1 100 100 0 50 4 2 0 0 1 4 "
            "75 75 -1 - -2 0 0 0 0 81 0 0|0|0 0 0 100 0 0\r\n"
            "2 [RECV] gidx 1 12771 163.918 Kartushka 1 0|0|0\r\n"
            "3 [RECV] in 1 Retye - 3913 25 43 2 0 1 0 9 2 "
            "-1.769.404.763.-1.885.-1.-1.-1.-1.-1 100 100 0 -1 0 2 0 0 0 "
            "1000009 75 75 -1 - -2 0 0 0 0 81 0 0|0|0 0 0 100 0 0\r\n"
        )
        self.assertEqual(
            load_player_families(log),
            {
                12771: (163, "Kartushka", True),
                3913: (-1, "", True),
            },
        )

    def test_reads_fairy_vnum_and_element_from_log(self):
        log = self._write(
            "1 [RECV] in 1 Fairy - 5005 25 121 2 0 1 0 9 2 "
            "-1.0.0.-1.-1.885.-1.-1.-1.-1 100 100 0 -1 0 2 0 0 0 "
            "1000009 75 75 -1 - -2 0 0 0 0 81 0 0|0|0 0 0 100 0 0\r\n"
            "2 [RECV] pairy 1 5005 4 3 30 0\r\n"
        )
        self.assertEqual(
            load_player_metadata(log)[5005],
            (-1, "", True, 885, 3, True),
        )

    def test_family_backfill_clears_on_map_change(self):
        log = self._write(
            "1 [RECV] gidx 1 12771 163.918 Kartushka 1 0|0|0\r\n"
            "2 [RECV] c_map 0 85 1\r\n"
        )
        self.assertEqual(load_player_families(log), {})

    def test_player_move_without_spawn_keeps_an_empty_name(self):
        log = self._write("17:07:21.000 [RECV] mv 1 424242 10 11 6\r\n")
        self.assertEqual(
            load_player_spawns(log), {424242: ("", 10, 11)}
        )

    def test_load_entities_replays_spawns_moves_out_and_map_clear(self):
        log = self._write(
            "1 [RECV] in 3 200 5030 10 20 2 100 100 0\r\n"
            "2 [RECV] mv 3 5030 11 21 7\r\n"
            "3 [RECV] in 2 955 3095 67 56 2 100 100 439 0\r\n"
            "4 [RECV] out 2 3095\r\n"
            "5 [RECV] in 1 Guder - 4165102 105 23 2 0 0 0\r\n"
            "6 [RECV] mv 1 4165102 106 24 6\r\n"
            "7 [RECV] c_map 0 20 1\r\n"
            "8 [RECV] in 3 206 5112 30 40 2 100 100 0\r\n"
            "9 [RECV] mv 3 5112 31 41 5\r\n"
        )
        self.assertEqual(
            load_entities(log), {5112: (3, 206, 31, 41)}
        )

    def test_load_entities_keeps_movers_without_a_known_vnum(self):
        log = self._write("1 [RECV] mv 3 6000 5 6 7\r\n")
        self.assertEqual(load_entities(log), {6000: (3, 0, 5, 6)})

    def test_load_entities_mapout_clears_the_list(self):
        log = self._write(
            "1 [RECV] in 3 200 5030 10 20 2\r\n"
            "2 [RECV] mapout\r\n"
        )
        self.assertEqual(load_entities(log), {})

    def test_load_entities_missing_log_is_empty(self):
        self.assertEqual(load_entities("/nonexistent/NosTalePacketLog.txt"), {})

    def test_reads_monster_vnums_and_player_names(self):
        log = self._write(
            "17:07:20.798 [RECV] in 3 206 5114 50 159 2 100 100 0\r\n"
            "17:07:20.798 [RECV] in 1 Guder - 4165102 105 23 2 0 0 0\r\n"
            "17:07:20.798 [RECV] mv 3 5114 50 166 4\r\n"
            "17:07:20.798 [RECV] in 2 2557 2230354 104 24 2 96 100 0\r\n"
        )
        vnums, names = load_spawns(log)
        self.assertEqual(vnums, {5114: 206, 2230354: 2557})
        self.assertEqual(names, {4165102: "Guder"})

    def test_later_spawn_overrides_reused_entity_id(self):
        log = self._write(
            "1 [RECV] in 3 200 5000 10 10 2\r\n"
            "2 [RECV] in 3 206 5000 20 20 2\r\n"
        )
        vnums, _names = load_spawns(log)
        self.assertEqual(vnums[5000], 206)

    def test_reads_skill_list_and_deduplicates(self):
        log = self._write(
            "1 [RECV] ski 0 866 866 866 867 868 873 876 30\r\n"
            "2 [RECV] ski 1 31 866 30\r\n"
        )
        self.assertEqual(
            load_skill_list(log),
            [866, 867, 868, 873, 876, 30, 31],
        )

    def test_reads_skill_slots_from_paired_packets(self):
        log = self._write(
            "1 [RECV] at 14881134 2 110 141 2 0 1 1\r\n"
            "2 [SEND] u_s 0 3 3161\r\n"
            "3 [RECV] su 1 14881134 3 3161 260 5 11\r\n"
            "4 [SEND] u_s 5 3 3163\r\n"
            # Another player's hit on the same target must not be paired.
            "5 [RECV] su 1 14877915 3 3163 866 5 11\r\n"
            "6 [RECV] su 1 14881134 3 3163 855 5 11\r\n"
        )
        slots, self_id = load_skill_slots(log)
        self.assertEqual(self_id, 14881134)
        self.assertEqual(slots, {0: 260, 5: 855})

    def test_reads_the_last_map_position(self):
        log = self._write(
            "1 [RECV] at 14881134 103 20 20 2 0 24 1\r\n"
            "2 [SEND] walk 21 21 1 10\r\n"
            "3 [RECV] mv 1 14881134 22 22 10\r\n"
            "4 [RECV] c_map 0 104 1\r\n"
        )
        self.assertEqual(load_last_position(log), (104, 22, 22, 14881134))

    def test_reads_the_map_portal_list_from_gp(self):
        log = self._write(
            "1 [RECV] at 14881134 3 45 12 2 0 2 1\r\n"
            "2 [RECV] gp 10 208 6 -1 0 0\r\n"
            "3 [RECV] gp 45 12 103 -1 2 0\r\n"
        )
        self.assertEqual(
            load_gates(log), {(3, 10, 208): 6, (3, 45, 12): 103}
        )

    def test_reads_the_timespace_list_from_wp(self):
        log = self._write(
            "1 [RECV] at 14881134 4 28 178 2 0 2 1\r\n"
            "2 [RECV] wp 35 142 16 4 26 99\r\n"
            "3 [RECV] wp 229 33 704 0 90 99\r\n"
        )
        self.assertEqual(
            load_timespaces(log),
            {(4, 35, 142): (16, 4, 26, 99), (4, 229, 33): (704, 0, 90, 99)},
        )

    def test_reads_portals_from_crossings(self):
        log = self._write(
            "1 [RECV] at 14881134 103 20 20 2 0 24 1\r\n"
            "2 [SEND] walk 20 20 1 10\r\n"
            "3 [RECV] c_map 0 103 0\r\n"
            "4 [RECV] mapout\r\n"
            "5 [RECV] at 14881134 104 11 9 2 0 25 1\r\n"
            "6 [SEND] walk 11 9 0 10\r\n"
            "7 [RECV] c_map 0 104 0\r\n"
            "8 [RECV] at 14881134 103 20 20 2 0 24 1\r\n"
        )
        self.assertEqual(
            load_portals(log),
            {(103, 20, 20): 104, (104, 11, 9): 103},
        )

    def test_missing_log_is_not_fatal(self):
        self.assertEqual(load_spawns("/nonexistent/NosTalePacketLog.txt"), ({}, {}))
        self.assertEqual(load_skill_list("/nonexistent/NosTalePacketLog.txt"), [])
        self.assertEqual(
            load_skill_slots("/nonexistent/NosTalePacketLog.txt"), ({}, None)
        )
        self.assertEqual(
            load_last_position("/nonexistent/NosTalePacketLog.txt"),
            (None, None, None, None),
        )
        self.assertEqual(
            load_portals("/nonexistent/NosTalePacketLog.txt"), {}
        )

    def test_bazaar_open_learns_the_last_npc_and_option(self):
        log = self._write(
            "21:12:30.101 [SEND] npc_req 2 10284\r\n"
            "21:12:31.033 [SEND] n_run 60 0 2 10284\r\n"
            "21:12:31.139 [SEND] c_blist  0 0 0 0 0 0 0 0 0\r\n"
            "21:12:31.139 [SEND] c_slist 0 0 0\r\n"
            "21:13:00.101 [SEND] npc_req 2 9999\r\n"
            "21:13:01.000 [SEND] n_run 16 1 2 9999\r\n"
            "21:13:01.100 [SEND] c_blist 0 0 0 0 0 0 0 0 1 1285\r\n"
        )
        self.assertEqual(load_bazaar_open(log), (10284, 60, 0))

    def test_bazaar_open_without_a_client_packet_is_none(self):
        log = self._write("21:12:30.101 [SEND] npc_req 2 10284\r\n")
        self.assertEqual(load_bazaar_open(log), (None, None, None))

    def test_inventory_replays_updates_and_gold(self):
        log = self._write(
            "02:02:16.473 [RECV] inv 2 0.2115.5 1.2282.11 2.0.0\r\n"
            "02:02:17.473 [RECV] inv 0 42.884.0.0.0.0.0 "
            "63.-4962.5.7.1.0.0\r\n"
            "02:02:18.473 [RECV] inv 1 4.1323.1 5.2282.7\r\n"
            "02:02:19.473 [RECV] inv 6 0.905.0.5.0\r\n"
            "02:02:16.473 [RECV] gold 34661 0\r\n"
            "06:48:48.421 [RECV] ivn 2 1.2282.10.0\r\n"
            # Standard bag: quantity 0 removes the slot.
            "06:48:49.421 [RECV] ivn 1 5.2282.0.0\r\n"
            # Equipment: quantity 0 is an occupied row, vnum 0 removes it.
            "06:48:50.421 [RECV] ivn 0 30.885.0.0.0.0.0\r\n"
            "06:48:51.421 [RECV] ivn 0 30.0.0.0.0.0.0\r\n"
            "06:48:52.421 [RECV] inv 2\r\n"
        )
        bags, gold = load_inventory(log)
        self.assertEqual(gold, 34661)
        # `inv 2` with no tokens clears the bag.
        self.assertEqual(bags[2], {})
        # The standard quantity-0 update removed slot 5.
        self.assertEqual(bags[1], {4: InventorySlot(1, 4, 1323, 1)})
        # Equipment tokens keep their extra fields and quantity 0 (bag 6).
        self.assertEqual(
            bags[6], {0: InventorySlot(6, 0, 905, 0, ("5", "0"))}
        )
        # Bag 0 keeps both the quantity-0 row and the negative vnum; the
        # vnum-0 update removed slot 30.
        self.assertNotIn(30, bags[0])
        self.assertEqual(bags[0][42].fields, ("0", "0", "0", "0"))
        self.assertEqual(bags[0][42].amount, 1)
        self.assertEqual(
            bags[0][63],
            InventorySlot(0, 63, -4962, 5, ("7", "1", "0", "0")),
        )
        self.assertEqual(bags[0][63].amount, 5)

    def test_only_full_inv_lines_count_as_complete_bags(self):
        log = self._write(
            "02:00:00.000 [RECV] inv 0 30.885.0.0.0.0.0\r\n"
            "02:00:00.000 [RECV] inv 1 0.2174.5\r\n"
            "02:00:00.000 [RECV] ivn 2 0.2282.11.0\r\n"
        )
        self.assertEqual(log_inventory_bags(log), {0, 1})

    def test_log_truncation_notice_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.txt"
            path.write_text(
                "14:19:33.739 [STATUS] log truncated at 16489 KB; "
                "a new session file starts\r\n",
                encoding="latin1",
            )
            self.assertTrue(log_was_truncated(path))
            path.write_text(
                "14:19:33.739 [RECV] mv 3 3003 118 3 5\r\n",
                encoding="latin1",
            )
            self.assertFalse(log_was_truncated(path))
            self.assertFalse(log_was_truncated(None))
            self.assertFalse(log_was_truncated(Path(tmp) / "missing.txt"))


class ClientLogPathTests(unittest.TestCase):
    def test_path_is_named_by_the_windows_pid(self):
        self.assertEqual(
            client_log_path(Path("/prefix"), 1612),
            Path("/prefix/drive_c/NosTalePacketLog-1612.txt"),
        )

    def test_default_log_path_prefers_the_newest_client_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            drive = Path(tmp) / "drive_c"
            drive.mkdir()
            older = drive / "NosTalePacketLog-100.txt"
            newer = drive / "NosTalePacketLog-200.txt"
            older.write_text("1 [RECV] at 1 2 3 4 0 0 0 0\r\n", encoding="latin1")
            newer.write_text("1 [RECV] at 5 6 7 8 0 0 0 0\r\n", encoding="latin1")
            os.utime(older, (1_000_000, 1_000_000))
            os.utime(newer, (2_000_000, 2_000_000))
            with patch.dict(os.environ, {"WINEPREFIX": tmp}):
                os.environ.pop("NTPL_LOG", None)
                self.assertEqual(default_log_path(), newer)

    def test_default_log_path_falls_back_to_the_legacy_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            drive = Path(tmp) / "drive_c"
            drive.mkdir()
            legacy = drive / "NosTalePacketLog.txt"
            legacy.write_text("legacy\r\n", encoding="latin1")
            with patch.dict(os.environ, {"WINEPREFIX": tmp}):
                os.environ.pop("NTPL_LOG", None)
                self.assertEqual(default_log_path(), legacy)

    def test_ntpl_log_override_wins(self):
        with patch.dict(
            os.environ, {"NTPL_LOG": "/somewhere/NosTalePacketLog.txt"}
        ):
            self.assertEqual(
                default_log_path(), Path("/somewhere/NosTalePacketLog.txt")
            )


if __name__ == "__main__":
    unittest.main()
