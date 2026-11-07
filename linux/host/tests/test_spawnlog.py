import tempfile
import unittest
from pathlib import Path

from ntpl.spawnlog import (
    load_gates,
    load_last_position,
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


if __name__ == "__main__":
    unittest.main()
