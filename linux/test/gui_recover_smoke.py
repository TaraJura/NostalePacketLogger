"""Headless recovery smoke test.

The fake server starts the character at 20/100 HP, below the configured 50%
threshold. The bot must sit (`rest 1 1 <charId>`) and not attack or walk.
When the fake server reports full HP, the bot must stand again.

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


class RecoveryServer:
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
                if text == "SEND|rest 1 1 14881134":
                    self.rests += 1
                    if self.rests == 1:
                        # Sit down and report full HP shortly after.
                        try:
                            self.connection.sendall(
                                b"RECV|rest 1 14881134 1\n"
                            )
                        except OSError:
                            return
                        threading.Timer(0.3, self._heal).start()
                    else:
                        try:
                            self.connection.sendall(
                                b"RECV|rest 1 14881134 0\n"
                            )
                        except OSError:
                            return

    def _heal(self) -> None:
        if self.connection is None:
            return
        try:
            self.connection.sendall(b"RECV|stat 100 100 100 100 0 0\n")
        except OSError:
            pass

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
    server = RecoveryServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def start_bot() -> bool:
        try:
            app.path_cells = [(25, 118)]
            app.loop_toggle.set_active(False)
            app.auto_attack_toggle.set_active(True)
            app.pickup_toggle.set_active(False)
            app.recover_toggle.set_active(True)
            app.low_hp_pct.set_value(50)
            app._start_bot()
            GLib.timeout_add(4000, check)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            # A MOVE after full HP is expected (the bot resumes the path);
            # attacks must not happen while recovering.
            bad = [
                line
                for line in server.received
                if line.startswith(("SEND|ncif", "SEND|u_s"))
            ]
            stats = app.engine.world.stats
            result["ok"] = (
                server.rests == 2
                and not bad
                and stats.hp == 100
                and stats.hp_max == 100
                and not app.recovering
            )
            result["detail"] = (
                f"rests={server.rests} bad_sends={bad} "
                f"hp={stats.hp}/{stats.hp_max} recovering={app.recovering} "
                f"status={app.status.get_text()!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1500, start_bot)
    app.run([])
    server.close()

    print("RECOVER SMOKE:", "PASS" if result["ok"] else "FAIL", result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
