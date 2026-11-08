"""Headless smoke test for **Retaliate without Auto-attack**.

Real scenario from the live session: Auto-attack is off, a monster is hitting
the character, and the sidebar shows Retaliate on. The bot must attack the
attacker and no one else, and a successful cast (the server follows `su` with
`cancel 0 0 -1`) must not be logged as "attack cancelled; retrying".

Fake state: player at (25,117) with 10 % HP (recovery would normally sit);
monster 500 (vnum 200, selected) stands next to the player but is passive,
monster 501 (vnum 212, **not** selected) keeps hitting the player. The bot
must select and attack 501 anyway (retaliation ignores the target filter),
never 500, must not sit to recover while under attack, and the Bot log must
stay free of the false cancellation message.

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
        self.connection = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "FakeServer":
        self.thread.start()
        return self

    def _run(self) -> None:
        try:
            self.connection, _ = self.listener.accept()
        except OSError:
            return

        self.connection.sendall(
            b"STATUS|ready\n"
            b"RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
            b"RECV|stat 50 500 100 100 0 0\n"
            b"RECV|rest 1 14881134 0\n"
            b"RECV|in 3 200 500 26 119 2 100 100 0 0 0 -1 0 0 -1 - 0 -1 "
            b"0 0 0 0 0 0 0 0 0 0 0 0 0\n"
            b"RECV|in 3 212 501 25 119 2 100 100 0 0 0 -1 0 0 -1 - 0 -1 "
            b"0 0 0 0 0 0 0 0 0 0 0 0 0\n"
            # 501 is hitting the character: this makes it the retaliation
            # target. 500 is closer and selected, but passive.
            b"RECV|su 3 501 1 14881134 10 9 11 4401\n"
        )
        threading.Thread(target=self._keep_attacking, daemon=True).start()

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
                    try:
                        self.connection.sendall(
                            (
                                f"STATUS|TARGET {entity_id} "
                                "OK 0x08A00000 0x087B4DD8\n"
                            ).encode()
                        )
                    except OSError:
                        return
                elif text.startswith("SEND|u_s "):
                    parts = text.split()
                    if len(parts) < 4:
                        continue
                    entity_id = parts[3]
                    # A successful cast: the hit lands, then the server ends
                    # the animation with `cancel 0 0 -1`. The bot must not
                    # report that as a cancelled attack.
                    try:
                        self.connection.sendall(
                            (
                                f"RECV|su 1 14881134 3 {entity_id} 855 7 11 "
                                "4001 0 0 1 40 900 0 0 860 2556\n"
                                "RECV|cancel 0 0 -1\n"
                            ).encode()
                        )
                    except OSError:
                        return
                elif text == "POS":
                    try:
                        self.connection.sendall(
                            b"STATUS|POS 25 117 14881134\n"
                        )
                    except OSError:
                        return

    def _keep_attacking(self) -> None:
        """Refresh the retaliation signal: the 5 s window must stay open."""
        while not self.stop.wait(1.0):
            try:
                self.connection.sendall(
                    b"RECV|su 3 501 1 14881134 10 9 11 4401\n"
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
    server = FakeServer().start()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def start_bot() -> bool:
        try:
            app.path_cells = []
            app.auto_attack_toggle.set_active(False)  # the live scenario
            app.retaliate_toggle.set_active(True)
            # "Only selected" with vnum 200: the attacking bee (212) is not
            # selected, so only a filter-ignoring retaliation can fight it.
            app.target_mode.set_selected(1)
            app.target_vnums = {200}
            app._update_target_info()
            app._start_bot()
            GLib.timeout_add(3500, check)
        except Exception as exc:  # noqa: BLE001 - report, do not crash GTK
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            lines = list(server.received)
            selected_501 = any(
                line == "TARGET|501" or "SEND|ncif 3 501" in line
                for line in lines
            )
            attacked_501 = any(
                line.startswith("SEND|u_s") and "0 3 501" in line
                for line in lines
            )
            ignored_500 = not any("500" in line for line in lines)
            no_sit = not any(
                line.startswith("SEND|rest") for line in lines
            )
            buffer = app.bot_log_buffer
            log_text = ""
            if buffer is not None:
                log_text = buffer.get_text(
                    buffer.get_start_iter(), buffer.get_end_iter(), False
                )
            no_false_cancel = "attack cancelled" not in log_text
            # Regression: the recovery-abort path used to start recovery
            # again in the same tick, spamming both messages every 50 ms.
            no_recovery_spam = (
                "attacked while recovering" not in log_text
                and "below 30%; recovering" not in log_text
            )
            result["ok"] = (
                selected_501
                and attacked_501
                and ignored_500
                and no_sit
                and no_false_cancel
                and no_recovery_spam
            )
            result["detail"] = (
                f"select={selected_501} attack={attacked_501} "
                f"ignored_passive={ignored_500} "
                f"no_sit={no_sit} "
                f"no_false_cancel={no_false_cancel} "
                f"no_recovery_spam={no_recovery_spam} "
                f"lines={lines[-10:]}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1500, start_bot)
    app.run([])
    server.close()

    print("RETALIATE SMOKE:", "PASS" if result["ok"] else "FAIL", result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
