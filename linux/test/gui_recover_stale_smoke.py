"""Headless smoke test: recovery is not blocked by a stale fight target.

Live complaint: recovery only started after Stop/Start. Cause: `fight_target_id`
pointed at a monster in reach whose death the host never saw (no `st`), so
"finish the kill first" deferred recovery forever. With the grace check, a
target that has not been attacked for 12 s no longer blocks sitting.

Fake state: 20/100 HP, auto-attack off, a monster in reach, the target set
manually with an old attack timestamp. The bot must sit.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading
import time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application


class StaleRecoveryServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.rests = 0
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
            b"RECV|stat 20 100 50 100 0 0\n"
            b"RECV|rest 1 14881134 0\n"
            b"RECV|in 3 200 500 26 117 2 100 100 0 0 0 -1 0 0 -1 - "
            b"0 -1 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
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
                            b"STATUS|POS 25 117 14881134 0\n"
                        )
                    except OSError:
                        return
                elif text.startswith("SEND|rest 1 1 "):
                    self.rests += 1
                    try:
                        self.connection.sendall(
                            b"RECV|rest 1 14881134 1\n"
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
    server = StaleRecoveryServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def start() -> bool:
        try:
            app.path_cells = []
            app.loop_toggle.set_active(False)
            app.auto_attack_toggle.set_active(False)
            app.retaliate_toggle.set_active(False)
            app.pickup_toggle.set_active(False)
            app.recover_toggle.set_active(True)
            app.low_hp_pct.set_value(50)
            app.recover_mp_toggle.set_active(False)
            app._start_bot()
            # A target in reach whose kill was never reported: the bot has
            # not attacked for longer than the grace window.
            app.fight_target_id = 500
            app.last_attack_sent_at = time.monotonic() - 30.0
            GLib.timeout_add(3000, check)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            result["ok"] = bool(
                server.rests >= 1
                and app.recovering
                and app.fight_target_id == 500
            )
            result["detail"] = (
                f"rests={server.rests} recovering={app.recovering} "
                f"fight_target={app.fight_target_id} "
                f"status={app.status.get_text()!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    def start_when_map_loaded() -> bool:
        if app.map_grid is None:
            return True
        return start()

    GLib.timeout_add(1200, start_when_map_loaded)
    app.run([])
    server.close()
    print("RECOVER-STALE SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
