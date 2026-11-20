"""Headless smoke test: Cast speed repeats the skill you press (no bot).

The fake server plays the DLL packet stream. With Cast speed on and the bot
stopped:

1. A manual `u_s 1 3 500` press is remembered.
2. `sr 1` (the server says that skill's cooldown ended) makes the GUI send
   the same press again; a second `sr 1` repeats it again.
3. The DLL's echo of the host's own send does not break the assist.
4. Pressing another skill (`u_s 4`) switches the assist; `sr 1` no longer
   repeats the old one and `sr 4` repeats the new one.
5. Turning Cast speed off stops the repeat.

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
PRESS_4 = "SEND|u_s 4 3 500"


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
        if not any(
            line == "PORTALS|0" or line.startswith("SPEED")
            for line in server.received
        ):
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
            GLib.timeout_add(150, press_sr)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase1 exception: {exc!r}"
            app.quit()
        return False

    def press_sr() -> bool:
        try:
            result["assist_press"] = (
                app.assist_packet == "u_s 1 3 500"
            )
            server.send("RECV|sr 1\n")
            GLib.timeout_add(250, check_first)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"press_sr exception: {exc!r}"
            app.quit()
        return False

    def check_first() -> bool:
        try:
            result["first_repeat"] = server.received.count(PRESS_1) >= 1
            # Echo the host's own send twice like the DLL; the assist must
            # keep its remembered press.
            server.send(PRESS_1 + "\n")
            server.send(PRESS_1 + "\n")
            server.send("RECV|sr 1\n")
            GLib.timeout_add(250, check_second)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check_first exception: {exc!r}"
            app.quit()
        return False

    def check_second() -> bool:
        try:
            result["second_repeat"] = server.received.count(PRESS_1) >= 2
            server.send(PRESS_4 + "\n")
            GLib.timeout_add(150, switch_skill)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check_second exception: {exc!r}"
            app.quit()
        return False

    def switch_skill() -> bool:
        try:
            result["switched"] = app.assist_packet == "u_s 4 3 500"
            before1 = server.received.count(PRESS_1)
            before4 = server.received.count(PRESS_4)
            server.send("RECV|sr 1\n")
            server.send("RECV|sr 4\n")
            GLib.timeout_add(250, check_switch)
            result["before1"] = before1
            result["before4"] = before4
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"switch_skill exception: {exc!r}"
            app.quit()
        return False

    def check_switch() -> bool:
        try:
            result["old_stopped"] = (
                server.received.count(PRESS_1) == result.get("before1")
            )
            result["new_repeat"] = (
                server.received.count(PRESS_4) > result.get("before4", 0)
            )
            app.cast_boost_switch.set_active(False)
            before = server.received.count(PRESS_4)
            server.send("RECV|sr 4\n")
            GLib.timeout_add(250, check_off)
            result["before_off"] = before
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check_switch exception: {exc!r}"
            app.quit()
        return False

    def check_off() -> bool:
        try:
            result["off_stopped"] = (
                server.received.count(PRESS_4) == result.get("before_off")
            )
            result["ok"] = bool(
                result.get("bot_off")
                and result.get("assist_press")
                and result.get("first_repeat")
                and result.get("second_repeat")
                and result.get("switched")
                and result.get("old_stopped")
                and result.get("new_repeat")
                and result.get("off_stopped")
            )
            result["detail"] = (
                f"bot_off={result.get('bot_off')} "
                f"assist={result.get('assist_press')} "
                f"first={result.get('first_repeat')} "
                f"second={result.get('second_repeat')} "
                f"switched={result.get('switched')} "
                f"old_stopped={result.get('old_stopped')} "
                f"new={result.get('new_repeat')} "
                f"off={result.get('off_stopped')} "
                f"lines={server.received[-10:]}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check_off exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(300, wait_attach)
    app.run([])
    server.close()

    if not result.get("ok"):
        result.setdefault("detail", f"lines={server.received[-12:]}")
    print("CAST ASSIST SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
