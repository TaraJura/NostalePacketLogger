"""Headless regression test: never attack a monster outside the skill range.

The live 2026-11-06 late session showed the bot sending `u_s 0 3 5087` at a
bee 38 cells away while the UI Range was large enough to select it; the client
played the attack animation at empty space and the server reported a
self-target. The scanned basic skill (866) has range 11, so this smoke sets
UI Range 40, places the monster 38 cells away and asserts that the bot sends
no ncif/u_s and reports the out-of-range target instead.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application


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
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.connection, _ = self.listener.accept()
        except OSError:
            return
        self.connection.sendall(
            b"STATUS|ready\n"
            b"RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
            b"RECV|stat 100 100 1000 1000 0 0\n"
            b"RECV|rest 1 14881134 0\n"
            b"RECV|ski 0 866 866 866 867 868 869 870 871 872 873 "
            b"874 875 876\n"
            b"RECV|in 3 204 5087 60 147 2 100 100 0\n"
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
    server = FakeServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def start_bot() -> bool:
        try:
            app.path_cells = []
            app.auto_attack_toggle.set_active(True)
            app.retaliate_toggle.set_active(False)
            app.move_first_toggle.set_active(True)
            app.skill_entry.set_text("0")
            app.attack_range.set_value(40)  # UI range is larger than 866
            app.attack_cooldown.set_value(1)
            app._start_bot()
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
            return False
        GLib.timeout_add(2000, check)
        return False

    def start_when_spells_loaded() -> bool:
        if not app.spells_loaded:
            return True
        return start_bot()

    def check() -> bool:
        try:
            lines = list(server.received)
            attacks = [
                line
                for line in lines
                if line.startswith(("TARGET|", "SEND|ncif", "SEND|u_s"))
            ]
            status = app.status.get_text()
            result["ok"] = (
                not attacks
                and "not attacking" in status
                and app.bot_running
            )
            result["detail"] = (
                f"attacks={attacks} running={app.bot_running} "
                f"status={status!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1200, start_when_spells_loaded)
    app.run([])
    server.close()

    print(
        "OUT OF RANGE SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
