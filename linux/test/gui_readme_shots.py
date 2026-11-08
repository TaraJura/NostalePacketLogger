"""Generate the screenshots used by the README and docs.

Manual tool, not part of ``make gui-test``: it starts a fake packet source,
opens the real GTK4 window against a real map from NStcData, draws a route,
spawns a few monsters with their real names, and leaves the window on screen
for ``linux/scripts/readme-shots.sh`` to capture with ffmpeg/Xvfb.

    NTPL_SHOT_MODE=map|packets|picker|spells \
        PYTHONPATH=linux/host python3 linux/test/gui_readme_shots.py
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.clients import ClientInfo, Discovery, WineContext, client_lock_path
from ntpl.gui import Application
from ntpl.mapdata import astar, load_nstc

MODE = os.environ.get("NTPL_SHOT_MODE", "map")
CHAR_ID = 14881134
HP, HP_MAX = 2950, 3209
MP, MP_MAX = 3600, 3727


def pick_scene():
    """A walkable start, a 20-40 cell route and three monster cells."""
    grids = load_nstc()
    for map_id in (20, 2, 85):
        grid = grids.get(map_id)
        if grid is None:
            continue
        cells = [
            (x, y)
            for y in range(grid.height)
            for x in range(grid.width)
            if grid.is_walkable(x, y)
        ]
        cells.sort(
            key=lambda cell: abs(cell[0] - grid.width // 2)
            + abs(cell[1] - grid.height // 2)
        )
        for start in cells[:80]:
            for dx, dy in (
                (42, 0),
                (0, 42),
                (-42, 0),
                (0, -42),
                (34, 26),
                (30, -30),
            ):
                goal = (start[0] + dx, start[1] + dy)
                if not (
                    0 <= goal[0] < grid.width
                    and 0 <= goal[1] < grid.height
                ):
                    continue
                if not grid.is_walkable(*goal):
                    continue
                path = astar(grid, start, goal)
                if path and len(path) >= 30:
                    return map_id, grid, start, path
    return None


def monster_line(vnum: int, entity: int, x: int, y: int) -> str:
    return (
        f"RECV|in 3 {vnum} {entity} {x} {y} 2 100 100 0 0 0 -1 0 0 -1 "
        "- 0 -1 0 0 0 0 0 0 0 0 0 0 0 1 0\n"
    )


def start_fake_client(scene) -> int:
    map_id, grid, start, path = scene
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    first, middle, last = path[len(path) // 4], path[len(path) // 2], path[-1]
    player = path[len(path) // 3]
    lines = [
        "STATUS|ready",
        "RECV|walk 1 2 3 4",
        f"RECV|c_map 0 {map_id} 1",
        f"RECV|at {CHAR_ID} {map_id} {start[0]} {start[1]} 2 0 13 1 -1 0 0",
        f"RECV|stat {HP} {HP_MAX} {MP} {MP_MAX} 0 0",
        "RECV|c_cond 0 0 1",
        monster_line(419, 5001, *first),
        monster_line(420, 5002, *middle),
        monster_line(421, 5003, *last),
        f"RECV|in 1 Mira - 900 {player[0]} {player[1]} 2 0 0 0",
        "RECV|ncif 3 5001",
        f"SEND|u_s 0 3 5001",
        "RECV|su 1 14881134 3 5001 855 7 11 4001 0 0 0 0 1817 0 0 0 4140",
        f"RECV|mv 3 5003 {last[0]} {last[1]} 4",
        "RECV|get 5001 4001",
    ]
    payload = ("\n".join(lines) + "\n").encode("latin-1")

    def serve() -> None:
        connection, _ = listener.accept()
        try:
            connection.sendall(payload)
            time.sleep(120)
        finally:
            connection.close()
            listener.close()

    threading.Thread(target=serve, daemon=True).start()
    return port


def picker_discovery(port: int) -> Discovery:
    context = WineContext(wine="wine", prefix=Path("/nonexistent"), env={})
    runtime = Path(tempfile.mkdtemp(prefix="ntpl-shot-lock-"))
    os.environ["NTPL_RUNTIME_DIR"] = str(runtime)
    owned = client_lock_path(332)
    owned.parent.mkdir(parents=True, exist_ok=True)
    owned.write_text(f"{os.getppid()}\n", encoding="ascii")
    return Discovery(
        clients=[
            ClientInfo(context, 331, "NosTale - Seedle", port),
            ClientInfo(context, 332, "NosTale - Dander", port + 1),
            ClientInfo(context, 333, "NosTale - Chicken", None),
        ],
        errors=[],
        injector=Path("/nonexistent/Injector.exe"),
        dll=Path("/nonexistent/PacketLogger.dll"),
    )


def main() -> int:
    scene = pick_scene()
    if scene is None:
        print("no walkable scene found")
        return 1
    map_id, grid, start, path = scene
    port = start_fake_client(scene)
    app = Application("127.0.0.1", port, unique=False, select_client=False)
    waypoints = [path[-1]]
    scheduled = {"late": False}

    def scroll_spells() -> bool:
        sidebar = app.sidebar_scroll.get_child()
        ok, rect = app.spell_rules_box.compute_bounds(sidebar)
        adjustment = app.sidebar_scroll.get_vadjustment()
        if ok:
            adjustment.set_value(max(0.0, rect.origin.y - 40))
        else:
            adjustment.set_value(adjustment.get_upper() * 0.62)
        print(
            "spells scroll:",
            ok,
            rect.origin.y if ok else None,
            adjustment.get_upper(),
            adjustment.get_value(),
            file=sys.stderr,
        )
        return False

    def add_spells() -> bool:
        if not app.spells_loaded:
            return True
        added = 0
        for wanted in ("Fire Bolt", "Healing"):
            spell = next(
                (
                    spell
                    for spell in app.spells.values()
                    if spell.active and wanted.lower() in spell.name.lower()
                ),
                None,
            )
            if spell is not None:
                app._add_spell_from_info(spell)
                added += 1
        print("spells added:", added, file=sys.stderr)
        GLib.timeout_add(500, scroll_spells)
        return False

    def late_setup() -> bool:
        app.waypoints[:] = waypoints
        app.path_start = None
        app._rebuild_path()
        app._set_status(
            f"map {map_id} · {grid.width}x{grid.height} · "
            f"{len(app.path_cells)}-cell route · 3 monsters"
        )
        print(
            "shot scene:",
            map_id,
            "start",
            start,
            "path",
            len(app.path_cells),
            "waypoints",
            waypoints,
            file=sys.stderr,
        )

        def redraw() -> bool:
            app.map_area.queue_draw()
            return False

        GLib.timeout_add(400, redraw)
        if MODE == "spells":
            app._ensure_spells_loaded()
            GLib.timeout_add(200, add_spells)
        if MODE == "picker":
            app._show_client_dialog(picker_discovery(port))
        return False

    def arrange() -> bool:
        if MODE == "packets":
            app.notebook.set_current_page(0)
            if app.window is not None:
                app.window.set_default_size(1440, 900)
            return False
        if app.map_grid is None or app.window is None:
            return True
        app.window.set_default_size(1440, 900)
        app.notebook.set_current_page(1)
        if not scheduled["late"]:
            scheduled["late"] = True
            # The map auto-detect and map-surface load run after the first
            # draw; apply the route once they have settled.
            GLib.timeout_add(2500, late_setup)
        return False

    GLib.timeout_add(1200, arrange)
    return int(app.run([]))


if __name__ == "__main__":
    raise SystemExit(main())
