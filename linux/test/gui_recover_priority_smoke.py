"""Headless smoke test: recovery priority under attack.

Required order: **Retaliate -> finish the current kill -> recover**, and once
the low-HP threshold is crossed the bot must not walk the route or start a
new fight.

State: 20/100 HP, retaliate on, one monster (500) hits every 0.4 s, a second
monster (501) is in range, and a route is loaded. The bot must fight 500 (not
501, not walk), and after 500 dies it must sit and heal.

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

HIT = b"RECV|su 3 500 1 14881134 10 9 11 4401\n"


class PriorityServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.rests = 0
        self.attacks: list[str] = []
        self.killed_500 = False
        self.connection = None
        self.hit_stop = threading.Event()
        self.stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        threading.Thread(target=self._hits, daemon=True).start()

    def _hits(self) -> None:
        while not self.hit_stop.is_set():
            time.sleep(0.4)
            connection = self.connection
            if connection is None or self.stop.is_set():
                continue
            try:
                connection.sendall(HIT)
            except OSError:
                return

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
            b"RECV|in 3 200 501 26 116 2 100 100 0 0 0 -1 0 0 -1 - "
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
                elif text.startswith("SEND|u_s "):
                    parts = text.split()
                    target = parts[3] if len(parts) >= 4 else ""
                    self.attacks.append(target)
                    if (
                        target == "500"
                        and len(self.attacks) >= 2
                        and not self.killed_500
                    ):
                        self.killed_500 = True
                        self.hit_stop.set()
                        try:
                            self.connection.sendall(
                                b"RECV|st 3 500 15 0 1 100 0 762 2556 "
                                b"762 0\n"
                            )
                        except OSError:
                            return
                elif text.startswith("TARGET|"):
                    entity_id = text.split("|", 1)[1]
                    try:
                        self.connection.sendall(
                            f"STATUS|TARGET {entity_id} OK 1 2\n".encode()
                        )
                    except OSError:
                        return

    def close(self) -> None:
        self.hit_stop.set()
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


def moves(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("MOVE|")]


def main() -> int:
    server = PriorityServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def start() -> bool:
        try:
            app.waypoints = [(30, 117)]
            app.path_start = None
            app.auto_attack_toggle.set_active(True)
            app.retaliate_toggle.set_active(True)
            app.pickup_toggle.set_active(False)
            app.recover_toggle.set_active(True)
            app.low_hp_pct.set_value(80)
            app.recover_mp_toggle.set_active(False)
            app.loop_toggle.set_active(False)
            app._start_bot()
            GLib.timeout_add(9000, check)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            targets = set(server.attacks)
            result["ok"] = bool(
                server.rests >= 1
                and app.recovering
                and targets == {"500"}
                and not moves(server.received)
            )
            result["detail"] = (
                f"attacks={server.attacks} rests={server.rests} "
                f"recovering={app.recovering} "
                f"moves={moves(server.received)} "
                f"status={app.status.get_text()!r}"
            )
        except Exception as exc:  # noqa: BLE001
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
    print("RECOVER PRIORITY SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
