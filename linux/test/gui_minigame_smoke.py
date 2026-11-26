"""Headless smoke test for the Minigame tab.

The fake map has two players, one pet/NPC and one monster all within a few
tiles of the character. Start must spam KEY|y, focus only the nearest player
(never the NPC or monster), switch when that player leaves the radius, and
stop cleanly. An old DLL's "unknown command: KEY" must stop the loop instead
of hammering it.

Run through scripts/dev-test-gui.sh (Xvfb).
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time

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
                b"RECV|in 1 Alice - 5001 26 117 2 0 1 0 9 2 "
                b"-1.0.0.-1.-1.885.-1.-1.-1.-1 "
                b"100 100 0 -1 0 2 0 0 0 1000009 75 75 -1 - -2 0 0 0 0 81 "
                b"0 0|0|0 0 0 100 0 0\n"
                b"RECV|in 1 Bob - 5002 29 117 2 0 1 0 9 2 "
                b"-1.0.0.-1.-1.885.-1.-1.-1.-1 "
                b"100 100 0 -1 0 2 0 0 0 1000009 75 75 -1 - -2 0 0 0 0 81 "
                b"0 0|0|0 0 0 100 0 0\n"
                b"RECV|in 2 2119 6001 25 118 2 100 100 0 0 0 -1 0 0 -1 - "
                b"0 -1 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
                b"RECV|in 3 9 7001 25 116 2 100 100 0 0 0 -1 0 0 -1 - "
                b"0 -1 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
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
                if text == "POS":
                    try:
                        self.connection.sendall(
                            b"STATUS|POS 25 117 14881134 0\n"
                        )
                    except OSError:
                        return
                elif text in ("TARGET|5001", "TARGET|5002"):
                    # The client's own selector answers a TARGET with its own
                    # ncif; the engine records it as world.target_id.
                    entity_id = text.split("|", 1)[1]
                    try:
                        self.connection.sendall(
                            f"SEND|ncif 1 {entity_id}\n".encode()
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


def keys(lines: list[str]) -> list[str]:
    return [line for line in lines if line == "KEY|y"]


def targets(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("TARGET|")]


def main() -> int:
    log = tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="latin1"
    )
    log.close()
    config_path = os.environ.get("NTPL_MINIGAME_CONFIG", "")
    server = FakeServer()
    app = Application(
        "127.0.0.1", server.port, unique=False, log_file=log.name
    )
    result: dict[str, object] = {"ok": False, "detail": "not checked"}
    keys_at_stop = 0
    deadline = time.time() + 6.0

    def phase1() -> bool:
        try:
            if "POS" not in server.received:
                if time.time() < deadline:
                    GLib.timeout_add(100, phase1)
                else:
                    result["detail"] = "GUI never attached"
                    app.quit()
                return False
            # No client picker in this smoke: attach the per-client config and
            # player world manually, as _attach_client would.
            app._minigame_reset_for_attach(1234)
            app.minigame_interval_spin.set_value(50)
            app.minigame_key_entry.set_text("y")
            app.minigame_focus_switch.set_active(True)
            app.minigame_radius_spin.set_value(15)
            app.minigame_toggle.set_active(True)
            GLib.timeout_add(700, phase2)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase1 exception: {exc!r}"
            app.quit()
        return False

    def phase2() -> bool:
        try:
            result["keys"] = len(keys(server.received)) >= 3
            result["nearest_player"] = "TARGET|5001" in targets(
                server.received
            )
            result["npc_not_targeted"] = not any(
                line in ("TARGET|6001", "SEND|ncif 1 6001")
                for line in server.received
            )
            result["monster_not_targeted"] = not any(
                line in ("TARGET|7001", "SEND|ncif 1 7001")
                for line in server.received
            )
            result["other_player_not_first"] = "TARGET|5002" not in targets(
                server.received
            )
            result["focus_state"] = app.minigame_focus_id == 5001
            status = app.minigame_status.get_text()
            result["status_focus"] = "focusing Alice" in status
            result["status_players"] = "2 players in range" in status
            result["running"] = app.minigame_running
            try:
                data = json.loads(open(config_path, encoding="utf-8").read())
            except (OSError, ValueError):
                data = None
            result["saved"] = (
                isinstance(data, dict)
                and data.get("key") == "y"
                and data.get("interval_ms") == 50
                and data.get("focus_players") is True
                and data.get("radius") == 15
            )
            server.send("RECV|mv 1 5001 25 140 5\n")
            server.send("RECV|mv 1 5002 27 117 5\n")
            GLib.timeout_add(900, phase3)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase2 exception: {exc!r}"
            app.quit()
        return False

    def phase3() -> bool:
        try:
            result["switched"] = "TARGET|5002" in targets(server.received)
            result["switch_state"] = app.minigame_focus_id == 5002
            result["switched_status"] = "focusing Bob" in (
                app.minigame_status.get_text()
            )
            app.minigame_toggle.set_active(False)
            result["stopped"] = (
                not app.minigame_running
                and app.minigame_timer is None
                and app.minigame_toggle.get_label() == "Start"
            )
            nonlocal keys_at_stop
            keys_at_stop = len(keys(server.received))
            GLib.timeout_add(400, phase4)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase3 exception: {exc!r}"
            app.quit()
        return False

    def phase4() -> bool:
        try:
            # Give in-flight lines time to arrive, then prove nothing new is
            # sent after the stop.
            nonlocal keys_at_stop
            keys_at_stop = len(keys(server.received))
            GLib.timeout_add(400, phase5)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase4 exception: {exc!r}"
            app.quit()
        return False

    def phase5() -> bool:
        try:
            result["no_keys_after_stop"] = (
                len(keys(server.received)) == keys_at_stop
            )
            # The old-DLL path must stop the loop on the first refusal.
            app.dll_key_supported = True
            app.minigame_toggle.set_active(True)
            server.send("STATUS|unknown command: KEY|y\n")
            GLib.timeout_add(400, phase6)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase5 exception: {exc!r}"
            app.quit()
        return False

    def phase6() -> bool:
        try:
            result["old_dll_stops"] = (
                not app.minigame_running
                and app.dll_key_supported is False
            )
            result["old_dll_status"] = "no KEY command" in (
                app.minigame_status.get_text()
            )
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["detail"] = f"phase6 exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(150, phase1)
    app.run([])
    server.close()
    try:
        os.unlink(log.name)
    except OSError:
        pass

    result["ok"] = all(
        result.get(key)
        for key in (
            "keys",
            "nearest_player",
            "npc_not_targeted",
            "monster_not_targeted",
            "other_player_not_first",
            "focus_state",
            "status_focus",
            "status_players",
            "running",
            "saved",
            "switched",
            "switch_state",
            "switched_status",
            "stopped",
            "no_keys_after_stop",
            "old_dll_stops",
            "old_dll_status",
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
