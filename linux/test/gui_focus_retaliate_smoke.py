"""Headless smoke test for strict focus fire with Retaliate on.

The bot locks onto monster 500. After two attacks a second monster, 501,
starts hitting the character. Retaliate must NOT steal the live target: the
bot keeps attacking 500 until the server reports its death. Only then does
the recent attacker (501) take over, and while 501 is alive a third monster,
502, hits the character without stealing that fight either. After 501 dies
the bot finishes 502.

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
        self.attacks_501 = 0
        self.attacks_502 = 0
        self.hit_501_sent = False
        self.hit_502_sent = False
        self.killed_500 = False
        self.killed_501 = False
        self.killed_502 = False
        self.kill_500_index: int | None = None
        self.kill_501_index: int | None = None
        self.kill_502_index: int | None = None
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
                    if len(parts) < 4:
                        continue
                    entity_id = parts[3]
                    if entity_id == "500":
                        self.attacks_500 += 1
                        if self.attacks_500 == 2 and not self.hit_501_sent:
                            # A second monster starts hitting the character
                            # while 500 is still alive; it must not steal the
                            # target.
                            self.hit_501_sent = True
                            self.send(
                                "RECV|su 3 501 1 14881134 10 9 11 4401\n"
                            )
                        elif (
                            self.attacks_500 >= 5
                            and not self.killed_500
                        ):
                            # 500 dies; 501 is still the recent attacker and
                            # takes over only now.
                            self.killed_500 = True
                            self.kill_500_index = len(self.received)
                            self.send(
                                "RECV|st 3 500 15 0 1 100 0 762 2556 "
                                "762 0\n"
                                "RECV|su 3 501 1 14881134 10 9 11 4401\n"
                            )
                    elif entity_id == "501":
                        self.attacks_501 += 1
                        if self.attacks_501 == 2 and not self.hit_502_sent:
                            # Another attacker appears while 501 is locked;
                            # strict focus keeps hitting 501.
                            self.hit_502_sent = True
                            self.send(
                                "RECV|in 3 200 502 25 118 2 100 100 0 0 0 "
                                "-1 0 0 -1 - 0 -1 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
                                "RECV|su 3 502 1 14881134 10 9 11 4401\n"
                            )
                        elif (
                            self.attacks_501 >= 5
                            and not self.killed_501
                        ):
                            self.killed_501 = True
                            self.kill_501_index = len(self.received)
                            self.send(
                                "RECV|st 3 501 15 0 1 100 0 762 2556 "
                                "762 0\n"
                                "RECV|su 3 502 1 14881134 10 9 11 4401\n"
                            )
                    elif entity_id == "502":
                        self.attacks_502 += 1
                        if self.attacks_502 >= 3 and not self.killed_502:
                            self.killed_502 = True
                            self.kill_502_index = len(self.received)
                            self.send(
                                "RECV|st 3 502 15 0 1 100 0 762 2556 "
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
            app.retaliate_toggle.set_active(True)
            app.pickup_toggle.set_active(False)
            app.recover_toggle.set_active(False)
            app.recover_mp_toggle.set_active(False)
            app.revive_toggle.set_active(True)
            app.loop_toggle.set_active(False)
            app._start_bot()
            GLib.timeout_add(20000, check)
        except Exception as exc:  # noqa: BLE001 - report, do not crash GTK
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            lines = list(server.received)
            kill_500 = server.kill_500_index
            kill_501 = server.kill_501_index
            kill_502 = server.kill_502_index
            phase_500 = (
                attacked_ids(lines[:kill_500])
                if kill_500 is not None
                else []
            )
            phase_501 = (
                attacked_ids(lines[kill_500:kill_501])
                if kill_500 is not None and kill_501 is not None
                else []
            )
            phase_502 = (
                attacked_ids(lines[kill_501:kill_502])
                if kill_501 is not None and kill_502 is not None
                else []
            )
            after = attacked_ids(lines[kill_502:]) if kill_502 else []
            log_text = ""
            if app.bot_log_buffer is not None:
                buffer = app.bot_log_buffer
                log_text = buffer.get_text(
                    buffer.get_start_iter(), buffer.get_end_iter(), False
                )
            result["ok"] = bool(
                server.hit_501_sent
                and server.hit_502_sent
                and server.killed_500
                and server.killed_501
                and server.killed_502
                and phase_500
                and set(phase_500) == {"500"}
                and phase_501
                and set(phase_501) == {"501"}
                and phase_502
                and set(phase_502) == {"502"}
                and not any("switching from" in line for line in log_text.splitlines())
            )
            result["detail"] = (
                f"phase500={phase_500} phase501={phase_501} "
                f"phase502={phase_502} after={after} "
                f"hit501={server.hit_501_sent} hit502={server.hit_502_sent} "
                f"killed={server.killed_500}/{server.killed_501}/"
                f"{server.killed_502} status={app.status.get_text()!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1200, start)
    app.run([])
    server.close()

    print(
        "FOCUS+RETALIATE SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
