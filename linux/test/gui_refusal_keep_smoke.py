"""Headless smoke test: a hit target is not dropped as a ghost.

Live failure from 2026-11-08 12:06: the bot dropped 5160 at 46 % HP after
two refused casts. Skill 855 has a 0.7 s cooldown, but the refusal path used
to shorten the attack cooldown to 0.1 s, so the bot retried before the skill
cooldown was over, was refused again, and the "2 refusals = ghost" rule
dropped a perfectly healthy target.

Here the first attack lands (`su`), then every later attack is refused
(`cancel 0 0 -1`). The target has been hit, so it is not a ghost: the bot
must keep it, back off to the skill's own cooldown, and not log
"dropping it". Only a never-hit target may be dropped.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application

HIT = (
    "RECV|su 1 14881134 3 500 855 7 11 4001 0 0 1 40 900 0 0 860 2556\n"
)


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
                    self.attacks += 1
                    if self.attacks == 1:
                        # The target is real: one cast lands, then ends.
                        self.send(HIT + "RECV|cancel 0 0 -1\n")
                    else:
                        # Everything after is refused (cooldown/state).
                        self.send("RECV|cancel 0 0 -1\n")

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
            app.retaliate_toggle.set_active(False)
            app.pickup_toggle.set_active(False)
            app.recover_toggle.set_active(False)
            app.recover_mp_toggle.set_active(False)
            app.revive_toggle.set_active(True)
            app.loop_toggle.set_active(False)
            app._start_bot()
            GLib.timeout_add(6500, check)
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
            dropped = "dropping it" in log_text
            kept = "keeping target" in log_text
            result["ok"] = bool(
                server.attacks >= 4
                and not dropped
                and kept
                and app.bot_running
                and app.fight_target_id == 500
            )
            result["detail"] = (
                f"attacks={server.attacks} dropped={dropped} kept={kept} "
                f"running={app.bot_running} "
                f"fight_target={app.fight_target_id} "
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
        "REFUSAL KEEP SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
