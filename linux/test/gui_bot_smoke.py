"""Headless bot smoke test.

A fake server sends the minimum live state: a player at (25,117) on map 20 and
a monster at (25,118). The GUI loads the real map, starts the bot with a short
path, and the test checks that the bot sent:

    ncif 3 500          (select the monster)
    u_s ... 866 ... 500 (auto-attack with the configured skill)
    walk 25 118 0 10    (first path cell)

The fake server answers each walk with a server `tp`, so the path loop advances
without the game.

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
            b"RECV|mv 3 500 25 118 10\n"
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
                if text.startswith("SEND|walk "):
                    parts = text.split()
                    if len(parts) >= 3:
                        try:
                            x, y = int(parts[1]), int(parts[2])
                        except ValueError:
                            continue
                        time.sleep(0.05)
                        try:
                            self.connection.sendall(
                                f"RECV|tp 1 14881134 {x} {y} 0\n".encode()
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
    app = Application("127.0.0.1", server.port)
    result = {"ok": False, "detail": "not checked"}

    def start_bot() -> bool:
        try:
            app.path_cells = [(25, 118), (25, 119)]
            app.loop_toggle.set_active(False)
            app.auto_attack_toggle.set_active(True)
            app.skill_entry.set_text("866")
            app.attack_cooldown.set_value(10)
            app._start_bot()
            GLib.timeout_add(2500, check)
        except Exception as exc:  # noqa: BLE001 - report, do not crash GTK
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            lines = list(server.received)
            has_ncif = any("SEND|ncif 3 500" in line for line in lines)
            has_skill = any(
                line.startswith("SEND|u_s") and "866" in line and "500" in line
                for line in lines
            )
            has_walk = any(line.startswith("SEND|walk 25 118") for line in lines)
            result["ok"] = has_ncif and has_skill and has_walk
            result["detail"] = (
                f"ncif={has_ncif} skill={has_skill} walk={has_walk} "
                f"lines={lines[-10:]}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1500, start_bot)
    app.run([])
    server.close()

    print("BOT SMOKE:", "PASS" if result["ok"] else "FAIL", result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
