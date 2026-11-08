"""Headless smoke test: Start while the character is sitting.

Live failure from 2026-11-08 12:15: the user loaded a route whose start
point was away from the character, pressed Start, and every `MOVE` was
ignored — the character was sitting, and the client does not walk while
seated. The host's last POS still said "standing", so `_start_travel` sent
the first leg anyway; the travel tick then kept timing out and never stood
the character up.

Here the game is sitting from the start but the host is first told
"standing" (the stale state at the button press). After the first ignored
MOVE the POS reports the sit flag. The travel tick must stand the character
before sending another leg, and that leg must be accepted.

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
        self.sitting = True
        self.x, self.y = 25, 117
        self.first_move_seen = False
        self.stand_sent = False
        self.moves_while_sitting = 0
        self.moves_after_stand = 0
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

        # The game is sitting, but the host's first information says standing:
        # this is the stale POS state at the moment Start is pressed.
        self.send(
            "STATUS|ready\n"
            "RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
            "RECV|rest 1 14881134 0\n"
            "RECV|stat 200 200 200 200 0 0\n"
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
                    flag = 1 if self.sitting else 0
                    if not self.first_move_seen:
                        flag = 0  # stale: the host still thinks standing
                    self.send(
                        f"STATUS|POS {self.x} {self.y} 14881134 {flag}\n"
                    )
                elif text.startswith("TARGET|"):
                    entity_id = text.split("|", 1)[1]
                    self.send(f"STATUS|TARGET {entity_id} OK 1 2\n")
                elif text.startswith("MOVE|"):
                    self.first_move_seen = True
                    if self.sitting:
                        # The client ignores MoveTo while seated.
                        self.moves_while_sitting += 1
                        continue
                    coords = text.split("|", 1)[1].split()
                    if len(coords) >= 2:
                        x, y = coords[0], coords[1]
                        self.x, self.y = int(x), int(y)
                        self.moves_after_stand += 1
                        self.send(
                            f"RECV|tp 1 14881134 {x} {y} 0\n"
                        )
                elif text.startswith("SEND|rest 1 1 "):
                    if self.sitting:
                        self.sitting = False
                        self.stand_sent = True
                        self.send("RECV|rest 1 14881134 0\n")

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
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def start() -> bool:
        try:
            app.path_cells = []
            app.waypoints = [(25, 118)]
            app.path_start = (25, 118)
            app.auto_attack_toggle.set_active(True)
            app.retaliate_toggle.set_active(False)
            app.pickup_toggle.set_active(False)
            app.recover_toggle.set_active(False)
            app.recover_mp_toggle.set_active(False)
            app.revive_toggle.set_active(True)
            app.loop_toggle.set_active(False)
            app._start_bot()
            GLib.timeout_add(5500, check)
        except Exception as exc:  # noqa: BLE001 - report, do not crash GTK
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            log_text = ""
            if app.bot_log_buffer is not None:
                buffer = app.bot_log_buffer
                log_text = buffer.get_text(
                    buffer.get_start_iter(), buffer.get_end_iter(), False
                )
            stood = (
                "sitting; standing up before walking" in log_text
                or "standing up, then walking" in log_text
            )
            result["ok"] = bool(
                server.stand_sent
                and server.moves_after_stand >= 1
                and not app.engine.world.resting
                and stood
            )
            result["detail"] = (
                f"stand_sent={server.stand_sent} "
                f"moves_while_sitting={server.moves_while_sitting} "
                f"moves_after_stand={server.moves_after_stand} "
                f"resting={app.engine.world.resting} stood={stood} "
                f"status={app.status.get_text()!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1300, start)
    app.run([])
    server.close()

    print(
        "START-SIT SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
