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
    "18:10:35.118 [RECV] mlo_info 0 3125 4 2000 0 0 0 999 1000 4999 "
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
                if text.startswith("SEND|mg 1 "):
                    # The live server answers GAMESTART with mlo_st.
                    try:
                        self.connection.sendall(b"RECV|mlo_st 0\n")
                    except OSError:
                        return
                elif text.startswith("SEND|mg 3 "):
                    # The live server answers the score with the 0-based
                    # reward level (4 = the 5x box).
                    try:
                        self.connection.sendall(b"RECV|mlo_lv 4\n")
                    except OSError:
                        return
                elif text.startswith("SEND|mg 4 "):
                    # The claim charges 100 points and grants item 2193 x2.
                    try:
                        self.connection.sendall(
                            b"RECV|mlpt 1900 100\n"
                            b"RECV|mlo_rw 2193 2\n"
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
                "SEND|mg 1 4 3125",
                "SEND|mg 3 4 3125 16000 16000",
                "SEND|mg 4 4 3125 4",
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
                and "reward item 2193 ×2" in status
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
            # The game refuses the minigame inside a raid team. Feed the live
            # rdlst shape with the local player and check both the refusal and
            # the hint in the points label.
            app.engine.world.minigame.points = 1900
            server.send(
                "RECV|rdlst 85 99 17 89.14.3.0.Tester.0.14881134.30\n"
            )
            GLib.timeout_add(500, check_raid)
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["detail"] = f"guard exception: {exc!r}"
            app.quit()
        return False

    def check_raid() -> bool:
        try:
            before = len(sent_mg())
            app._quarry_play()
            app._quarry_points_tick()
            status = app.quarry_status.get_text()
            label = app.quarry_points_label.get_text()
            result["raid_guard"] = (
                len(sent_mg()) == before
                and app.quarry_running is False
                and "raid team" in status
                and "raid team" in label
            )
            result["ok"] = bool(
                result.get("claimed")
                and result.get("guard")
                and result["raid_guard"]
            )
            result["detail"] = (
                f"packets={result.get('packets')} "
                f"status={result.get('status')!r} "
                f"label={result.get('label')!r} "
                f"claimed={result.get('claimed')} "
                f"guard={result.get('guard')} "
                f"raid_guard={result.get('raid_guard')} "
                f"raid_label={label!r} "
                f"running={app.quarry_running}"
            )
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["detail"] = f"raid exception: {exc!r}"
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
