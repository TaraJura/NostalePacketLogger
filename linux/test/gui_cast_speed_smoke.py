"""Headless smoke test for the **Cast speed** switch.

The DLL zeroes the client's skill cooldowns from the server's `su` packet;
the GUI has to send the on/off state to the DLL, round-trip it through the
bot configuration, and restore normal on exit. Checks:

* attaching sends `CAST|0` (no stale hack from an earlier GUI),
* the switch defaults to off,
* turning it on sends `CAST|1` immediately and drops the bot's cast pause,
* the setting round-trips through the bot configuration as a boolean,
* turning it off sends `CAST|0`,
* closing the window sends `CAST|0` before the link goes away.

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


def cast_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("CAST|")]


def main() -> int:
    server = FakeServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}
    deadline = time.time() + 5.0

    def phase1() -> bool:
        try:
            # The GUI connects asynchronously; wait for the attach sync.
            if not cast_lines(server.received):
                if time.time() < deadline:
                    GLib.timeout_add(100, phase1)
                else:
                    result["detail"] = "GUI never sent CAST| on attach"
                    app.quit()
                return False
            result["attach_off"] = cast_lines(server.received)[0] == "CAST|0"
            result["switch_off"] = not app.cast_boost_switch.get_active()
            app.cast_boost_switch.set_active(True)
            GLib.timeout_add(400, phase2)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase1 exception: {exc!r}"
            app.quit()
        return False

    def phase2() -> bool:
        try:
            result["sent_on"] = "CAST|1" in server.received
            result["collect_on"] = app._collect_config().cast_boost is True
            result["scale_zero"] = app._cast_speed_scale() == 0.0
            config = BotConfig(cast_boost=True)
            result["config_value"] = config.cast_boost is True
            result["round_trip"] = (
                config.to_dict().get("cast_boost") is True
                and BotConfig.from_dict(config.to_dict()).cast_boost is True
            )
            # An old percentage config maps to "on".
            result["legacy_on"] = BotConfig.from_dict(
                {"cast_speed": 200}
            ).cast_boost is True
            result["legacy_off"] = BotConfig.from_dict(
                {"cast_speed": 100}
            ).cast_boost is False
            app.cast_boost_switch.set_active(False)
            GLib.timeout_add(400, phase3)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase2 exception: {exc!r}"
            app.quit()
        return False

    def phase3() -> bool:
        try:
            result["sent_off"] = cast_lines(server.received).count(
                "CAST|0"
            ) >= 2
            result["scale_one"] = app._cast_speed_scale() == 1.0
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase3 exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(300, phase1)
    app.run([])
    # do_shutdown sends CAST|0; give the fake server a moment to read it.
    deadline = time.time() + 1.0
    while time.time() < deadline and server.received[-1:] != ["CAST|0"]:
        time.sleep(0.02)
    server.close()

    lines = list(server.received)
    casts = cast_lines(lines)
    result["shutdown_off"] = bool(casts) and casts[-1] == "CAST|0"
    result["casts"] = casts
    result["ok"] = bool(
        result.get("attach_off")
        and result.get("switch_off")
        and result.get("sent_on")
        and result.get("collect_on")
        and result.get("scale_zero")
        and result.get("config_value")
        and result.get("round_trip")
        and result.get("legacy_on")
        and result.get("legacy_off")
        and result.get("sent_off")
        and result.get("scale_one")
        and result.get("shutdown_off")
    )
    if not result.get("detail") or result["detail"] == "not checked":
        result["detail"] = (
            f"attach_off={result.get('attach_off')} "
            f"switch_off={result.get('switch_off')} "
            f"sent_on={result.get('sent_on')} "
            f"collect_on={result.get('collect_on')} "
            f"scale_zero={result.get('scale_zero')} "
            f"round_trip={result.get('round_trip')} "
            f"legacy_on={result.get('legacy_on')} "
            f"legacy_off={result.get('legacy_off')} "
            f"sent_off={result.get('sent_off')} "
            f"scale_one={result.get('scale_one')} "
            f"shutdown_off={result.get('shutdown_off')} "
            f"casts={casts} tail={lines[-8:]}"
        )
    print("CAST SPEED SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
