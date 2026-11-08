"""Headless smoke test for pairing cancels with the cast they end.

The server answers a landed cast with `su` and later `cancel 0 0 -1` when
the animation ends. With a fast skill the next attack is already sent when
that cancel arrives, so the cancel must not be treated as a refusal just
because the bot's newest attack has no hit yet. Here every attack gets `su`
immediately and `cancel` 0.4 s later while the bot attacks every 0.3 s: the
bot must keep attacking with no "attack cancelled; retrying" and no target
dropped.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application
from ntpl.spells import SpellInfo

BASIC_SKILL = 855
CANCEL_DELAY_S = 0.4


class FakeServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.attacks = 0
        self.timers: list[threading.Timer] = []
        self.connection = None
        self.send_lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "FakeServer":
        self.thread.start()
        return self

    def send(self, text: str) -> None:
        with self.send_lock:
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
                elif text.startswith("SEND|u_s 0 3 500"):
                    self.attacks += 1
                    # The hit lands at once; the animation ends later.
                    self.send(
                        f"RECV|su 1 14881134 3 500 {BASIC_SKILL} 7 11 4001 "
                        "0 0 1 40 900 0 0 860 2556\n"
                    )
                    timer = threading.Timer(
                        CANCEL_DELAY_S,
                        self.send,
                        args=("RECV|cancel 0 0 -1\n",),
                    )
                    timer.daemon = True
                    self.timers.append(timer)
                    timer.start()

    def close(self) -> None:
        self.stop.set()
        for timer in self.timers:
            timer.cancel()
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
    ready_tries = {"count": 0}

    def start() -> bool:
        if not app.spells_loaded and ready_tries["count"] < 10:
            # Application loads Skill.dat in a thread; the finish callback
            # replaces app.spells, so install the probe after it.
            ready_tries["count"] += 1
            return True
        try:
            app.path_cells = []
            # A fast basic attack (0.3 s) so the next `u_s` always leaves
            # before the previous cast's delayed `cancel` arrives.
            app.spells[BASIC_SKILL] = SpellInfo(
                vnum=BASIC_SKILL,
                name="cancel-pair probe",
                key=0,
                skill_type=1,
                class_id=0,
                target_type=0,
                range=6,
                cast_time=0.0,
                cooldown=0.3,
                mp_cost=0,
            )
            app.engine.world.basic_skill = BASIC_SKILL
            app.auto_attack_toggle.set_active(True)
            app.retaliate_toggle.set_active(False)
            app.pickup_toggle.set_active(False)
            app.recover_toggle.set_active(False)
            app.recover_mp_toggle.set_active(False)
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
            log_text = ""
            if app.bot_log_buffer is not None:
                buffer = app.bot_log_buffer
                log_text = buffer.get_text(
                    buffer.get_start_iter(), buffer.get_end_iter(), False
                )
            dropped = "dropping it" in log_text
            false_cancel = "attack cancelled; retrying" in log_text
            result["ok"] = bool(
                server.attacks >= 6
                and not dropped
                and not false_cancel
                and app.bot_running
            )
            result["detail"] = (
                f"attacks={server.attacks} dropped={dropped} "
                f"false_cancel={false_cancel} running={app.bot_running} "
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
        "CANCEL PAIR SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
