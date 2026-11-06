"""Headless smoke test for the spell catalog and rule execution order.

Two active spells are added from the scanned Skill.dat:

    1. Holy Hammer   (876, 145 MP, 90 s cooldown)
    2. Holy Attack   (866,   0 MP, 0.9 s cooldown)

Both use the "Always" condition, so the bot runs them as a rotation in list
order: 876 must be the first cast, then 866 repeatedly while 876 is on its
spell cooldown. The fake server does not need to answer the casts.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading

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
        self.connection.sendall(
            b"STATUS|ready\n"
            b"RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
            b"RECV|stat 100 100 1000 1000 0 0\n"
            b"RECV|rest 1 14881134 0\n"
            b"RECV|ski 0 866 866 866 867 868 869 870 871 872 873 "
            b"874 875 876\n"
            b"RECV|in 3 200 500 26 119 2 100 100 0\n"
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
                            b"STATUS|POS 25 117 14881134\n"
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
    server = FakeServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    def start_bot() -> bool:
        try:
            app.path_cells = []
            app.auto_attack_toggle.set_active(True)
            app.retaliate_toggle.set_active(False)
            app.move_first_toggle.set_active(True)
            app.skill_entry.set_text("0")
            app.attack_cooldown.set_value(1)
            if app.spells.get(876) is None or app.spells.get(866) is None:
                result["detail"] = "spell catalog missing 876/866"
                app.quit()
                return False
            app._add_spell_from_info(app.spells[876])
            app._add_spell_from_info(app.spells[866])
            app._start_bot()
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
            return False
        GLib.timeout_add(2500, check)
        return False

    def start_when_spells_loaded() -> bool:
        if not app.spells_loaded:
            return True
        return start_bot()

    def check() -> bool:
        try:
            lines = list(server.received)
            # Skill.dat vnums are resolved to u_s slots: 876 -> 10,
            # 866 -> 0 (the basic-attack slot).
            hammer = [
                i for i, line in enumerate(lines)
                if line == "SEND|u_s 10 3 500"
            ]
            attack = [
                i for i, line in enumerate(lines)
                if line == "SEND|u_s 0 3 500"
            ]
            ordered = bool(hammer and attack and hammer[0] < attack[0])
            repeated = len(attack) >= 2
            no_raw_vnum = not any(
                line in ("SEND|u_s 876 3 500", "SEND|u_s 866 3 500")
                for line in lines
            )
            no_mp_shortage = not any(
                "not enough MP" in line for line in lines
            )
            result["ok"] = (
                ordered and repeated and no_raw_vnum and no_mp_shortage
            )
            result["detail"] = (
                f"hammer={len(hammer)} attack={len(attack)} "
                f"ordered={ordered} repeated={repeated} "
                f"no_raw_vnum={no_raw_vnum} lines={lines[-12:]}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1000, start_when_spells_loaded)
    app.run([])
    server.close()

    print(
        "SPELL ORDER SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
