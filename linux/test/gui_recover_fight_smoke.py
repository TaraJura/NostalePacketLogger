"""Headless recovery-vs-combat smoke test.

The bot attacks a monster; after the first hit HP and MP drop below their
thresholds. It must finish the half-killed monster first, then sit and heal
to full. This is the "never abandon a fight" rule.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application


class FightRecoveryServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.attacks = 0
        self.rests = 0
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
            b"RECV|ski 0 866 866 866 867 868 869 870 871 872 873 874 875 876\n"
            b"RECV|in 3 204 500 25 118 2 100 100 0\n"
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
                if text.startswith("SEND|u_s "):
                    self.attacks += 1
                    if self.attacks == 1:
                        # Both bars fall below the 50 % thresholds mid-fight.
                        try:
                            self.connection.sendall(
                                b"RECV|stat 20 100 20 100 0 0\n"
                            )
                        except OSError:
                            return
                    elif self.attacks == 2:
                        # Finish the kill only after the second hit.
                        try:
                            self.connection.sendall(
                                b"RECV|st 3 500 15 0 1 100 0 119 513 119 0\n"
                            )
                        except OSError:
                            return
                elif text == "SEND|rest 1 1 14881134":
                    self.rests += 1
                    if self.rests == 1:
                        try:
                            self.connection.sendall(
                                b"RECV|rest 1 14881134 1\n"
                            )
                        except OSError:
                            return
                        threading.Timer(0.3, self._heal).start()
                    else:
                        try:
                            self.connection.sendall(
                                b"RECV|rest 1 14881134 0\n"
                            )
                        except OSError:
                            return

    def _heal(self) -> None:
        if self.connection is None:
            return
        try:
            self.connection.sendall(b"RECV|stat 100 100 100 100 0 0\n")
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
    server = FightRecoveryServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def start_bot() -> bool:
        try:
            app.path_cells = []
            app.loop_toggle.set_active(False)
            app.auto_attack_toggle.set_active(True)
            app.pickup_toggle.set_active(False)
            app.recover_toggle.set_active(True)
            app.low_hp_pct.set_value(50)
            app.recover_mp_toggle.set_active(True)
            app.low_mp_pct.set_value(50)
            app._start_bot()
            GLib.timeout_add(6000, check)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            lines = list(server.received)
            first_attack = next(
                (i for i, line in enumerate(lines)
                 if line.startswith("SEND|u_s ")),
                None,
            )
            first_rest = next(
                (i for i, line in enumerate(lines)
                 if line == "SEND|rest 1 1 14881134"),
                None,
            )
            stats = app.engine.world.stats
            finished_first = (
                first_attack is not None
                and first_rest is not None
                and first_attack < first_rest
                and server.attacks >= 2
            )
            result["ok"] = (
                finished_first
                and server.rests >= 2
                and stats.hp == 100
                and stats.mp == 100
                and not app.recovering
            )
            result["detail"] = (
                f"attacks={server.attacks} rests={server.rests} "
                f"finished_first={finished_first} "
                f"hp={stats.hp}/{stats.hp_max} mp={stats.mp}/{stats.mp_max} "
                f"recovering={app.recovering} "
                f"status={app.status.get_text()!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    def start_when_spells_loaded() -> bool:
        if not app.spells_loaded:
            return True
        return start_bot()

    GLib.timeout_add(1200, start_when_spells_loaded)
    app.run([])
    server.close()
    print("RECOVER FIGHT SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
