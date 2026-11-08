"""Headless smoke test for the death config option.

The server offers the in-place revival dialog (`dlgi #revival^0 …`, "Continue
here? You'll need 10 Seeds of Power") after a death.

* Revive **off** (default): the bot stops on death and sends nothing.
* Revive **on**: the bot answers the dialog with the client's own Continue
  burst (`mall 50`, `c_close …`, `bp_close`, `f_stash_end`) and resumes when
  the server sends `revive`.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application

REVIVAL_DIALOG = "RECV|dlgi #revival^0 #revival^1 571 0 0\n"


class FakeServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.revived = False
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
            "RECV|stat 0 2791 3827 3827 0 0\n"
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
                    self.send("STATUS|POS 25 117 14881134\n")
                elif text == "SEND|mall 50" and not self.revived:
                    # The bot answered the revival dialog: revive it.
                    self.revived = True
                    self.send(
                        "RECV|cond 1 14881134 0 0 10\n"
                        "RECV|revive 1 14881134 0\n"
                        "RECV|stat 2791 2791 3827 3827 0 0\n"
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


def main() -> int:
    server = FakeServer().start()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def phase_a() -> bool:
        try:
            app.revive_toggle.set_active(False)
            app._start_bot()
            GLib.timeout_add(1200, check_a)
        except Exception as exc:  # noqa: BLE001 - report, do not crash GTK
            result["detail"] = f"phase_a exception: {exc!r}"
            app.quit()
        return False

    def check_a() -> bool:
        try:
            lines = list(server.received)
            result["stopped"] = not app.bot_running
            result["no_revive_sent"] = not any(
                line.startswith("SEND|mall") for line in lines
            )
            result["death_status"] = "revive is off" in app.status.get_text()
            # Second phase: offer the dialog and enable the revive option.
            server.send(REVIVAL_DIALOG)
            app.revive_toggle.set_active(True)
            app._start_bot()
            GLib.timeout_add(1800, check_b)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check_a exception: {exc!r}"
            app.quit()
        return False

    def check_b() -> bool:
        try:
            lines = list(server.received)
            sent_mall = any(line == "SEND|mall 50" for line in lines)
            sent_bp = any(line == "SEND|bp_close" for line in lines)
            result["ok"] = bool(
                result.get("stopped")
                and result.get("no_revive_sent")
                and result.get("death_status")
                and sent_mall
                and sent_bp
                and "dead" not in app.status.get_text()
            )
            result["detail"] = (
                f"stopped_off={result.get('stopped')} "
                f"no_revive_off={result.get('no_revive_sent')} "
                f"death_status={result.get('death_status')} "
                f"sent_mall={sent_mall} sent_bp={sent_bp} "
                f"revived={server.revived} "
                f"running={app.bot_running} status={app.status.get_text()!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check_b exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1200, phase_a)
    app.run([])
    server.close()

    print("REVIVE SMOKE:", "PASS" if result["ok"] else "FAIL", result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
