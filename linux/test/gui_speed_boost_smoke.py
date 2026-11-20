"""Headless smoke test for the **Speed +** control.

The DLL adds a flat bonus to the client's `cond` speed; the GUI only has to
send the configured bonus to the DLL. Checks:

* attaching sends the default `SPEED|0` (no stale boost from an earlier GUI),
* changing the spin button sends the new value immediately,
* the setting round-trips through the bot configuration,
* closing the window sends `SPEED|0` before the link goes away.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading
import time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.botconfig import BotConfig
from ntpl.gui import Application

SELF_ID = 14881134


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
                b"RECV|cond 1 14881134 0 0 10\n"
                b"RECV|rest 1 14881134 0\n"
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
                text = line.decode("utf-8", "replace")
                self.received.append(text)
                if text == "POS":
                    try:
                        self.connection.sendall(
                            f"STATUS|POS 25 117 {SELF_ID} 0\n".encode()
                        )
                    except OSError:
                        return

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


def speed_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("SPEED|")]


def main() -> int:
    server = FakeServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}
    deadline = time.time() + 5.0

    def phase1() -> bool:
        try:
            # The GUI connects asynchronously. Wait for the attach sync (the
            # first SPEED| command) before changing the value, otherwise the
            # test would race the connection.
            if not speed_lines(server.received):
                if time.time() < deadline:
                    GLib.timeout_add(100, phase1)
                else:
                    result["detail"] = "GUI never sent SPEED| on attach"
                    app.quit()
                return False
            result["attach_zero"] = speed_lines(server.received)[0] == "SPEED|0"
            app.speed_boost_spin.set_value(25)
            GLib.timeout_add(500, phase2)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase1 exception: {exc!r}"
            app.quit()
        return False

    def phase2() -> bool:
        try:
            result["sent25"] = "SPEED|25" in server.received
            app.speed_boost_spin.set_value(0)
            GLib.timeout_add(400, phase3)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase2 exception: {exc!r}"
            app.quit()
        return False

    def phase3() -> bool:
        try:
            result["sent_zero_again"] = (
                speed_lines(server.received).count("SPEED|0") >= 2
            )
            config = BotConfig(speed_boost=35)
            result["config_value"] = config.speed_boost == 35
            result["round_trip"] = (
                config.to_dict().get("speed_boost") == 35
                and BotConfig.from_dict(config.to_dict()).speed_boost == 35
            )
            app.speed_boost_spin.set_value(config.speed_boost)
            result["spin_after_config"] = (
                int(app.speed_boost_spin.get_value()) == 35
            )
            result["collected"] = app._collect_config().speed_boost == 35
            GLib.timeout_add(500, phase4)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase3 exception: {exc!r}"
            app.quit()
        return False

    def phase4() -> bool:
        try:
            result["sent35"] = "SPEED|35" in server.received
            # The DLL reports a server correction; the GUI must reset the
            # control to 0 and send SPEED|0.
            server.send("STATUS|SPEED CORRECTED\n")
            GLib.timeout_add(500, phase5)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase4 exception: {exc!r}"
            app.quit()
        return False

    def phase5() -> bool:
        try:
            result["correction_zero"] = (
                int(app.speed_boost_spin.get_value()) == 0
            )
            # The DLL already turned the boost off; the silent spin update
            # must not send a new SPEED command.
            result["correction_no_send"] = (
                speed_lines(server.received)[-1:] == ["SPEED|35"]
            )
            status = app.status.get_text() if app.status is not None else ""
            result["correction_status"] = "server corrected" in status
            server.send("STATUS|SPEED TUNED 1\n")
            GLib.timeout_add(500, phase6)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase5 exception: {exc!r}"
            app.quit()
        return False

    def phase6() -> bool:
        try:
            result["tuned_spin"] = int(app.speed_boost_spin.get_value()) == 1
            # The silent update must not restart the probe with SPEED|1.
            result["tuned_no_send"] = (
                speed_lines(server.received)[-1:] == ["SPEED|35"]
            )
            status = app.status.get_text() if app.status is not None else ""
            result["tuned_status"] = "limited the speed boost to +1" in status
            server.send("STATUS|SPEED APPLIED 3\n")
            GLib.timeout_add(500, phase7)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase6 exception: {exc!r}"
            app.quit()
        return False

    def phase7() -> bool:
        try:
            result["applied_spin"] = (
                int(app.speed_boost_spin.get_value()) == 1
            )
            status = app.status.get_text() if app.status is not None else ""
            result["applied_status"] = "applied" in status
            server.send("STATUS|SPEED UNSUPPORTED\n")
            GLib.timeout_add(500, phase8)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase7 exception: {exc!r}"
            app.quit()
        return False

    def phase8() -> bool:
        try:
            result["unsupported_zero"] = (
                int(app.speed_boost_spin.get_value()) == 0
            )
            result["unsupported_no_send"] = (
                speed_lines(server.received)[-1:] == ["SPEED|35"]
            )
            status = app.status.get_text() if app.status is not None else ""
            result["unsupported_status"] = "rejects even +1" in status
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase8 exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(300, phase1)
    app.run([])
    # do_shutdown sends SPEED|0; give the fake server a moment to read it
    # before the socket is closed.
    deadline = time.time() + 1.0
    while time.time() < deadline and server.received[-1:] != ["SPEED|0"]:
        time.sleep(0.02)
    server.close()

    lines = list(server.received)
    speeds = speed_lines(lines)
    result["shutdown_zero"] = bool(speeds) and speeds[-1] == "SPEED|0"
    result["speeds"] = speeds
    result["ok"] = bool(
        result.get("attach_zero")
        and result.get("sent25")
        and result.get("sent_zero_again")
        and result.get("config_value")
        and result.get("round_trip")
        and result.get("spin_after_config")
        and result.get("collected")
        and result.get("sent35")
        and result.get("correction_zero")
        and result.get("correction_no_send")
        and result.get("correction_status")
        and result.get("tuned_spin")
        and result.get("tuned_no_send")
        and result.get("tuned_status")
        and result.get("applied_spin")
        and result.get("applied_status")
        and result.get("unsupported_zero")
        and result.get("unsupported_no_send")
        and result.get("unsupported_status")
        and result.get("shutdown_zero")
    )
    if not result.get("detail") or result["detail"] == "not checked":
        result["detail"] = (
            f"attach_zero={result.get('attach_zero')} "
            f"sent25={result.get('sent25')} "
            f"sent_zero_again={result.get('sent_zero_again')} "
            f"round_trip={result.get('round_trip')} "
            f"spin_after_config={result.get('spin_after_config')} "
            f"collected={result.get('collected')} "
            f"sent35={result.get('sent35')} "
            f"correction_zero={result.get('correction_zero')} "
            f"correction_no_send={result.get('correction_no_send')} "
            f"correction_status={result.get('correction_status')} "
            f"tuned_spin={result.get('tuned_spin')} "
            f"tuned_no_send={result.get('tuned_no_send')} "
            f"tuned_status={result.get('tuned_status')} "
            f"applied_spin={result.get('applied_spin')} "
            f"applied_status={result.get('applied_status')} "
            f"unsupported_zero={result.get('unsupported_zero')} "
            f"unsupported_no_send={result.get('unsupported_no_send')} "
            f"unsupported_status={result.get('unsupported_status')} "
            f"shutdown_zero={result.get('shutdown_zero')} "
            f"speeds={speeds} tail={lines[-8:]}"
        )
    print("SPEED BOOST SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
