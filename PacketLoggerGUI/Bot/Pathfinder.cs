using System;
using System.Collections.Generic;

namespace PacketLoggerGUI.Bot
{
    public class MapGrid
    {
        public int Width { get; }
        public int Height { get; }
        private readonly byte[,] _grid;

        public MapGrid(int width, int height, byte[,] grid)
        {
            Width = width;
            Height = height;
            _grid = grid;
        }

        // Create an empty walkable grid (when no map data available)
        public MapGrid(int width = 300, int height = 300)
        {
            Width = width;
            Height = height;
            _grid = new byte[width, height];
        }

        public bool IsWalkable(int x, int y)
        {
            if (x < 0 || x >= Width || y < 0 || y >= Height) return false;
            return _grid[x, y] == 0;
        }

        public void SetObstacle(int x, int y)
        {
            if (x >= 0 && x < Width && y >= 0 && y < Height)
                _grid[x, y] = 1;
        }
    }

    public class PathResult
    {
        public List<(int X, int Y)> Points { get; }
        public bool Found { get; }

        public PathResult(List<(int X, int Y)> points, bool found)
        {
            Points = points;
            Found = found;
        }

        public static PathResult Empty => new(new List<(int, int)>(), false);

        /// <summary>
        /// Get the next N steps along the path, collapsing straight-line segments.
        /// </summary>
        public List<(int X, int Y)> GetWaypoints(int maxCount = 5)
        {
            if (Points.Count == 0) return new();

            var waypoints = new List<(int X, int Y)>();
            int prevDx = 0, prevDy = 0;

            for (int i = 1; i < Points.Count && waypoints.Count < maxCount; i++)
            {
                int dx = Points[i].X - Points[i - 1].X;
                int dy = Points[i].Y - Points[i - 1].Y;

                // Direction changed — add the previous point as a waypoint
                if (dx != prevDx || dy != prevDy)
                {
                    waypoints.Add(Points[i - 1]);
                    prevDx = dx;
                    prevDy = dy;
                }
            }

            // Always add the final destination
            if (Points.Count > 0)
                waypoints.Add(Points[^1]);

            return waypoints;
        }
    }

    /// <summary>
    /// A* pathfinding on NosTale map grids.
    /// Adapted from NosSmooth.Extensions.Pathfinding.
    /// Supports 8-directional movement with diagonal cost sqrt(2).
    /// </summary>
    public static class Pathfinder
    {
        private static readonly (int dx, int dy, double cost)[] Neighbors =
        {
            (0, -1, 1.0),    // up
            (0, 1, 1.0),     // down
            (-1, 0, 1.0),    // left
            (1, 0, 1.0),     // right
            (-1, -1, 1.414), // up-left
            (1, -1, 1.414),  // up-right
            (-1, 1, 1.414),  // down-left
            (1, 1, 1.414),   // down-right
        };

        /// <summary>
        /// Find path from start to target using A*.
        /// Returns path including start and end points.
        /// </summary>
        public static PathResult FindPath(MapGrid? map, int startX, int startY, int targetX, int targetY, int maxIterations = 10000)
        {
            // If no map data, return direct line
            if (map == null)
                return new PathResult(new List<(int, int)> { (startX, startY), (targetX, targetY) }, true);

            if (startX == targetX && startY == targetY)
                return new PathResult(new List<(int, int)> { (startX, startY) }, true);

            // If target isn't walkable, find nearest walkable tile
            if (!map.IsWalkable(targetX, targetY))
            {
                var nearest = FindNearestWalkable(map, targetX, targetY, 5);
                if (nearest == null) return PathResult.Empty;
                (targetX, targetY) = nearest.Value;
            }

            var openSet = new PriorityQueue<(int x, int y), double>();
            var visited = new HashSet<long>();
            var cameFrom = new Dictionary<long, long>();
            var gScore = new Dictionary<long, double>();

            long startKey = Key(startX, startY);
            long targetKey = Key(targetX, targetY);

            openSet.Enqueue((startX, startY), 0);
            gScore[startKey] = 0;
            visited.Add(startKey);

            int iterations = 0;

            while (openSet.Count > 0 && iterations++ < maxIterations)
            {
                var (cx, cy) = openSet.Dequeue();
                long currentKey = Key(cx, cy);

                if (cx == targetX && cy == targetY)
                    return ReconstructPath(cameFrom, startKey, targetKey, startX, startY, targetX, targetY);

                double currentG = gScore.GetValueOrDefault(currentKey, double.MaxValue);

                foreach (var (dx, dy, cost) in Neighbors)
                {
                    int nx = cx + dx;
                    int ny = cy + dy;
                    long nKey = Key(nx, ny);

                    if (visited.Contains(nKey)) continue;
                    if (!map.IsWalkable(nx, ny)) continue;

                    // For diagonal movement, check that both cardinal neighbors are walkable
                    if (dx != 0 && dy != 0)
                    {
                        if (!map.IsWalkable(cx + dx, cy) || !map.IsWalkable(cx, cy + dy))
                            continue;
                    }

                    visited.Add(nKey);
                    double tentativeG = currentG + cost;
                    double h = Heuristic(nx, ny, targetX, targetY);

                    gScore[nKey] = tentativeG;
                    cameFrom[nKey] = currentKey;
                    openSet.Enqueue((nx, ny), tentativeG + h);
                }
            }

            return PathResult.Empty;
        }

        /// <summary>
        /// Find path to the nearest entity of a given type, stopping within range.
        /// </summary>
        public static PathResult FindPathToRange(MapGrid? map, int startX, int startY, int targetX, int targetY, double stopRange)
        {
            double dist = Heuristic(startX, startY, targetX, targetY);
            if (dist <= stopRange)
                return new PathResult(new List<(int, int)> { (startX, startY) }, true);

            // Find a point along the line that's within range of target
            double ratio = (dist - stopRange) / dist;
            int midX = startX + (int)((targetX - startX) * ratio);
            int midY = startY + (int)((targetY - startY) * ratio);

            return FindPath(map, startX, startY, midX, midY);
        }

        private static double Heuristic(int x1, int y1, int x2, int y2)
        {
            int dx = x1 - x2;
            int dy = y1 - y2;
            return Math.Sqrt(dx * dx + dy * dy);
        }

        private static long Key(int x, int y) => ((long)x << 32) | (uint)y;

        private static (int x, int y) FromKey(long key) => ((int)(key >> 32), (int)(key & 0xFFFFFFFF));

        private static PathResult ReconstructPath(Dictionary<long, long> cameFrom, long startKey, long targetKey, int startX, int startY, int targetX, int targetY)
        {
            var path = new List<(int, int)>();
            long current = targetKey;

            while (current != startKey)
            {
                var (x, y) = FromKey(current);
                path.Add((x, y));
                if (!cameFrom.TryGetValue(current, out current))
                    break;
            }

            path.Add((startX, startY));
            path.Reverse();
            return new PathResult(path, true);
        }

        private static (int x, int y)? FindNearestWalkable(MapGrid map, int x, int y, int radius)
        {
            for (int r = 1; r <= radius; r++)
            {
                for (int dx = -r; dx <= r; dx++)
                {
                    for (int dy = -r; dy <= r; dy++)
                    {
                        if (map.IsWalkable(x + dx, y + dy))
                            return (x + dx, y + dy);
                    }
                }
            }
            return null;
        }
    }
}
