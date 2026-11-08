"""Headless smoke test for focus + the Retaliate exception.

The bot locks onto monster 500. After two hits a second monster, 501, starts
hitting the character. Retaliate is the one allowed reason to leave a live
target, so the bot must switch to 501 and finish it.

While 501 is being killed, a third monster, 502, hits the character too. It
must NOT steal the fight: taking the newest hit every time made Retaliate
ping-pong between attackers. When 501 dies, 502 (still a recent attacker)
takes over; once it dies the bot returns to the original target 500.

The Bot log must name the first switch (``retaliate: 501 is attacking you;
switching from 500``) and must not name a switch to 502 while 501 is alive.

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
        self.killed_501 = False
        self.killed_502 = False
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
                            # The first attacker takes over.
                            self.hit_501_sent = True
                            self.send(
                                "RECV|su 3 501 1 14881134 10 9 11 4401\n"
                            )
                    elif entity_id == "501":
                        self.attacks_501 += 1
                        if self.attacks_501 == 2 and not self.hit_502_sent:
                            # A second, closer attacker appears while 501 is
                            # still the locked target. Retaliate must stay on
                            # 501 instead of ping-ponging.
                            self.hit_502_sent = True
                            self.send(
                                "RECV|in 3 200 502 25 118 2 100 100 0 0 0 "
                                "-1 0 0 -1 - 0 -1 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
                                "RECV|su 3 502 1 14881134 10 9 11 4401\n"
                            )
                        elif self.attacks_501 >= 4 and not self.killed_501:
                            # The retaliation target dies; focus moves to the
                            # other attacker (502 is still recent).
                            self.killed_501 = True
                            self.kill_501_index = len(self.received)
                            self.send(
                                "RECV|st 3 501 15 0 1 100 0 762 2556 "
                                "762 0\n"
                            )
                    elif entity_id == "502":
                        self.attacks_502 += 1
                        if self.attacks_502 == 2 and not self.killed_502:
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
            app.retaliate_toggle.set_active(True)  # the exception
            app.pickup_toggle.set_active(False)
            app.recover_toggle.set_active(False)
            app.recover_mp_toggle.set_active(False)
            app.revive_toggle.set_active(True)
            app.loop_toggle.set_active(False)
            app._start_bot()
            GLib.timeout_add(13000, check)
        except Exception as exc:  # noqa: BLE001 - report, do not crash GTK
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            lines = list(server.received)
            first_501 = next(
                (
                    index
                    for index, line in enumerate(lines)
                    if line.startswith("SEND|u_s ")
                    and line.split()[3] == "501"
                ),
                None,
            )
            kill_501 = server.kill_501_index
            kill_502 = server.kill_502_index
            before = (
                attacked_ids(lines[:first_501])
                if first_501 is not None
                else []
            )
            stick = (
                attacked_ids(lines[first_501:kill_501])
                if first_501 is not None and kill_501 is not None
                else []
            )
            after_501 = (
                attacked_ids(lines[kill_501:kill_502])
                if kill_501 is not None and kill_502 is not None
                else []
            )
            after_502 = attacked_ids(lines[kill_502:]) if kill_502 else []
            log_text = ""
            if app.bot_log_buffer is not None:
                buffer = app.bot_log_buffer
                log_text = buffer.get_text(
                    buffer.get_start_iter(), buffer.get_end_iter(), False
                )
            explained = (
                "retaliate: 501 is attacking you; switching from 500"
                in log_text
            )
            no_ping_pong = "retaliate: 502" not in log_text
            result["ok"] = bool(
                server.hit_501_sent
                and server.hit_502_sent
                and server.killed_501
                and server.killed_502
                and before
                and set(before) == {"500"}
                and stick
                and set(stick) == {"501"}
                and after_501
                and set(after_501) == {"502"}
                and after_502
                and set(after_502) == {"500"}
                and explained
                and no_ping_pong
            )
            result["detail"] = (
                f"before={before} stick={stick} after_501={after_501} "
                f"after_502={after_502} hit501={server.hit_501_sent} "
                f"hit502={server.hit_502_sent} "
                f"killed501={server.killed_501} "
                f"killed502={server.killed_502} "
                f"explained={explained} no_ping_pong={no_ping_pong}"
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
