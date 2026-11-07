"""NosTale map grids (NStcData.NOS).

The client's `NostaleData/NStcData.NOS` archive contains one grid per map:

    header     16 bytes ("NT Data 05" + padding)
    count      uint32
    separator  1 byte
    index      count * (map_id:uint32, offset:uint32)
    blocks     at each offset:
                 creation_date uint32
                 data_size     uint32   (uncompressed)
                 compressed    uint32
                 is_compressed uint8
                 payload       zlib stream when is_compressed

The decompressed payload starts with `width:int16`, `height:int16`, followed by
`width * height` bytes, one per cell. The palette comes from OnexExplorer
(NosTale Research); 0x00 is the open/walkable terrain. The live 2026-11-06
session confirmed the format: all 5,849 entity positions in the current map
landed on 0x00 cells, and the best-scoring grid was map 20 (230x260).

This module is read-only and never touches the game process.
"""

from __future__ import annotations

import heapq
import json
import math
import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

#: OnexExplorer's NSTC palette (colour per terrain byte).
TERRAIN_COLORS: dict[int, tuple[int, int, int]] = {
    0x00: (255, 255, 255),
    0x01: (100, 100, 100),
    0x02: (100, 150, 100),
    0x03: (0, 0, 0),
    0x09: (100, 100, 150),
    0x0A: (0, 50, 200),
    0x0B: (150, 100, 100),
    0x0D: (150, 150, 100),
    0x10: (150, 100, 150),
    0x11: (0, 200, 50),
    0x12: (200, 50, 0),
    0x13: (250, 230, 10),
    0xFF: (150, 0, 255),
}

#: Terrain byte treated as walkable. 0x00 is the open ground in the live map.
WALKABLE = 0x00

#: Default install path on this machine; override with NTPL_NSTC or --file.
DEFAULT_NSTC = (
    Path.home() / "Desktop/nostake/Nostale/NostaleData/NStcData.NOS"
)


class MapDataError(ValueError):
    pass


@dataclass(frozen=True)
class MapGrid:
    map_id: int
    width: int
    height: int
    cells: bytes

    def at(self, x: int, y: int) -> int | None:
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.cells[y * self.width + x]
        return None

    def is_walkable(self, x: int, y: int) -> bool:
        return self.at(x, y) == WALKABLE

    def neighbours(self, x: int, y: int):
        """Walkable 8-direction neighbours, without cutting blocked corners.

        Diagonals are only allowed when both orthogonal cells are walkable:
        the client cannot squeeze between two blocked cells, and allowing it
        would make the drawn path pass through a wall corner.
        """
        for dx, dy in (
            (1, 0), (-1, 0), (0, 1), (0, -1),
            (1, 1), (1, -1), (-1, 1), (-1, -1),
        ):
            nx, ny = x + dx, y + dy
            if not self.is_walkable(nx, ny):
                continue
            if dx and dy and not (
                self.is_walkable(x + dx, y) and self.is_walkable(x, y + dy)
            ):
                continue
            yield nx, ny

    def walkable_count(self) -> int:
        return self.cells.count(WALKABLE)


def load_nstc(path: str | Path | None = None) -> dict[int, MapGrid]:
    """Parse the NStcData archive into `{map_id: MapGrid}`."""
    source = Path(path) if path is not None else DEFAULT_NSTC
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise MapDataError(f"cannot read {source}: {exc}") from exc

    if not data.startswith(b"NT Data"):
        raise MapDataError(f"{source} is not a NosTale 'NT Data' archive")
    if len(data) < 0x15:
        raise MapDataError(f"{source} is truncated")

    count = struct.unpack_from("<I", data, 0x10)[0]
    index_start = 0x15
    index_end = index_start + count * 8
    if index_end > len(data):
        raise MapDataError(f"{source}: index ({count} entries) exceeds the file")

    grids: dict[int, MapGrid] = {}
    for position in range(index_start, index_end, 8):
        map_id, offset = struct.unpack_from("<II", data, position)
        if offset + 13 > len(data):
            continue
        _creation, _data_size, compressed_size, compressed = struct.unpack_from(
            "<IIIB", data, offset
        )
        payload = data[offset + 13 : offset + 13 + compressed_size]
        if compressed:
            try:
                raw = zlib.decompress(payload)
            except zlib.error:
                continue
        else:
            raw = payload

        if len(raw) < 4:
            continue
        width, height = struct.unpack_from("<hh", raw, 0)
        if width <= 0 or height <= 0:
            continue
        cells = raw[4 : 4 + width * height]
        if len(cells) != width * height:
            continue
        grids[map_id] = MapGrid(map_id, width, height, cells)

    if not grids:
        raise MapDataError(f"{source}: no map grids found")
    return grids


def find_map_for_positions(
    grids: dict[int, MapGrid],
    points: list[tuple[int, int]],
    limit: int = 8,
) -> list[tuple[float, int]]:
    """Rank maps by how many entity positions land on walkable cells.

    The live map's moving entities are all on 0x00; other maps at the same
    coordinates score much lower. Returns `(fraction, map_id)` sorted best
    first. Maps that do not cover every point are skipped.
    """
    if not points:
        return []

    ranked: list[tuple[float, int]] = []
    for grid in grids.values():
        if not all(
            0 <= x < grid.width and 0 <= y < grid.height for x, y in points
        ):
            continue
        walkable = sum(1 for x, y in points if grid.is_walkable(x, y))
        ranked.append((walkable / len(points), grid.map_id))
    ranked.sort(reverse=True)
    return ranked[:limit]


def choose_map_for_positions(
    grids: dict[int, MapGrid],
    points: list[tuple[int, int]],
    current_id: int | None = None,
    minimum: float = 0.6,
) -> tuple[float, int] | None:
    """Pick the most likely live map for ``points``.

    :func:`find_map_for_positions` alone is ambiguous when the host attaches
    after login: the server never announced a map id, and many maps — often
    several sharing one layout — contain the same handful of entity positions
    on walkable cells and tie at 100 %. The real map is the *tightest* grid
    that holds every point, so among equally good candidates prefer the
    smallest one. A current map that is tied for best is kept, so the view does
    not flip between equal candidates as points arrive.

    Returns ``(fraction, map_id)``, or ``None`` when no grid fits well enough.
    """
    ranked = find_map_for_positions(grids, points, limit=len(grids))
    if not ranked:
        return None
    best_fraction = ranked[0][0]
    if best_fraction < minimum:
        return None
    tied = [
        map_id
        for fraction, map_id in ranked
        if best_fraction - fraction <= 1e-9
    ]
    if current_id in tied:
        return best_fraction, current_id
    map_id = min(
        tied,
        key=lambda candidate: (
            grids[candidate].width * grids[candidate].height,
            candidate,
        ),
    )
    return best_fraction, map_id


_SQRT2 = math.sqrt(2.0)


def _octile(ax: int, ay: int, bx: int, by: int) -> float:
    """Admissible 8-direction heuristic: straight + diagonal remainder."""
    dx = abs(ax - bx)
    dy = abs(ay - by)
    return max(dx, dy) + (_SQRT2 - 1.0) * min(dx, dy)


def astar(
    grid: MapGrid,
    start: tuple[int, int],
    goal: tuple[int, int],
) -> list[tuple[int, int]] | None:
    """Shortest 8-direction path over walkable cells, excluding `start`.

    Diagonal steps cost sqrt(2) and are only taken when both orthogonal cells
    are walkable (no corner cutting). Returns the cells to walk through (goal
    included), or None when no path exists or either endpoint is not walkable.
    """
    if not grid.is_walkable(*start) or not grid.is_walkable(*goal):
        return None
    if start == goal:
        return []

    frontier: list[tuple[float, float, int, tuple[int, int]]] = [
        (_octile(start[0], start[1], *goal), 0.0, 0, start)
    ]
    g_score: dict[tuple[int, int], float] = {start: 0.0}
    came_from: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    counter = 1

    while frontier:
        _estimate, g, _counter, current = heapq.heappop(frontier)
        if g > g_score.get(current, math.inf):
            continue
        if current == goal:
            break
        for neighbour in grid.neighbours(*current):
            step = (
                _SQRT2
                if neighbour[0] != current[0] and neighbour[1] != current[1]
                else 1.0
            )
            tentative = g + step
            if tentative + 1e-9 >= g_score.get(neighbour, math.inf):
                continue
            g_score[neighbour] = tentative
            came_from[neighbour] = current
            heapq.heappush(
                frontier,
                (
                    tentative
                    + _octile(neighbour[0], neighbour[1], *goal),
                    tentative,
                    counter,
                    neighbour,
                ),
            )
            counter += 1

    if goal not in came_from:
        return None

    path: list[tuple[int, int]] = []
    node = goal
    while node is not None and node != start:
        path.append(node)
        node = came_from[node]
    path.reverse()
    return path


def terrain_rgb(value: int | None) -> tuple[int, int, int]:
    if value is None:
        return (0, 0, 0)
    return TERRAIN_COLORS.get(value, TERRAIN_COLORS[0xFF])


# -- learned portals ----------------------------------------------------
#
# The client has no static portal table: a portal is a server-side warp tile,
# so the only way to learn one is to stand on it and be teleported. The engine
# records the cell the character occupied when the server sent the leaving
# `c_map`/`mapout`, and the destination arrives in the following `at`. These
# helpers persist that map across GUI runs.

PortalMap = dict[tuple[int, int, int], int]


def default_portals_path() -> Path:
    """``NTPL_PORTALS``, or ``$XDG_CONFIG_HOME/ntpl/portals.json``."""
    override = os.environ.get("NTPL_PORTALS")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "ntpl" / "portals.json"


def load_portals(path: str | Path | None = None) -> PortalMap:
    """Read ``(mapId, x, y) -> destination mapId``; bad files yield {}."""
    source = Path(path) if path is not None else default_portals_path()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    records = data.get("portals", []) if isinstance(data, dict) else []
    portals: PortalMap = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            map_id = int(record["map"])
            x = int(record["x"])
            y = int(record["y"])
            destination = int(record["to"])
        except (KeyError, TypeError, ValueError):
            continue
        if map_id > 0 and x >= 0 and y >= 0 and destination > 0:
            portals[(map_id, x, y)] = destination
    return portals


def add_portal(
    portals: PortalMap,
    map_id: int,
    x: int,
    y: int,
    destination: int,
    radius: int = 2,
) -> bool:
    """Add a portal unless the same map/destination already has one nearby.

    The departure cell is the last client walk before the server's map
    change, so a repeated crossing can land a cell or two off; one marker is
    enough. Returns ``True`` when the map changed.
    """
    for (other_map, other_x, other_y), other_destination in portals.items():
        if (
            other_map == map_id
            and other_destination == destination
            and abs(other_x - x) <= radius
            and abs(other_y - y) <= radius
        ):
            return False
    portals[(map_id, x, y)] = destination
    return True


def normalize_portals(portals: PortalMap, radius: int = 2) -> PortalMap:
    """Collapse nearby markers for the same map and destination into one.

    A crossing records the last client walk, which can be a cell or two away
    from the warp tile, and several crossings of the same portal would leave
    a cluster. Averaging the cluster gives one marker per real portal.
    """
    clusters: list[list[tuple[int, int, int]]] = []
    destinations: list[int] = []
    for (map_id, x, y), destination in sorted(portals.items()):
        for index, cluster in enumerate(clusters):
            if (
                destinations[index] == destination
                and cluster[0][0] == map_id
                and all(
                    abs(other_x - x) <= radius
                    and abs(other_y - y) <= radius
                    for _, other_x, other_y in cluster
                )
            ):
                cluster.append((map_id, x, y))
                break
        else:
            clusters.append([(map_id, x, y)])
            destinations.append(destination)

    merged: PortalMap = {}
    for cluster, destination in zip(clusters, destinations):
        map_id = cluster[0][0]
        x = round(sum(point[1] for point in cluster) / len(cluster))
        y = round(sum(point[2] for point in cluster) / len(cluster))
        merged[(map_id, x, y)] = destination
    return merged


def save_portals(portals: PortalMap, path: str | Path | None = None) -> None:
    """Write the portal map atomically; failures are not fatal."""
    source = Path(path) if path is not None else default_portals_path()
    try:
        source.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "portals": [
                {"map": map_id, "x": x, "y": y, "to": destination}
                for (map_id, x, y), destination in sorted(portals.items())
            ],
        }
        temporary = source.with_suffix(source.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, source)
    except OSError:
        return
