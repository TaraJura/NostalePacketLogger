"""Headless smoke test: loot right away, also while being hit.

A monster keeps hitting the character (`su ... 1 <charId>` every 0.4 s).
Phase 1: HP is full and a free drop (owner -1) lies 4 cells away. The bot must
walk to it and pick it up during the fight instead of leaving it for later.
Phase 2: HP drops below the Recovery HP threshold and a second free drop
appears 3 cells away. Now the bot must defend first: no walk, no `get`.
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

HIT = "RECV|su 3 999 1 14881134 10 9 11 4401\n"


class FightLootServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.x, self.y = 25, 117
        self.connection = None
        self.lock = threading.Lock()
        self.stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def send(self, text: str) -> None:
        with self.lock:
            if self.connection is None:
                return
            try:
                self.connection.sendall(text.encode())
            except OSError:
                pass

    def _hits(self) -> None:
        while not self.stop.is_set():
            self.send(HIT)
            time.sleep(0.4)

    def _run(self) -> None:
        try:
            connection, _ = self.listener.accept()
        except OSError:
            return
        with self.lock:
            self.connection = connection
        self.send(
            "STATUS|ready\n"
            "RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
            "RECV|stat 100 100 100 100 0 0\n"
            # Free drop (live marker -1) 4 cells away.
            "RECV|drop 1303 999 29 117 1 1 -1\n"
        )
        threading.Thread(target=self._hits, daemon=True).start()
        buffer = b""
        while not self.stop.is_set():
            try:
                chunk = connection.recv(4096)
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
                    self.send(f"STATUS|POS {self.x} {self.y} 14881134 0\n")
                elif text.startswith("MOVE|"):
                    parts = text.split()
                    if len(parts) >= 2:
                        try:
                            self.x = int(parts[0][5:])
                            self.y = int(parts[1])
                        except ValueError:
                            continue
                        self.send(f"SEND|walk {self.x} {self.y} 0 10\n")
                elif text.startswith("SEND|get 1 "):
                    parts = text.split()
                    if len(parts) >= 4:
                        self.send(f"RECV|get 1 14881134 {parts[3]} 0\n")

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
    server = FightLootServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}
    phase2 = {"start": 0}

    def start_bot() -> bool:
        try:
            app.path_cells = []
            app.loop_toggle.set_active(False)
            app.auto_attack_toggle.set_active(False)
            app.retaliate_toggle.set_active(False)
            app.pickup_toggle.set_active(True)
            app.recover_toggle.set_active(False)
            app.recover_mp_toggle.set_active(False)
            app.low_hp_pct.set_value(70)
            app._start_bot()
            GLib.timeout_add(5000, start_phase2)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def start_phase2() -> bool:
        phase1_get = any(
            line == "SEND|get 1 14881134 999" for line in server.received
        )
        phase1_move = any(line.startswith("MOVE|") for line in server.received)
        result["phase1"] = (
            f"phase1 under_attack={app._under_attack()} get={phase1_get} "
            f"move={phase1_move} drop_gone={999 not in app.engine.world.drops}"
        )
        result["phase1_ok"] = (
            phase1_get and phase1_move and 999 not in app.engine.world.drops
        )
        phase2["start"] = len(server.received)
        # 30 % HP is below the 70 % threshold; a new free drop 3 cells away.
        server.send(
            "RECV|stat 30 100 100 100 0 0\n"
            f"RECV|drop 1303 997 {server.x} {server.y + 3} 1 1 -1\n"
        )
        GLib.timeout_add(3000, check)
        return False

    def check() -> bool:
        try:
            later = server.received[phase2["start"]:]
            phase2_get = any(
                line == "SEND|get 1 14881134 997" for line in later
            )
            phase2_move = any(line.startswith("MOVE|") for line in later)
            kept = 997 in app.engine.world.drops
            phase2_ok = not phase2_get and not phase2_move and kept
            result["ok"] = bool(result.get("phase1_ok")) and phase2_ok
            result["detail"] = (
                f"{result.get('phase1')} | phase2 get={phase2_get} "
                f"move={phase2_move} kept={kept} "
                f"status={app.status.get_text()!r}"
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
    server.close()
    print("LOOT FIGHT SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
