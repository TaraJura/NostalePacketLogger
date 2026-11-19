"""Headless smoke test: fight in place, but never freeze on an unreachable one.

The bot attacks monster 500 with skill 260 (Energy Bolt, Skill.dat range 6).
While the monster is in range the bot fights without taking a path leg. Then
the monster is moved to 8 cells away — outside the skill's range but inside
the search radius. The bot must NOT stand there holding an unreachable
target: the drawn route must resume (a MOVE appears). The target is kept.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application

SELF_ID = 14881134


class HoldServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.x, self.y = 25, 117
        self.moved_at: int | None = None
        self.death_at: int | None = None
        self.attacks = 0
        self.connection = None
        self.stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def send(self, text: str) -> None:
        try:
            self.connection.sendall(text.encode())
        except OSError:
            pass

    def _run(self) -> None:
        try:
            self.connection, _ = self.listener.accept()
        except OSError:
            return
        self.send(
            "STATUS|ready\n"
            "RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
            "RECV|stat 100 100 100 100 0 0\n"
            "RECV|rest 1 14881134 0\n"
            "RECV|in 3 200 500 26 117 2 100 100 0 0 0 -1 0 0 -1 - "
            "0 -1 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
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
                    self.send(f"STATUS|POS {self.x} {self.y} {SELF_ID} 0\n")
                elif text.startswith("MOVE|"):
                    parts = text.split()
                    if len(parts) >= 2:
                        try:
                            self.x = int(parts[0][5:])
                            self.y = int(parts[1])
                        except ValueError:
                            continue
                        self.send(f"SEND|walk {self.x} {self.y} 0 10\n")
                elif text.startswith("TARGET|"):
                    entity_id = text.split("|", 1)[1]
                    self.send(f"STATUS|TARGET {entity_id} OK 1 2\n")
                elif text.startswith("SEND|u_s "):
                    self.attacks += 1
                    # Echo the client's own send (as the DLL does) so the
                    # engine learns slot 0, then answer with the skill.
                    self.send(
                        "SEND|u_s 0 3 500\n"
                        "RECV|su 1 14881134 3 500 260 9 11 4401\n"
                    )
                    if self.moved_at is None:
                        # Out of Energy Bolt's 6-cell range, still in the
                        # 15-cell search radius: the bot must hold.
                        self.moved_at = len(self.received)
                        self.send("RECV|mv 3 500 33 117 10\n")

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


def moves(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("MOVE|")]


def main() -> int:
    server = HoldServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def start() -> bool:
        try:
            app.waypoints = [(30, 117)]
            app.path_start = None
            app.auto_attack_toggle.set_active(True)
            app.retaliate_toggle.set_active(False)
            app.pickup_toggle.set_active(False)
            app.recover_toggle.set_active(False)
            app.recover_mp_toggle.set_active(False)
            app.loop_toggle.set_active(False)
            app._start_bot()
            GLib.timeout_add(4500, check_range)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check_range() -> bool:
        try:
            moved = server.moved_at
            before = moves(server.received[:moved]) if moved else []
            after = moves(server.received[moved:]) if moved else []
            result["ok"] = bool(
                server.attacks
                and not before
                and after
                and app.bot_running
            )
            result["detail"] = (
                f"attacks={server.attacks} in_range_moves={before} "
                f"resumed={after} running={app.bot_running} "
                f"status={app.status.get_text()!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check_range exception: {exc!r}"
        app.quit()
        return False

    def start_when_map_loaded() -> bool:
        if app.map_grid is None:
            return True
        return start()

    GLib.timeout_add(1200, start_when_map_loaded)
    app.run([])
    server.close()
    print("HOLD SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
