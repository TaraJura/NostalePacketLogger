"""Headless smoke test: Cast speed works without the bot.

The fake server plays the DLL packet stream. With Cast speed on and the bot
stopped:

1. A manual `u_s 1 3 500` press is refused by the server
   (`cancel 0 1 -1`). The GUI must hold that exact packet and re-send it when
   the skill's cooldown ends (`sr 1`).
2. The same with a press refused mid-cast, flushed by the server's next self
   `su` instead of an `sr`.
3. The DLL's echo of the host's own `SEND|u_s ...` must not be treated as a
   new key press (no loop).

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading
import time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application

SELF_ID = 14881134
PRESS_1 = "SEND|u_s 1 3 500"
PRESS_2 = "SEND|u_s 2 3 500"


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
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.connection, _ = self.listener.accept()
        except OSError:
            return
        try:
            self.connection.sendall(
                b"STATUS|ready\n"
                b"RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
                b"RECV|stat 100 100 400 400 0 0\n"
                b"RECV|rest 1 14881134 0\n"
                b"RECV|ski 0 866 866 866 867 868 869 870 871 872 873\n"
            )
        except OSError:
            return
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
                self.received.append(line.decode("utf-8", "replace"))

    def send(self, text: str) -> None:
        try:
            self.connection.sendall(text.encode())
        except OSError:
            pass

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
    server = FakeServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}
    deadline = time.time() + 5.0

    def wait_attach() -> bool:
        if not any(line == "PORTALS|0" or line.startswith("SPEED")
                   for line in server.received):
            if time.time() < deadline:
                return True
            result["detail"] = "GUI never attached"
            app.quit()
            return False
        app.cast_boost_switch.set_active(True)
        GLib.timeout_add(150, phase1)
        return False

    def phase1() -> bool:
        try:
            result["bot_off"] = not app.bot_running
            server.send(PRESS_1 + "\n")
            server.send("RECV|cancel 0 1 -1\n")
            GLib.timeout_add(200, phase1_check)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase1 exception: {exc!r}"
            app.quit()
        return False

    def phase1_check() -> bool:
        try:
            result["held"] = app.manual_cast_packet == "u_s 1 3 500"
            result["status"] = "queued" in (
                app.status.get_text() if app.status is not None else ""
            )
            server.send("RECV|sr 1\n")
            GLib.timeout_add(250, phase2)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase1check exception: {exc!r}"
            app.quit()
        return False

    def phase2() -> bool:
        try:
            result["resent_sr"] = server.received.count(PRESS_1) >= 1
            result["cleared"] = app.manual_cast_packet is None
            # Echo the host's own packet like the DLL send hook does; it
            # must not become a new held press.
            server.send(PRESS_1 + "\n")
            GLib.timeout_add(200, phase2_check)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase2 exception: {exc!r}"
            app.quit()
        return False

    def phase2_check() -> bool:
        try:
            result["no_echo_hold"] = app.manual_cast_packet is None
            server.send(PRESS_2 + "\n")
            server.send("RECV|cancel 0 2 -1\n")
            GLib.timeout_add(200, phase2_flush)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase2check exception: {exc!r}"
            app.quit()
        return False

    def phase2_flush() -> bool:
        try:
            result["held2"] = app.manual_cast_packet == "u_s 2 3 500"
            # A self cast finishing frees the server for the held press.
            server.send(
                "RECV|su 1 14881134 3 500 867 3 5 0 0 1 100 0 2 0 400 400\n"
            )
            GLib.timeout_add(250, phase3)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase2flush exception: {exc!r}"
            app.quit()
        return False

    def phase3() -> bool:
        try:
            result["resent_su"] = server.received.count(PRESS_2) >= 1
            result["ok"] = bool(
                result.get("bot_off")
                and result.get("held")
                and result.get("status")
                and result.get("resent_sr")
                and result.get("cleared")
                and result.get("no_echo_hold")
                and result.get("held2")
                and result.get("resent_su")
            )
            result["detail"] = (
                f"bot_off={result.get('bot_off')} held={result.get('held')} "
                f"status={result.get('status')} "
                f"resent_sr={result.get('resent_sr')} "
                f"cleared={result.get('cleared')} "
                f"no_echo_hold={result.get('no_echo_hold')} "
                f"held2={result.get('held2')} "
                f"resent_su={result.get('resent_su')} "
                f"lines={server.received[-8:]}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase3 exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(300, wait_attach)
    app.run([])
    server.close()

    if not result.get("ok"):
        result.setdefault(
            "detail", f"lines={server.received[-12:]}"
        )
    print("MANUAL CAST SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
