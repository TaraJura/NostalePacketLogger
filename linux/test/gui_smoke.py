"""Headless GTK4 smoke test.

Starts a fake packet source on loopback, opens the real GUI against it, lets
the reader thread and the GLib drain timer run, then checks that both packets
became rows and that the DLL's STATUS line reached the status bar.

Run through scripts/dev-test-gui.sh (which supplies Xvfb), not directly.
"""

from __future__ import annotations

import socket
import threading
import time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application


def start_fake_server() -> int:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve() -> None:
        connection, _ = listener.accept()
        connection.sendall(
            b"STATUS|ready\n"
            b"RECV|walk 1 2 3 4\n"
            b"SEND|walk 2 2 0 10\n"
            b"SEND|u_s 1 2\n"
            b"RECV|stat 100 200 50 120 0 0\n"
        )
        # A real portal crossing: the trailing c_map carries the new map id in
        # its second field. This used to set map_id 0 and leave the UI on the
        # old map.
        time.sleep(0.3)
        connection.sendall(
            b"RECV|c_map 0 20 0\n"
            b"RECV|mapout\n"
            b"RECV|at 14881134 85 6 8 2 0 13 1 -1 0 0\n"
            b"RECV|c_map 0 85 1\n"
            b"RECV|in 1 Tester - 123 7 8 2 0 0 0\n"
            b"RECV|in 3 206 500 8 9 2 100 100 0 0 0 -1 0 0 -1 - 0 -1 "
            b"0 0 0 0 0 0 0 0 0 0 0 1 0\n"
        )
        time.sleep(15)
        connection.close()
        listener.close()

    threading.Thread(target=serve, daemon=True).start()
    return port


def main() -> int:
    port = start_fake_server()
    app = Application("127.0.0.1", port, unique=False)
    result = {"ok": False, "detail": "no check ran"}

    def check() -> bool:
        try:
            rows = app.store.get_n_items()
            status = app.status.get_text()
            map_ok = app.map_grid is not None and app.map_surface is not None
            switched = (
                app.map_grid is not None
                and app.map_grid.map_id == 85
                and app.engine.world.map_id == 85
                and "85" in status
            )

            def tip(cell):
                scale, dx, dy = app._map_geometry(
                    app.map_area.get_width(), app.map_area.get_height()
                )
                return app._map_tooltip_text(
                    dx + (cell[0] + 0.5) * scale,
                    dy + (cell[1] + 0.5) * scale,
                )

            own_tip = tip((6, 8))
            player_tip = tip((7, 8))
            monster_tip = tip((8, 9))
            tooltips_ok = (
                own_tip is not None
                and own_tip.startswith("You")
                and player_tip is not None
                and "Player Tester" in player_tip
                and "char id 123" in player_tip
                and monster_tip is not None
                and "Monster #500" in monster_tip
                and "vnum 206" in monster_tip
            )

            # Without a known own position, rebuilding a path must not pretend
            # the first waypoint is the character (that made the bot walk away
            # from the real position and get every walk rejected).
            app.engine.world.x = None
            app.engine.world.y = None
            app.engine.world.self_id = None
            app.waypoints[:] = [(25, 118)]
            app._rebuild_path()
            guard = (
                app.path_cells == []
                and "position unknown" in app.status.get_text()
            )
            facing = app.engine.world.facing
            stats = app.engine.world.stats
            stats_ok = (
                stats.hp == 100
                and stats.hp_max == 200
                and stats.mp == 50
                and stats.mp_max == 120
                and app.stats_area is not None
            )

            result["detail"] = (
                f"rows={rows} status={status!r} map={app.map_grid.map_id if app.map_grid else None} "
                f"surface={app.map_surface is not None} guard={guard} facing={facing} "
                f"hp={stats.hp}/{stats.hp_max} stats_area={app.stats_area is not None} "
                f"switched={switched} tooltips={tooltips_ok} "
                f"size={app.map_area.get_width()}x{app.map_area.get_height()} "
                f"entities={len(app.engine.world.entities)} "
                f"tips={own_tip!r}/{player_tip!r}/{monster_tip!r}"
            )
            result["ok"] = (
                rows >= 2
                and map_ok
                and switched
                and guard
                and facing == (1, 0)
                and stats_ok
                and tooltips_ok
            )
        except Exception as exc:  # noqa: BLE001 - report, do not crash GTK
            result["detail"] = f"exception: {exc!r}"
        app.quit()
        return False

    def show_map() -> bool:
        # The map page must be visible (and allocated) before hover
        # tooltips can be tested against real widget coordinates.
        app.notebook.set_current_page(1)
        GLib.timeout_add(500, check)
        return False

    GLib.timeout_add(1000, show_map)
    app.run([])

    print("GUI SMOKE:", "PASS" if result["ok"] else "FAIL", result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
