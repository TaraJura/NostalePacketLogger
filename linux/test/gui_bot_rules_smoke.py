"""Headless smoke test for the target filter, spell rules and config save.

The fake server presents two monsters:

    501, vnum 201 (Rock Turtle), at (25,118) - closest
    500, vnum 200 (Mountain Turtle), at (26,119)

The filter allows only vnum 200, so the bot must ignore 501.  HP is 40 %, so
the spell rule (spell 7, my HP below 50 %) must send `u_s 7 3 500` instead of
the basic attack.  Finally the smoke saves the configuration, changes a
widget, loads it back and checks that the state was restored.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.botconfig import SpellRule
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
            b"RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
            b"RECV|stat 40 100 100 100 0 0\n"
            b"RECV|rest 1 14881134 0\n"
            b"RECV|in 3 201 501 25 118 2 100 100 0\n"
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
    server = FakeServer().start()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    )
    handle.close()
    temp_path = handle.name

    def start_bot() -> bool:
        try:
            app.path_cells = []
            app.auto_attack_toggle.set_active(True)
            app.retaliate_toggle.set_active(False)
            app.move_first_toggle.set_active(True)
            app.skill_entry.set_text("0")
            app.attack_cooldown.set_value(10)
            app.target_mode.set_selected(1)  # only selected
            app.target_vnums = {200}
            app._update_target_info()
            app._add_spell_rule(
                SpellRule(
                    enabled=True,
                    spell=7,
                    subject="my_hp",
                    comparator="below",
                    threshold=50,
                    cooldown=30,
                    target="monster",
                )
            )
            app._start_bot()
        except Exception as exc:  # noqa: BLE001 - report, do not crash GTK
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        GLib.timeout_add(2000, check)
        return False

    def check() -> bool:
        try:
            lines = list(server.received)
            has_target = any(line == "SEND|ncif 3 500" for line in lines)
            has_spell = any(line == "SEND|u_s 7 3 500" for line in lines)
            ignored_other = not any("501" in line for line in lines)
            no_basic = not any(line.startswith("SEND|u_s 0 ") for line in lines)

            condition_ok = (
                app._spell_condition_met(
                    SpellRule(
                        subject="my_mp", comparator="above", threshold=50
                    )
                )
                and not app._spell_condition_met(
                    SpellRule(
                        subject="my_hp", comparator="above", threshold=50
                    )
                )
                and app._spell_condition_met(
                    SpellRule(
                        subject="my_hp", comparator="below", threshold=50
                    )
                )
            )
            self_target_ok = (
                app._skill_packet(14881134, skill=20, target_type=1)
                == "u_s 20 1 14881134"
            )

            saved = app._save_config_to(temp_path)
            app.attack_range.set_value(5)
            app.target_vnums = set()
            app._clear_spell_rules()
            loaded = app._load_config_from(temp_path)
            config_ok = (
                saved
                and loaded
                and app.attack_range.get_value() == 10
                and app.target_mode.get_selected() == 1
                and app.target_vnums == {200}
                and len(app.spell_rows) == 1
                and app.spell_rows[0]["spell"].get_text() == "7"
            )

            result["ok"] = (
                has_target
                and has_spell
                and ignored_other
                and no_basic
                and condition_ok
                and self_target_ok
                and config_ok
            )
            result["detail"] = (
                f"target={has_target} spell={has_spell} "
                f"ignored_other={ignored_other} no_basic={no_basic} "
                f"conditions={condition_ok} self={self_target_ok} "
                f"config={config_ok} lines={lines[-10:]}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1500, start_bot)
    app.run([])
    server.close()
    try:
        os.unlink(temp_path)
    except OSError:
        pass

    print("BOT RULES SMOKE:", "PASS" if result["ok"] else "FAIL", result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
