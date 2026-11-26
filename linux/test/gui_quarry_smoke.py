"""Headless smoke test for the Great Quarry production minigame.

The fake server describes the live Great Quarry object (mlo_info: 2,000
production points, the six reward ranges) and replies to the claim with a
new point balance. Play must send the three client packets in order — start,
16,000-point result (the default 5x minimum), 5x claim — show the confirmed
balance, and refuse to start when the production points are below the
100-point claim cost.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application

#: The live Great Quarry line as it appears in the client's DLL log. The
#: window was opened before this host attached, so the server sends no new
#: mlo_info: the attach-time log seed must supply it.
LOG_INFO = (
    "18:10:35.118 [RECV] mlo_info 0 3125 5 2000 0 0 0 999 1000 4999 "
    "5000 7999 8000 11999 12000 15999 16000 1000000\r\n"
)


class QuarryServer:
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
            self.connection.sendall(
                (
                    "STATUS|ready\n"
                    "RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
                    "RECV|stat 100 200 50 120 0 0\n"
                ).encode()
            )
        except OSError:
            return
        buffer = b""
        while not self.stop.is_set():
            try:
                chunk = self.connection.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                text = line.decode("utf-8", "replace")
                self.received.append(text)
                if text.startswith("SEND|mg 4 "):
                    # The server accepted the claim and charged 100 points.
                    try:
                        self.connection.sendall(
                            b"RECV|mlo_mg 3125 1900 0 0 100 100\n"
                        )
                    except OSError:
                        return

    def close(self) -> None:
        self.stop.set()
        for sock in (self.connection, self.listener):
            if sock is None:
                continue
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


def main() -> int:
    log = tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="latin1"
    )
    with log:
        log.write(LOG_INFO)
    server = QuarryServer()
    app = Application(
        "127.0.0.1", server.port, unique=False, log_file=log.name
    )
    result: dict[str, object] = {"ok": False, "detail": "not checked"}
    state = {"budget": 80}

    def sent_mg() -> list[str]:
        return [
            line for line in server.received if line.startswith("SEND|mg ")
        ]

    def wait_ready() -> bool:
        state["budget"] -= 1
        if app.engine.world.minigame.points != 2000:
            if state["budget"] <= 0:
                result["detail"] = "mlo_info never reached the engine"
                app.quit()
                return False
            return True
        if app.client is None or not app.client.connected:
            return True
        if app.quarry_status is None:
            result["detail"] = "the production section was not built"
            app.quit()
            return False
        app.notebook.set_current_page(6)  # Minigame
        app._quarry_play()
        GLib.timeout_add(3500, check)
        return False

    def check() -> bool:
        try:
            packets = sent_mg()
            expected = [
                "SEND|mg 1 1 3125",
                "SEND|mg 3 1 3125 16000 16000",
                "SEND|mg 4 1 3125 5",
            ]
            status = app.quarry_status.get_text()
            label = app.quarry_points_label.get_text()
            result["packets"] = packets
            result["status"] = status
            result["label"] = label
            claimed = (
                packets == expected
                and "5x claimed" in status
                and "1,900" in status
                and "1,900" in label
                and app.quarry_running is False
            )
            result["claimed"] = claimed

            # Below the 100-point claim cost the sequence must not start.
            app.engine.world.minigame.points = 50
            state["guard_before"] = len(sent_mg())
            app._quarry_play()
            GLib.timeout_add(300, check_guard)
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["detail"] = f"check exception: {exc!r}"
            app.quit()
        return False

    def check_guard() -> bool:
        try:
            before = state.get("guard_before", 0)
            status = app.quarry_status.get_text()
            result["guard"] = (
                len(sent_mg()) == before
                and app.quarry_running is False
                and "needs at least 100" in status
            )
            result["ok"] = bool(result.get("claimed") and result["guard"])
            result["detail"] = (
                f"packets={result.get('packets')} "
                f"status={result.get('status')!r} "
                f"label={result.get('label')!r} "
                f"claimed={result.get('claimed')} "
                f"guard={result.get('guard')} "
                f"running={app.quarry_running}"
            )
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["detail"] = f"guard exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(200, wait_ready)
    app.run([])
    server.close()
    try:
        os.unlink(log.name)
    except OSError:
        pass
    print(
        "QUARRY SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
