"""Headless smoke test: mirror a spell when the follower has no `ski` list.

A GUI that attaches after login never sees the client's ordered skill list,
so it cannot map an SP spell vnum to its own `u_s` slot. With the same SP card
the followed client's slot is valid, so mirror mode must fall back to it —
including a bus event whose vnum was not resolved (`skill = 0`).

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

MIRROR_TMP = tempfile.TemporaryDirectory()
os.environ["NTPL_MIRROR_DIR"] = MIRROR_TMP.name

from ntpl.gui import Application
from ntpl.mirror import MirrorWriter

PLAYER_NAME = "Mirror Friend"
PLAYER_ID = 777777
SELF_ID = 14881134
MONSTER_ID = 500


class SlotServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.x, self.y = 25, 117
        self.connection = None
        self.stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.connection, _ = self.listener.accept()
        except OSError:
            return
        self.connection.sendall(
            b"STATUS|ready\n"
            b"RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
            b"RECV|stat 10000 10000 10000 10000 0 0\n"
            # Deliberately no `ski`: the follower cannot map vnums to slots.
            + f"RECV|in 1 {PLAYER_NAME} - {PLAYER_ID} 26 117 0 0 0 0\n".encode()
            + b"RECV|in 3 200 500 27 117 2 100 100 0 0 0 -1 0 0 -1 - "
              b"0 -1 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
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
                    try:
                        self.connection.sendall(
                            (f"STATUS|POS {self.x} {self.y} {SELF_ID} 0\n")
                            .encode()
                        )
                    except OSError:
                        return
                elif text.startswith("MOVE|"):
                    parts = text.split()
                    if len(parts) >= 2:
                        try:
                            self.x = int(parts[0][5:])
                            self.y = int(parts[1])
                        except ValueError:
                            continue
                        try:
                            self.connection.sendall(
                                f"SEND|walk {self.x} {self.y} 0 10\n".encode()
                            )
                        except OSError:
                            return
                elif text.startswith("TARGET|"):
                    entity_id = text.split("|", 1)[1]
                    try:
                        self.connection.sendall(
                            f"STATUS|TARGET {entity_id} OK 1 2\n".encode()
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
    server = SlotServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}
    leader_bus = MirrorWriter(
        path=Path(MIRROR_TMP.name) / "leader.jsonl"
    )

    def start_bot() -> bool:
        try:
            app.path_cells = []
            app.loop_toggle.set_active(False)
            app.auto_attack_toggle.set_active(False)
            app.retaliate_toggle.set_active(False)
            app.pickup_toggle.set_active(False)
            app.recover_toggle.set_active(False)
            app.recover_mp_toggle.set_active(False)
            app.follow_player_entry.set_text(PLAYER_NAME)
            app.follow_mirror_check.set_active(True)
            app.follow_player_toggle.set_active(True)
            app._start_bot()
            leader_bus.spell(
                skill=835,
                slot=2,
                target_type=3,
                target_id=MONSTER_ID,
                char_id=PLAYER_ID,
                name=PLAYER_NAME,
                map_id=20,
            )
            GLib.timeout_add(1800, second_spell)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def second_spell() -> bool:
        # The leader's client could not resolve this slot: the follower must
        # still cast it from the published slot alone.
        leader_bus.spell(
            skill=0,
            slot=3,
            target_type=3,
            target_id=MONSTER_ID,
            char_id=PLAYER_ID,
            name=PLAYER_NAME,
            map_id=20,
        )
        GLib.timeout_add(2500, check)
        return False

    def check() -> bool:
        try:
            att = [
                line for line in server.received
                if line.startswith("SEND|u_s ")
            ]
            result["ok"] = (
                "SEND|u_s 2 3 500" in att
                and "SEND|u_s 3 3 500" in att
            )
            result["detail"] = (
                f"attacks={att} status={app.status.get_text()!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    def start_when_map_loaded() -> bool:
        if app.map_grid is None:
            return True
        return start_bot()

    GLib.timeout_add(1200, start_when_map_loaded)
    app.run([])
    leader_bus.close()
    server.close()
    MIRROR_TMP.cleanup()
    print("FOLLOW-MIRROR-SLOT SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
