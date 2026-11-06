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
        connection.sendall(b"STATUS|ready\nRECV|walk 1 2 3 4\nSEND|u_s 1 2\n")
        time.sleep(15)
        connection.close()
        listener.close()

    threading.Thread(target=serve, daemon=True).start()
    return port


def main() -> int:
    port = start_fake_server()
    app = Application("127.0.0.1", port)
    result = {"ok": False, "detail": "no check ran"}

    def check() -> bool:
        try:
            rows = app.store.get_n_items()
            status = app.status.get_text()
            map_ok = app.map_grid is not None and app.map_surface is not None
            result["detail"] = (
                f"rows={rows} status={status!r} map={app.map_grid.map_id if app.map_grid else None} "
                f"surface={app.map_surface is not None}"
            )
            result["ok"] = rows >= 2 and status == "ready" and map_ok
        except Exception as exc:  # noqa: BLE001 - report, do not crash GTK
            result["detail"] = f"exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1500, check)
    app.run([])

    print("GUI SMOKE:", "PASS" if result["ok"] else "FAIL", result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
