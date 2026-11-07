"""Headless loot-approach smoke test.

A drop owned by the player lies 5 cells away. The bot must walk to it (the
server rejects `get` from far away), pick it up, and then resume the drawn
path. Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application


class LootServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.x, self.y = 25, 117
        self.got = False
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
            b"RECV|drop 1046 999 30 117 1 0 14881134\n"
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
                elif text.startswith("SEND|get 1 "):
                    # SEND|get 1 <charId> <dropId>
                    parts = text.split()
                    if len(parts) >= 4:
                        try:
                            self.connection.sendall(
                                f"RECV|get 1 14881134 {parts[3]} 0\n".encode()
                            )
                        except OSError:
                            return
                        self.got = True

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
    server = LootServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def start_bot() -> bool:
        try:
            app.path_cells = [(25, 120)]
            app.loop_toggle.set_active(False)
            app.auto_attack_toggle.set_active(False)
            app.pickup_toggle.set_active(True)
            app.recover_toggle.set_active(False)
            app.recover_mp_toggle.set_active(False)
            app._start_bot()
            GLib.timeout_add(6000, check)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            lines = list(server.received)
            moves = [
                i for i, line in enumerate(lines)
                if line.startswith("MOVE|")
            ]
            gets = [
                i for i, line in enumerate(lines)
                if line.startswith("SEND|get 1 14881134 999")
            ]
            move_before = bool(moves) and bool(gets) and moves[0] < gets[0]
            move_after = (
                bool(gets)
                and any(index > gets[0] for index in moves)
            )
            drop_gone = 999 not in app.engine.world.drops
            result["ok"] = (
                server.got
                and move_before
                and move_after
                and drop_gone
            )
            result["detail"] = (
                f"got={server.got} moves={[(lines[i]) for i in moves]} "
                f"gets={[lines[i] for i in gets]} "
                f"move_before={move_before} move_after={move_after} "
                f"drop_gone={drop_gone} status={app.status.get_text()!r}"
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
    print("LOOT SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
