"""Headless regression test: the server rejects every walk.

The live 2026-11-06 session showed the bot sending one path cell every three
seconds while the server answered nothing (the character was not moving). The
bot must not walk further and further away from a position the server does not
accept: after MAX_REJECTED_WALKS timeouts it stops and says why.

The fake server sends the map position but deliberately never answers a walk
with `tp`/`mv`.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading
import time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import MAX_REJECTED_WALKS, Application


class SilentServer:
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
        self.thread.start()

    def _run(self) -> None:
        try:
            self.connection, _ = self.listener.accept()
        except OSError:
            return

        self.connection.sendall(
            b"STATUS|ready\n"
            b"RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
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
                # No tp/mv answer on purpose.

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
    server = SilentServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def start_bot() -> bool:
        try:
            app.path_cells = [(25, 118), (25, 119), (25, 120), (25, 121)]
            app.loop_toggle.set_active(True)
            app.auto_attack_toggle.set_active(False)
            app.attack_walk_delay = 0.0
            app.walk_timeout = 0.15
            app.walk_timeout_per_cell = 0.0
            app._start_bot()
            GLib.timeout_add(2000, check)
        except Exception as exc:  # noqa: BLE001 - report, do not crash GTK
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            moves = [
                line for line in server.received if line.startswith("MOVE|")
            ]
            status = app.status.get_text()
            stopped = not app.bot_running
            gave_up = "walks rejected" in status
            result["ok"] = (
                stopped
                and gave_up
                and len(moves) == MAX_REJECTED_WALKS
            )
            result["detail"] = (
                f"stopped={stopped} gave_up={gave_up} moves={len(moves)} "
                f"status={status!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1200, start_bot)
    app.run([])
    server.close()

    print("BOT REJECT SMOKE:", "PASS" if result["ok"] else "FAIL", result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
