"""Headless smoke test for far path goals (**the standing-still bug**).

A raw waypoint (or the first goal after a loop wraps) can be many cells away
from the character. The client sends one `walk` to that far destination and
the server rejects it, so the character stands still until the bot times out.

Fake state: player at (25,117) on map 20; the bot goal is (25,121), four cells
away. One `_bot_tick` must send a MOVE at most three cells away (never the far
goal itself), and the route must only advance when the goal is actually
reached.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import MAX_MOVE_LEG_CELLS, Application


class FakeServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.connection = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "FakeServer":
        self.thread.start()
        return self

    def _run(self) -> None:
        try:
            self.connection, _ = self.listener.accept()
        except OSError:
            return

        self.connection.sendall(
            b"STATUS|ready\n"
            b"RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
            b"RECV|rest 1 14881134 0\n"
        )

        buffer = b""
        while not self.stop.is_set():
            try:
                chunk = self.connection.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                text = line.decode("utf-8", "replace")
                self.received.append(text)
                if text == "POS":
                    try:
                        self.connection.sendall(
                            b"STATUS|POS 25 117 14881134\n"
                        )
                    except OSError:
                        return

    def close(self) -> None:
        self.stop.set()
        for sock in (self.connection, self.listener):
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass


def main() -> int:
    server = FakeServer().start()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def setup() -> bool:
        try:
            app._ensure_map_loaded()
            if app.map_grid is None or app.map_grid.map_id != 20:
                app._select_map(20)
            for toggle in (
                app.auto_attack_toggle,
                app.pickup_toggle,
                app.recover_toggle,
                app.recover_mp_toggle,
            ):
                toggle.set_active(False)
            app.loop_toggle.set_active(False)
            # Four cells away: a single MOVE would send `walk 25 121`, which
            # the server rejects.
            app.bot_goals = [(25, 121)]
            app.path_index = 0
            app.pending_target = None
            app.walk_not_before = 0.0
            app.bot_running = True
            app._bot_tick()
            GLib.timeout_add(400, check)
        except Exception as exc:  # noqa: BLE001 - report, do not crash GTK
            result["detail"] = f"setup exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            moves = [
                line for line in server.received
                if line.startswith("MOVE|")
            ]
            far = False
            if moves:
                parts = moves[0].split()
                try:
                    x, y = int(parts[0][5:]), int(parts[1])
                except ValueError:
                    x = y = None
                if x is not None and y is not None:
                    far = max(abs(x - 25), abs(y - 117)) > MAX_MOVE_LEG_CELLS
            result["ok"] = bool(moves) and not far
            result["detail"] = (
                f"moves={moves} far={far} goal_still_pending="
                f"{app.path_index == 0}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1500, setup)
    app.run([])
    server.close()

    print("FAR LEG SMOKE:", "PASS" if result["ok"] else "FAIL", result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
