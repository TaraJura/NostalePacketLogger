"""Headless smoke test for death handling and ghost targets.

Two live failures from 2026-11-08:

1. The character died (`stat 0 2791 …`) and the bot kept firing `u_s` at a
   monster; the server answered every one with `cancel`. While dead the bot
   must stay silent and wait for `revive 1 <charId> 0`, then resume.

2. After the revive the bot kept attacking a monster that only existed in the
   client (the server refused every hit). After MAX_CANCEL_STREAK refusals on
   the same target the bot must drop it instead of retrying forever.

Fake state: player at (25,117), monster 500 in range. The server first reports
0 HP, then revives the character; afterwards every attack is answered with
`cancel 0 0 -1` (no `su`), simulating the ghost.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading
import time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application, MAX_CANCEL_STREAK


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
            "RECV|in 3 200 501 25 119 2 100 100 0 0 0 -1 0 0 -1 - 0 -1 "
            "0 0 0 0 0 0 0 0 0 0 0 0 0\n"
            # Dead: 0 HP.
            "RECV|stat 0 2791 100 3827 0 0\n"
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
                elif text.startswith("SEND|u_s "):
                    # Every hit is refused: a client-only ghost entity.
                    time.sleep(0.02)
                    self.send("RECV|cancel 0 0 -1\n")
                elif text == "POS":
                    self.send("STATUS|POS 25 117 14881134\n")

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


def attacks(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("SEND|u_s")]


def main() -> int:
    server = FakeServer().start()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def start() -> bool:
        try:
            app.path_cells = []
            app.auto_attack_toggle.set_active(True)
            app.retaliate_toggle.set_active(True)
            app._start_bot()
            GLib.timeout_add(1500, dead_check)
        except Exception as exc:  # noqa: BLE001 - report, do not crash GTK
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def dead_check() -> bool:
        try:
            result["while_dead"] = attacks(server.received)
            # Revive the character; from now on every attack is cancelled.
            server.send(
                "RECV|stat 1 2791 100 3827 0 0\n"
                "RECV|revive 1 14881134 0\n"
                "RECV|stat 2791 2791 3827 3827 0 0\n"
            )
            GLib.timeout_add(5000, alive_check)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"dead_check exception: {exc!r}"
            app.quit()
        return False

    def alive_check() -> bool:
        try:
            lines = list(server.received)
            alive_attacks = attacks(lines)
            tail = alive_attacks[-MAX_CANCEL_STREAK:]
            status = app.status.get_text()
            log_text = ""
            if app.bot_log_buffer is not None:
                buffer = app.bot_log_buffer
                log_text = buffer.get_text(
                    buffer.get_start_iter(), buffer.get_end_iter(), False
                )
            result["ok"] = bool(
                # Dead: not a single attack was sent.
                result.get("while_dead") == []
                # Alive again: attacks resumed, but the ghost was dropped
                # after the streak instead of being retried forever.
                # One target drops after MAX_CANCEL_STREAK refusals; the
                # next refusal reaches CANCEL_PAUSE_TRIGGER and combat pauses
                # instead of hopping to more targets.
                and len(alive_attacks) == MAX_CANCEL_STREAK + 1
                and "dropping it" in log_text
                and "pausing combat" in log_text
            )
            result["detail"] = (
                f"while_dead={len(result.get('while_dead', []))} "
                f"alive_attacks={len(alive_attacks)} "
                f"dropped={'dropping it' in log_text} "
                f"paused={'pausing combat' in log_text} "
                f"tail={tail} status={status!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"alive_check exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1200, start)
    app.run([])
    server.close()

    print("DEATH SMOKE:", "PASS" if result["ok"] else "FAIL", result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
