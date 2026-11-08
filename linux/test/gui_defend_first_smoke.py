"""Headless smoke test: defend before recovering.

Live failure from 2026-11-08 12:12: the bot dropped 5085 as a ghost, the
monster kept hitting the character, and the bot sat down to recover at 62 %
HP. ``_retaliation_target`` returned None because the attacker had been
removed from the world state, so the old "fight back when attacked" guard
never fired and the bot re-sent ``rest`` every few seconds while being hit.

HP and MP are below 70 % here, and an attacker is hitting the character
from an entity the host never saw (999 has no spawn). The bot must not sit:
being hit is enough to forbid resting, even when the attacker cannot be
resolved. It must keep attacking the monster it can see.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application

UNSEEN_HIT = "RECV|su 3 999 1 14881134 10 9 11 4401\n"


class FakeServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.attacks = 0
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

    def _keep_hitting(self) -> None:
        while not self.stop.wait(1.0):
            self.send(UNSEEN_HIT)

    def _run(self) -> None:
        try:
            self.connection, _ = self.listener.accept()
        except OSError:
            return

        self.send(
            "STATUS|ready\n"
            "RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
            "RECV|rest 1 14881134 0\n"
            "RECV|stat 50 100 50 100 0 0\n"
            "RECV|in 3 200 500 26 119 2 100 100 0 0 0 -1 0 0 -1 - 0 -1 "
            "0 0 0 0 0 0 0 0 0 0 0 0 0\n"
            + UNSEEN_HIT
        )
        threading.Thread(target=self._keep_hitting, daemon=True).start()

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
                    self.attacks += 1

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
            app.auto_attack_toggle.set_active(True)
            app.retaliate_toggle.set_active(True)
            app.pickup_toggle.set_active(False)
            app.recover_toggle.set_active(True)
            app.recover_mp_toggle.set_active(True)
            app.low_hp_pct.set_value(70)
            app.low_mp_pct.set_value(70)
            app.revive_toggle.set_active(True)
            app.loop_toggle.set_active(False)
            app._start_bot()
            GLib.timeout_add(4500, check)
        except Exception as exc:  # noqa: BLE001 - report, do not crash GTK
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            rests = [
                line
                for line in server.received
                if line.startswith("SEND|rest")
            ]
            result["ok"] = bool(
                not rests
                and server.attacks >= 1
                and not app.recovering
            )
            result["detail"] = (
                f"rests={rests} attacks={server.attacks} "
                f"recovering={app.recovering} "
                f"status={app.status.get_text()!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1200, start)
    app.run([])
    server.close()

    print(
        "DEFEND FIRST SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
