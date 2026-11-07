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
    choose_map_for_positions,
    find_map_for_positions,
    load_nstc,
    load_portals,
    normalize_portals,
    save_portals,
    set_gate,
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

    def test_open_ground_uses_diagonals(self):
        grid = MapGrid(3, 5, 5, bytes(25))
        path = astar(grid, (0, 0), (4, 4))
        self.assertEqual(len(path), 4)
        previous = (0, 0)
        for cell in path:
            self.assertLessEqual(
                max(abs(cell[0] - previous[0]), abs(cell[1] - previous[1])),
                1,
            )
            previous = cell

    def test_does_not_cut_blocked_corner(self):
        # (1,0) and (0,1) are blocked; the diagonal (0,0) -> (1,1) must not
        # squeeze between them.
        cells = bytearray(9)
        cells[1] = 1  # (1, 0)
        cells[3] = 1  # (0, 1)
        grid = MapGrid(4, 3, 3, bytes(cells))
        self.assertIsNone(astar(grid, (0, 0), (1, 1)))

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


class ChooseMapTests(unittest.TestCase):
    """Attach-time auto-detection: the server map id is unknown."""

    POINTS = [(10, 10), (20, 20), (30, 30)]

    def grids(self) -> dict[int, MapGrid]:
        # The live bug: maps 300 and 5613 share a 240x190 layout and a handful
        # of entity positions fit both perfectly, while the real map 2
        # (150x150, NosVille Meadows) also holds the same points. The tightest
        # perfect fit is the right answer.
        return {
            2: MapGrid(2, 150, 150, bytes(150 * 150)),
            300: MapGrid(300, 240, 190, bytes(240 * 190)),
            5613: MapGrid(5613, 240, 190, bytes(240 * 190)),
        }

    def test_tie_prefers_smallest_perfect_fit(self):
        self.assertEqual(
            choose_map_for_positions(self.grids(), self.POINTS),
            (1.0, 2),
        )

    def test_current_map_is_kept_while_tied(self):
        # Once the view shows one of the tied maps, do not flip it every tick.
        self.assertEqual(
            choose_map_for_positions(
                self.grids(), self.POINTS, current_id=5613
            ),
            (1.0, 5613),
        )

    def test_better_fit_replaces_the_current_map(self):
        # A selected map that fits only part of the cloud must not stick
        # merely because most points are walkable on it (the old 0.6 rule).
        blocked = bytearray(150 * 150)
        blocked[10 * 150 + 10] = 1  # one live point lands on a wall
        grids = {
            2: MapGrid(2, 150, 150, bytes(blocked)),
            300: MapGrid(300, 240, 190, bytes(240 * 190)),
        }
        self.assertEqual(
            choose_map_for_positions(grids, self.POINTS, current_id=2),
            (1.0, 300),
        )

    def test_no_fit_below_minimum(self):
        # 1 walkable cell of 4 queried cells = 0.25 < the 0.6 floor.
        grid = MapGrid(9, 2, 2, bytes([0, 1, 1, 1]))
        self.assertIsNone(
            choose_map_for_positions({9: grid}, [(0, 0), (1, 1)])
        )

    def test_points_outside_every_grid(self):
        grid = MapGrid(9, 2, 2, bytes(4))
        self.assertIsNone(
            choose_map_for_positions({9: grid}, [(5, 5), (6, 6)])
        )


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


class PortalStoreTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "portals.json"
            save_portals({(103, 20, 20): 104, (104, 11, 9): 103}, path)
            self.assertEqual(
                load_portals(path),
                {(103, 20, 20): 104, (104, 11, 9): 103},
            )

    def test_set_gate_replaces_a_nearby_crossing_guess(self):
        portals = {(3, 44, 12): 103, (3, 10, 208): 6}
        changed = set_gate(portals, 3, 45, 12, 103)
        self.assertTrue(changed)
        self.assertEqual(portals, {(3, 45, 12): 103, (3, 10, 208): 6})

    def test_normalize_collapses_nearby_markers(self):
        raw = {(103, 41, 31): 3, (103, 42, 33): 3, (103, 20, 20): 104}
        self.assertEqual(
            normalize_portals(raw),
            {(103, 42, 32): 3, (103, 20, 20): 104},
        )

    def test_missing_file_and_bad_records_are_ignored(self):
        self.assertEqual(load_portals("/nonexistent/portals.json"), {})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "portals.json"
            path.write_text(
                '{"portals": [{"map": 0, "x": 1, "y": 2, "to": 3},'
                ' {"map": 5, "x": 6, "y": 7, "to": 8}, "junk"]}',
                encoding="utf-8",
            )
            self.assertEqual(load_portals(path), {(5, 6, 7): 8})


if __name__ == "__main__":
    unittest.main()
