"""Headless player-follow smoke test.

A player stands 10 cells away. With Follow enabled the bot must walk the
character toward that player, stop within two cells, and not attack, target
or pick anything up while the toggle is on. Run through
scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application

PLAYER_NAME = "Follower Friend"
PLAYER_ID = 777777
SELF_ID = 14881134


class FollowServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.x, self.y = 25, 117
        self.player_x, self.player_y = 35, 112
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
            # Low HP/MP on purpose: follow must not sit down to recover.
            b"RECV|stat 30 100 30 100 0 0\n"
            + f"RECV|in 1 {PLAYER_NAME} - {PLAYER_ID} "
              f"{self.player_x} {self.player_y} 0 0 0 0\n".encode()
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
                            (f"STATUS|POS {self.x} {self.y} {SELF_ID} 0\n"
                             f"RECV|mv 1 {PLAYER_ID} {self.player_x} "
                             f"{self.player_y} 6\n").encode()
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
    server = FollowServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def start_bot() -> bool:
        try:
            app.path_cells = []
            app.loop_toggle.set_active(False)
            app.auto_attack_toggle.set_active(False)
            app.pickup_toggle.set_active(False)
            # Recovery is on and the character is below both thresholds:
            # following must still take priority over sitting.
            app.recover_toggle.set_active(True)
            app.recover_mp_toggle.set_active(True)
            app.follow_player_entry.set_text(PLAYER_NAME)
            app.follow_player_toggle.set_active(True)
            app._start_bot()
            GLib.timeout_add(9000, check)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            lines = list(server.received)
            moves = [line for line in lines if line.startswith("MOVE|")]
            attacks = [line for line in lines if "u_s" in line]
            targets = [line for line in lines if line.startswith("TARGET|")]
            pickups = [line for line in lines if line.startswith("SEND|get")]
            rests = [line for line in lines if line.startswith("SEND|rest")]
            distance = max(
                abs(server.x - server.player_x),
                abs(server.y - server.player_y),
            )
            result["ok"] = (
                bool(moves)
                and distance <= 2
                and not attacks
                and not targets
                and not pickups
                and not rests
                and app.follow_player_toggle.get_active()
            )
            result["detail"] = (
                f"moves={moves} end=({server.x},{server.y}) "
                f"distance={distance} attacks={attacks} targets={targets} "
                f"pickups={pickups} rests={rests} "
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
        return start_bot()

    GLib.timeout_add(1200, start_when_map_loaded)
    app.run([])
    server.close()
    print("FOLLOW SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
