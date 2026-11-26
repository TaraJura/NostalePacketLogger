"""Headless smoke test: follow a player whose name was never seen.

The fake client knows player 7349 only from `mv 1 7349 ...` — there is no
`in 1 <name> - 7349` spawn; that is what happens to a player who was already
in view when the DLL was injected. Following by name must explain the unnamed
id, and following by that char id must work.

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
PLAYER_ID = 7349
PLAYER_NAME = "root3"


class NamelessServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
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
            b"RECV|stat 100 100 100 100 0 0\n"
            # Only movement: the player's `in 1 name - id` happened before
            # the logger attached, so the host has no name for entity 7349.
            b"RECV|mv 1 7349 30 117 10\n"
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
                            (f"STATUS|POS {self.x} {self.y} {SELF_ID} 0\n")
                            .encode()
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
    server = NamelessServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def start_bot(name: str) -> None:
        app.path_cells = []
        app.loop_toggle.set_active(False)
        app.auto_attack_toggle.set_active(False)
        app.retaliate_toggle.set_active(False)
        app.pickup_toggle.set_active(False)
        app.recover_toggle.set_active(False)
        app.recover_mp_toggle.set_active(False)
        app.follow_player_entry.set_text(name)
        app.follow_player_toggle.set_active(True)
        app._start_bot()

    def first_check() -> bool:
        try:
            status = app.status.get_text()
            explained = (
                f"{PLAYER_NAME} is not in sight" in status
                and "unnamed players (enter the char id)" in status
                and str(PLAYER_ID) in status
            )
            if not explained:
                result["detail"] = f"missing explanation: {status!r}"
                app.quit()
                return False
            # Use the char id the message suggested.
            app._stop_bot()
            start_bot(str(PLAYER_ID))
            GLib.timeout_add(3500, second_check)
            return False
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"first check exception: {exc!r}"
            app.quit()
            return False

    def second_check() -> bool:
        try:
            moves = [
                line for line in server.received if line.startswith("MOVE|")
            ]
            result["ok"] = bool(moves)
            result["detail"] = (
                f"moves={moves} end=({server.x},{server.y}) "
                f"status={app.status.get_text()!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["detail"] = f"second check exception: {exc!r}"
        app.quit()
        return False

    def start_when_map_loaded() -> bool:
        if app.map_grid is None:
            return True
        try:
            start_bot(PLAYER_NAME)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
            return False
        GLib.timeout_add(1600, first_check)
        return False

    GLib.timeout_add(1200, start_when_map_loaded)
    app.run([])
    server.close()
    print("FOLLOW-NAMELESS SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
