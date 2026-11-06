import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from ntpl.mapdata import (
    DEFAULT_NSTC,
    MapDataError,
    MapGrid,
    astar,
    find_map_for_positions,
    load_nstc,
    terrain_rgb,
)


def build_nos(grids: dict[int, tuple[int, int, bytes]]) -> bytes:
    """Build a minimal NStcData.NOS for the parser test."""
    header = b"NT Data 05" + b"\x00" * 6
    count = len(grids)
    index_start = 0x15
    offset = index_start + count * 8
    index = bytearray()
    blocks = bytearray()
    for map_id, (width, height, cells) in grids.items():
        raw = struct.pack("<hh", width, height) + cells
        compressed = zlib.compress(raw)
        block = struct.pack("<IIIB", 0, len(raw), len(compressed), 1) + compressed
        index += struct.pack("<II", map_id, offset)
        blocks += block
        offset += len(block)
    return header + struct.pack("<I", count) + b"\x00" + bytes(index) + bytes(blocks)


class ParserTests(unittest.TestCase):
    def test_round_trip(self):
        cells = bytes([0, 0, 1, 0, 2, 0, 0, 0, 0])
        payload = build_nos({7: (3, 3, cells)})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "NStcData.NOS"
            path.write_bytes(payload)
            grids = load_nstc(path)
        self.assertIn(7, grids)
        grid = grids[7]
        self.assertEqual((grid.width, grid.height), (3, 3))
        self.assertEqual(grid.at(0, 0), 0)
        self.assertEqual(grid.at(2, 0), 1)
        self.assertTrue(grid.is_walkable(0, 0))
        self.assertFalse(grid.is_walkable(2, 0))
        self.assertIsNone(grid.at(9, 9))
        self.assertTrue(grid.is_walkable(-1, 0) is False)

    def test_bad_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.NOS"
            path.write_bytes(b"not an archive")
            with self.assertRaises(MapDataError):
                load_nstc(path)

    def test_missing_file(self):
        with self.assertRaises(MapDataError):
            load_nstc("/nonexistent/NStcData.NOS")


class AstarTests(unittest.TestCase):
    def grid(self) -> MapGrid:
        # 5x5, a wall down x=2 except y=1 and y=3
        cells = []
        for y in range(5):
            for x in range(5):
                cells.append(1 if (x == 2 and y not in (1, 3)) else 0)
        return MapGrid(1, 5, 5, bytes(cells))

    def test_path_around_wall(self):
        path = astar(self.grid(), (0, 0), (4, 4))
        self.assertIsNotNone(path)
        self.assertEqual(path[-1], (4, 4))
        self.assertTrue(all(self.grid().is_walkable(*cell) for cell in path))
        self.assertNotIn((2, 2), path)

    def test_unreachable(self):
        cells = bytes([0, 1, 0, 1, 0] + [1, 1, 1, 1, 1] + [0, 1, 0, 1, 0] * 3)
        grid = MapGrid(2, 5, 5, cells)
        self.assertIsNone(astar(grid, (0, 0), (4, 4)))

    def test_endpoints_must_be_walkable(self):
        grid = self.grid()
        self.assertIsNone(astar(grid, (2, 2), (0, 0)))

    def test_same_cell(self):
        self.assertEqual(astar(self.grid(), (0, 0), (0, 0)), [])


class RankingTests(unittest.TestCase):
    def test_ranking_prefers_covering_walkable_map(self):
        walkable = bytes([0] * 25)
        blocked = bytes([1] * 25)
        grids = {
            1: MapGrid(1, 5, 5, walkable),
            2: MapGrid(2, 5, 5, blocked),
            3: MapGrid(3, 2, 2, bytes(4)),  # does not cover
        }
        ranked = find_map_for_positions(grids, [(1, 1), (2, 2), (4, 4)])
        self.assertEqual(ranked[0][1], 1)
        self.assertAlmostEqual(ranked[0][0], 1.0)
        self.assertEqual([entry[1] for entry in ranked], [1, 2])

    def test_empty_points(self):
        self.assertEqual(find_map_for_positions({}, []), [])


class PaletteTests(unittest.TestCase):
    def test_known_colors(self):
        self.assertEqual(terrain_rgb(0x00), (255, 255, 255))
        self.assertEqual(terrain_rgb(0x0B), (150, 100, 100))
        self.assertEqual(terrain_rgb(0xFE), (150, 0, 255))


@unittest.skipUnless(DEFAULT_NSTC.exists(), "local NostaleData not available")
class LocalArchiveTests(unittest.TestCase):
    def test_current_map_candidate(self):
        grids = load_nstc(DEFAULT_NSTC)
        self.assertIn(20, grids)
        grid = grids[20]
        self.assertEqual((grid.width, grid.height), (230, 260))
        # Live 2026-11-06: the character stood and walked around (25,117).
        self.assertTrue(grid.is_walkable(25, 117))
        self.assertTrue(grid.is_walkable(25, 118))


if __name__ == "__main__":
    unittest.main()
