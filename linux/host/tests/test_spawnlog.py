import tempfile
import unittest
from pathlib import Path

from ntpl.spawnlog import load_spawns


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

    def test_missing_log_is_not_fatal(self):
        self.assertEqual(load_spawns("/nonexistent/NosTalePacketLog.txt"), ({}, {}))


if __name__ == "__main__":
    unittest.main()
