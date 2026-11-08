"""Headless smoke test for target focus.

Two wolves are in range: 500 at distance 2 and 501 at distance 4. The bot
must keep attacking 500 until it dies, even when 501 is moved next to the
character (distance 1) mid-fight. Only after 500's death may it switch.
Retaliate is off here, so this is the pure focus behaviour.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading

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
        self.attacks_500 = 0
        self.kill_after_index: int | None = None
        self.connection = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "FakeServer":
        self.thread.start()
        return self

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
            "RECV|rest 1 14881134 0\n"
            "RECV|in 3 200 500 26 119 2 100 100 0 0 0 -1 0 0 -1 - 0 -1 "
            "0 0 0 0 0 0 0 0 0 0 0 0 0\n"
            "RECV|in 3 200 501 26 121 2 100 100 0 0 0 -1 0 0 -1 - 0 -1 "
            "0 0 0 0 0 0 0 0 0 0 0 0 0\n"
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
                if text.startswith("TARGET|"):
                    entity_id = text.split("|", 1)[1]
                    self.send(f"STATUS|TARGET {entity_id} OK 1 2\n")
                elif text == "POS":
                    self.send("STATUS|POS 25 117 14881134\n")
                elif text.startswith("SEND|u_s "):
                    parts = text.split()
                    if len(parts) >= 4 and parts[3] == "500":
                        self.attacks_500 += 1
                        if self.attacks_500 == 2:
                            # A closer monster appears: focus must hold.
                            self.send("RECV|mv 3 501 25 118 10\n")
                        elif self.attacks_500 >= 4:
                            # Kill the locked target.
                            self.kill_after_index = len(self.received)
                            self.send(
                                "RECV|st 3 500 15 0 1 100 0 762 2556 "
                                "762 0\n"
                            )

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


def attacked_ids(lines: list[str]) -> list[str]:
    result = []
    for line in lines:
        if not line.startswith("SEND|u_s "):
            continue
        parts = line.split()
        if len(parts) >= 4:
            result.append(parts[3])
    return result


def main() -> int:
    server = FakeServer().start()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def start() -> bool:
        try:
            app.path_cells = []
            app.auto_attack_toggle.set_active(True)
            app.retaliate_toggle.set_active(False)  # pure focus
            app.pickup_toggle.set_active(False)
            app.recover_toggle.set_active(False)
            app.recover_mp_toggle.set_active(False)
            app.revive_toggle.set_active(True)
            app.loop_toggle.set_active(False)
            app._start_bot()
            GLib.timeout_add(9000, check)
        except Exception as exc:  # noqa: BLE001 - report, do not crash GTK
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            lines = list(server.received)
            kill_index = server.kill_after_index
            before = attacked_ids(
                lines[:kill_index] if kill_index is not None else []
            )
            after = attacked_ids(
                lines[kill_index:] if kill_index is not None else []
            )
            result["ok"] = bool(
                kill_index is not None
                and before
                and set(before) == {"500"}
                and "501" in after
            )
            result["detail"] = (
                f"attacks_before_kill={before} "
                f"attacks_after_kill={after} "
                f"attacks_500={server.attacks_500} "
                f"kill_seen={kill_index is not None}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1200, start)
    app.run([])
    server.close()

    print("FOCUS SMOKE:", "PASS" if result["ok"] else "FAIL", result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
