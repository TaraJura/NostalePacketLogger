"""Headless smoke test for "follow a player and attack their target".

The followed player stands 5 cells away and hits monster 500. With the
Follow frame's "Attack the same target as the player" checkbox on, the bot
must walk close to the player, then attack 500 — and never the other monster
(501) in range. Following must still be the priority.

Run through scripts/dev-test-bot.sh (Xvfb).
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


class FollowAttackServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.x, self.y = 25, 117
        self.player_x, self.player_y = 30, 117
        self.attack_positions: list[tuple[int, int]] = []
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
            b"RECV|stat 100 100 100 100 0 0\n"
            + f"RECV|in 1 {PLAYER_NAME} - {PLAYER_ID} "
              f"{self.player_x} {self.player_y} 0 0 0 0\n".encode()
            + b"RECV|in 3 200 500 31 117 2 100 100 0 0 0 -1 0 0 -1 - "
              b"0 -1 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
            + b"RECV|in 3 200 501 26 116 2 100 100 0 0 0 -1 0 0 -1 - "
              b"0 -1 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
            + f"RECV|su 1 {PLAYER_ID} 3 500 10 9 11 4401\n".encode()
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
                             f"RECV|su 1 {PLAYER_ID} 3 500 10 9 11 4401\n"
                             ).encode()
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
                elif text.startswith("TARGET|"):
                    entity_id = text.split("|", 1)[1]
                    try:
                        self.connection.sendall(
                            f"STATUS|TARGET {entity_id} OK 1 2\n".encode()
                        )
                    except OSError:
                        return
                elif text.startswith("SEND|u_s "):
                    # Where was the character when it attacked?
                    self.attack_positions.append((self.x, self.y))

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
    server = FollowAttackServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def start_bot() -> bool:
        try:
            app.path_cells = []
            app.loop_toggle.set_active(False)
            app.auto_attack_toggle.set_active(False)
            app.retaliate_toggle.set_active(False)
            app.pickup_toggle.set_active(False)
            app.recover_toggle.set_active(False)
            app.recover_mp_toggle.set_active(False)
            app.follow_player_entry.set_text(PLAYER_NAME)
            app.follow_attack_check.set_active(True)
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
            attacks = [line for line in lines if line.startswith("SEND|u_s ")]
            attacked = {
                line.split()[3] for line in attacks if len(line.split()) >= 4
            }
            target_501 = [line for line in lines if line == "TARGET|501"]
            pickups = [line for line in lines if line.startswith("SEND|get")]
            distance = max(
                abs(server.x - server.player_x),
                abs(server.y - server.player_y),
            )
            away_attacks = [
                position
                for position in server.attack_positions
                if max(
                    abs(position[0] - server.player_x),
                    abs(position[1] - server.player_y),
                )
                > 2
            ]
            result["ok"] = (
                bool(moves)
                and distance <= 2
                and attacked == {"500"}
                and not target_501
                and not pickups
                and bool(away_attacks)
                and app.follow_player_toggle.get_active()
                and app.follow_attack_check.get_active()
            )
            result["detail"] = (
                f"moves={moves} end=({server.x},{server.y}) "
                f"distance={distance} attacked={sorted(attacked)} "
                f"away_attacks={away_attacks} "
                f"target501={target_501} pickups={pickups} "
                f"status={app.status.get_text()!r}"
            )
        except Exception as exc:  # noqa: BLE001
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
    print("FOLLOW-ATTACK SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
