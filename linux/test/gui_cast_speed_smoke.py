"""Headless smoke test for the **Cast speed** control.

The DLL shortens the client's skill cooldown from the server's `su` packet;
the GUI has to send the percentage to the DLL, round-trip it through the bot
configuration, and restore 100% on exit. Checks:

* attaching sends the default `CAST|100` (no stale hack from an earlier GUI),
* the spin spans 100-1000%,
* changing it sends the new value immediately,
* the setting round-trips through the bot configuration,
* closing the window sends `CAST|100` before the link goes away.

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
            result["attach_normal"] = cast_lines(server.received)[0] == "CAST|100"
            adjustment = app.cast_speed_spin.get_adjustment()
            result["spin_range"] = (
                int(adjustment.get_lower()) == 100
                and int(adjustment.get_upper()) == 1000
            )
            app.cast_speed_spin.set_value(200)
            GLib.timeout_add(400, phase2)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase1 exception: {exc!r}"
            app.quit()
        return False

    def phase2() -> bool:
        try:
            result["sent200"] = "CAST|200" in server.received
            config = BotConfig(cast_speed=250)
            result["config_value"] = config.cast_speed == 250
            result["round_trip"] = (
                config.to_dict().get("cast_speed") == 250
                and BotConfig.from_dict(config.to_dict()).cast_speed == 250
            )
            app.cast_speed_spin.set_value(config.cast_speed)
            GLib.timeout_add(400, phase3)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase2 exception: {exc!r}"
            app.quit()
        return False

    def phase3() -> bool:
        try:
            result["sent250"] = "CAST|250" in server.received
            result["collected"] = app._collect_config().cast_speed == 250
            result["scale_half"] = abs(app._cast_speed_scale() - 0.4) < 1e-9
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase3 exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(300, phase1)
    app.run([])
    # do_shutdown sends CAST|100; give the fake server a moment to read it.
    deadline = time.time() + 1.0
    while time.time() < deadline and server.received[-1:] != ["CAST|100"]:
        time.sleep(0.02)
    server.close()

    lines = list(server.received)
    casts = cast_lines(lines)
    result["shutdown_normal"] = bool(casts) and casts[-1] == "CAST|100"
    result["casts"] = casts
    result["ok"] = bool(
        result.get("attach_normal")
        and result.get("spin_range")
        and result.get("sent200")
        and result.get("config_value")
        and result.get("round_trip")
        and result.get("sent250")
        and result.get("collected")
        and result.get("scale_half")
        and result.get("shutdown_normal")
    )
    if not result.get("detail") or result["detail"] == "not checked":
        result["detail"] = (
            f"attach_normal={result.get('attach_normal')} "
            f"spin_range={result.get('spin_range')} "
            f"sent200={result.get('sent200')} "
            f"round_trip={result.get('round_trip')} "
            f"sent250={result.get('sent250')} "
            f"collected={result.get('collected')} "
            f"scale_half={result.get('scale_half')} "
            f"shutdown_normal={result.get('shutdown_normal')} "
            f"casts={casts} tail={lines[-8:]}"
        )
    print("CAST SPEED SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
