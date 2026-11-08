"""Headless smoke test for **Block portals**.

Map 999 (a synthetic 6x6 grid) has one portal at (2,1), directly between the
character at (1,1) and a clicked goal at (4,1). The test checks that, with the
toggle on (the default):

* the portal is part of the blocked-cell set,
* a direct MOVE onto it is refused and never reaches the DLL,
* click-to-walk routes around it and still arrives,
* a drop/monster ON the portal is approached from a neighbouring cell,
* turning the toggle off allows the MOVE again.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading
import time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.engine import walk_checksum
from ntpl.gui import Application
from ntpl.mapdata import MapGrid

MAP_ID = 999


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
        self.position = (1, 1)
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "FakeServer":
        self.thread.start()
        return self

    def _run(self) -> None:
        try:
            self.connection, _ = self.listener.accept()
        except OSError:
            return

        self.connection.sendall(
            b"STATUS|ready\n"
            b"RECV|at 14881134 999 1 1 0 0 0 0 0 0 0\n"
            b"RECV|gp 2 1 104 -1 0 0\n"
            b"RECV|rest 1 14881134 0\n"
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
                            (
                                f"STATUS|POS {self.position[0]} "
                                f"{self.position[1]} 14881134\n"
                            ).encode()
                        )
                    except OSError:
                        return
                elif text.startswith("MOVE|"):
                    parts = text.split()
                    if len(parts) < 2:
                        continue
                    try:
                        x, y = int(parts[0][5:]), int(parts[1])
                    except ValueError:
                        continue
                    self.position = (x, y)
                    time.sleep(0.02)
                    try:
                        self.connection.sendall(
                            (
                                f"SEND|walk {x} {y} "
                                f"{walk_checksum(x, y)} 10\n"
                                f"STATUS|POS {x} {y} 14881134\n"
                            ).encode()
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
    server = FakeServer().start()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def setup() -> bool:
        try:
            app.map_grid = MapGrid(MAP_ID, 6, 6, bytes(36))
            app.map_source = "test"
            # With the toggle on (default): the portal is blocked, a drop on
            # it is approached next to it, and a MOVE onto it is refused.
            result["cells"] = sorted(app._portal_cells())
            result["approach"] = app._walkable_cell(2, 1)
            result["refused"] = app._client_move(2, 1) is False
            app.loop_toggle.set_active(False)
            app._start_travel((4, 1))
            GLib.timeout_add(2200, phase2)
            return False
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"setup exception: {exc!r}"
            app.quit()
            return False

    def phase2() -> bool:
        try:
            moves = [
                line
                for line in server.received
                if line.startswith("MOVE|")
            ]
            result["moves"] = moves
            result["avoided"] = "MOVE|2 1" not in moves
            result["arrived"] = (
                not app.travel_targets and app.travel_timer is None
            )
            # Turn the protection off: the same MOVE must now be sent, and
            # the DLL must be told to stop dropping preq.
            app.block_portal_toggle.set_active(False)
            result["off_cells"] = sorted(app._portal_cells())
            result["allowed"] = app._client_move(2, 1)
            GLib.timeout_add(400, phase3)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase2 exception: {exc!r}"
            app.quit()
        return False

    def phase3() -> bool:
        try:
            lines = list(server.received)
            result["ok"] = bool(
                result.get("cells") == [(2, 1)]
                and result.get("approach") not in (None, (2, 1))
                and result.get("refused") is True
                and result.get("moves")
                and result.get("avoided") is True
                and result.get("arrived") is True
                and result.get("off_cells") == []
                and result.get("allowed") is True
                and "MOVE|2 1" in lines
                and "PORTALS|1" in lines
                and "PORTALS|0" in lines
                and lines.index("PORTALS|1") < lines.index("PORTALS|0")
            )
            result["detail"] = (
                f"cells={result.get('cells')} "
                f"approach={result.get('approach')} "
                f"refused={result.get('refused')} "
                f"moves={result.get('moves')} "
                f"avoided={result.get('avoided')} "
                f"arrived={result.get('arrived')} "
                f"off_cells={result.get('off_cells')} "
                f"allowed_after_off={result.get('allowed')} "
                f"last={lines[-8:]}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase3 exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1500, setup)
    app.run([])
    server.close()

    print("PORTAL BLOCK SMOKE:", "PASS" if result["ok"] else "FAIL", result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
