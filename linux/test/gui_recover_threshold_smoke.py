"""Headless smoke test: recovery uses the configured HP threshold.

HP is 20 %, the configured `Below HP %` is 15 %, so no recovery may trigger:
the bot must walk its route and never sit. A fixed percentage (the removed
25 % rule) would sit here instead, so this pins the behaviour to the
configured threshold.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application


class ThresholdServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.rests = 0
        self.x, self.y = 25, 117
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
            b"RECV|stat 20 100 50 100 0 0\n"
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
                            f"STATUS|POS {self.x} {self.y} 14881134 0\n".encode()
                        )
                    except OSError:
                        return
                elif text.startswith("MOVE|"):
                    parts = text.split()
                    if len(parts) >= 2:
                        try:
                            self.x = int(parts[0][5:])
                            self.y = int(parts[1])
                        except ValueError:
                            continue
                        try:
                            self.connection.sendall(
                                f"SEND|walk {self.x} {self.y} 0 10\n".encode()
                            )
                        except OSError:
                            return
                elif text.startswith("SEND|rest 1 1 "):
                    self.rests += 1

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
    server = ThresholdServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def start() -> bool:
        try:
            app.waypoints = [(30, 117)]
            app.path_start = None
            app.loop_toggle.set_active(False)
            app.auto_attack_toggle.set_active(False)
            app.retaliate_toggle.set_active(False)
            app.pickup_toggle.set_active(False)
            app.recover_toggle.set_active(True)
            # 20 % HP is ABOVE this threshold: no recovery may trigger.
            app.low_hp_pct.set_value(15)
            app.recover_mp_toggle.set_active(False)
            app._start_bot()
            GLib.timeout_add(5000, check)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            moves = [
                line
                for line in server.received
                if line.startswith("MOVE|")
            ]
            result["ok"] = bool(
                moves
                and server.rests == 0
                and not app.recovering
                and not app.recovery_pending
            )
            result["detail"] = (
                f"moves={moves} rests={server.rests} "
                f"recovering={app.recovering} "
                f"pending={app.recovery_pending} "
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
    print("RECOVER THRESHOLD SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
