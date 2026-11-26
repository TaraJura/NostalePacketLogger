"""Headless smoke test for "Mirror movement & spells" in Follow mode.

The followed character is driven by another simulated client: its own host
publishes its `walk` and `u_s` to the shared mirror bus.  The follower must
replay the same destination and the same spells, retargeting a self cast from
the followed character's id to its own.  After the bus goes quiet, the
server's broadcast `mv`/`su` packets must still drive the mirror (fallback).

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

# Isolate the bus before Application/MirrorReader read the directory.
MIRROR_TMP = tempfile.TemporaryDirectory()
os.environ["NTPL_MIRROR_DIR"] = MIRROR_TMP.name

from ntpl.gui import Application
from ntpl.mirror import MirrorWriter

PLAYER_NAME = "Mirror Friend"
PLAYER_ID = 777777
SELF_ID = 14881134
MONSTER_ID = 500
LEADER_MAP = 20


class MirrorServer:
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
            b"RECV|stat 100 100 100 100 0 0\n"
            b"RECV|ski 0 5 10\n"
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

    def broadcast(self, line: str) -> None:
        if self.connection is not None:
            try:
                self.connection.sendall((line + "\n").encode())
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
    server = MirrorServer()
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
            # The followed client's publisher: one attack, one walk, one
            # self-buff. The buff carries the followed id and must be
            # retargeted to our own character.
            leader_bus.spell(
                skill=10,
                slot=1,
                target_type=3,
                target_id=MONSTER_ID,
                char_id=PLAYER_ID,
                name=PLAYER_NAME,
                map_id=LEADER_MAP,
            )
            leader_bus.move(
                28,
                117,
                speed=10,
                char_id=PLAYER_ID,
                name=PLAYER_NAME,
                map_id=LEADER_MAP,
            )
            leader_bus.spell(
                skill=5,
                slot=0,
                target_type=1,
                target_id=PLAYER_ID,
                char_id=PLAYER_ID,
                name=PLAYER_NAME,
                map_id=LEADER_MAP,
            )
            GLib.timeout_add(6000, fallback_check)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def fallback_check() -> bool:
        """Bus actions must have happened; now test the server fallback."""
        try:
            lines = list(server.received)
            bus_attacks = [
                line for line in lines
                if line in ("SEND|u_s 1 3 500", "SEND|u_s 0 1 14881134")
            ]
            bus_moves = [line for line in lines if line == "MOVE|28 117"]
            if len(bus_attacks) < 2 or not bus_moves:
                result["detail"] = (
                    f"bus not mirrored: attacks={bus_attacks} "
                    f"moves={[l for l in lines if l.startswith('MOVE|')]} "
                    f"status={app.status.get_text()!r}"
                )
                app.quit()
                return False
            # Bus has been quiet for > MIRROR_BUS_GRACE_S: the server's own
            # mv/su broadcasts must drive the same behavior.
            server.broadcast(f"RECV|mv 1 {PLAYER_ID} 30 117 10")
            server.broadcast(
                f"RECV|su 1 {PLAYER_ID} 3 {MONSTER_ID} 10 9 11 4401"
            )
            GLib.timeout_add(3500, final_check)
            return False
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"fallback check exception: {exc!r}"
            app.quit()
            return False

    def final_check() -> bool:
        try:
            lines = list(server.received)
            attacks = [line for line in lines if line.startswith("SEND|u_s ")]
            moves = [line for line in lines if line.startswith("MOVE|")]
            fallback_move = [line for line in lines if line == "MOVE|30 117"]
            fallback_attack = [line for line in attacks if line == "SEND|u_s 1 3 500"]
            result["ok"] = (
                "SEND|u_s 1 3 500" in attacks
                and "SEND|u_s 0 1 14881134" in attacks
                and any(line == "MOVE|28 117" for line in moves)
                and bool(fallback_move)
                and len(fallback_attack) >= 2
                and app.follow_player_toggle.get_active()
                and app.follow_mirror_check.get_active()
            )
            result["detail"] = (
                f"attacks={attacks} moves={moves} "
                f"fallback_move={fallback_move} "
                f"status={app.status.get_text()!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["detail"] = f"final check exception: {exc!r}"
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
    print("FOLLOW-MIRROR SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
