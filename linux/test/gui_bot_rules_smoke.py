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

from gi.repository import GLib, Gtk

from ntpl.botconfig import SpellRule
from ntpl.engine import Entity
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
            b"RECV|ski 0 866 866 866 867 868 869 870 871 872 873 "
            b"874 875 876\n"
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
                if text.startswith("TARGET|"):
                    entity_id = text.split("|", 1)[1]
                    try:
                        self.connection.sendall(
                            (
                                f"STATUS|TARGET {entity_id} "
                                "0x08A00000 0x087B4DD8\n"
                            ).encode()
                        )
                    except OSError:
                        return
                elif text == "POS":
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
                    spell=867,
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
            has_target = any(
                line == "TARGET|500" or line == "SEND|ncif 3 500"
                for line in lines
            )
            has_spell = any(line == "SEND|u_s 1 3 500" for line in lines)
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
            always_ok = app._spell_condition_met(
                SpellRule(subject="always")
            )
            slot_ok = (
                app._skill_send_id(867) == 1
                and app._skill_send_id(876) == 10
            )
            catalog_ok = (
                app.spells.get(866) is not None
                and app.spells[866].name == "Holy Attack"
                and app.spells.get(876) is not None
                and app.spells[876].mp_cost == 145
            )
            self_spell_ok = (
                app._spell_target(
                    SpellRule(spell=869, target="monster")
                )
                == "self"
            )
            heal_target_ok = (
                not app._prefers_self_target(app.spells[867])
                and app._prefers_self_target(app.spells[869])
            )
            app._add_spell_from_info(app.spells[876])
            added = app.spell_rows[-1]
            picker_ok = (
                added["spell"].get_text() == "876"
                and added["cooldown"].get_value() >= 90
                and added["subject"].get_selected() == 0  # Always
                and app._spell_target(
                    SpellRule(spell=876, target="monster")
                )
                == "monster"
            )
            app._remove_spell_rule(added)

            unknown = Entity(9999, 3)  # vnum 0: spawned before injection
            app.target_mode.set_selected(1)  # only selected
            app.target_vnums = {200}
            app.attack_unknown_toggle.set_active(False)
            unknown_off = not app._monster_allowed(unknown)
            app.attack_unknown_toggle.set_active(True)
            unknown_on = app._monster_allowed(unknown)
            unknown_ok = unknown_off and unknown_on

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
                and app.spell_rows[0]["spell"].get_text() == "867"
            )

            # Scanned spell metadata is enforced during selection.
            app._clear_spell_rules()
            app._add_spell_from_info(app.spells[876])
            stats = app.engine.world.stats
            stats.mp, stats.mp_max = 100, 1000
            app.spell_last_cast.clear()
            now = time.monotonic()
            mp_blocked = app._select_spell_rule(
                now, Entity(500, 3, x=26, y=119), 14881134
            ) is None
            stats.mp = 1000
            near = app._select_spell_rule(
                now, Entity(500, 3, x=26, y=119), 14881134
            )
            far = app._select_spell_rule(
                now, Entity(500, 3, x=45, y=119), 14881134
            )
            spell_rules_ok = (
                mp_blocked
                and near is not None
                and near[1].spell == 876
                and far is None
            )

            def find_listbox(widget):
                if isinstance(widget, Gtk.ListBox):
                    return widget
                child = widget.get_first_child()
                while child is not None:
                    found = find_listbox(child)
                    if found is not None:
                        return found
                    child = child.get_next_sibling()
                return None

            app._ensure_spells_loaded()
            app._open_spell_picker(lambda spell: None)
            picker = app.spell_picker_window
            picker_dialog_ok = False
            if picker is not None:
                listbox = find_listbox(picker)
                rows = 0
                if listbox is not None:
                    child = listbox.get_first_child()
                    while child is not None:
                        rows += 1
                        child = child.get_next_sibling()
                picker_dialog_ok = rows >= 1
                picker.close()

            result["ok"] = (
                has_target
                and has_spell
                and ignored_other
                and no_basic
                and condition_ok
                and self_target_ok
                and always_ok
                and slot_ok
                and catalog_ok
                and self_spell_ok
                and heal_target_ok
                and picker_ok
                and unknown_ok
                and config_ok
                and spell_rules_ok
                and picker_dialog_ok
            )
            result["detail"] = (
                f"target={has_target} spell={has_spell} "
                f"ignored_other={ignored_other} no_basic={no_basic} "
                f"conditions={condition_ok} self={self_target_ok} "
                f"always={always_ok} slots={slot_ok} "
                f"catalog={catalog_ok} "
                f"self_spell={self_spell_ok} heal_target={heal_target_ok} "
                f"picker={picker_ok} "
                f"unknown={unknown_ok} config={config_ok} "
                f"spell_rules={spell_rules_ok} "
                f"picker_dialog={picker_dialog_ok} lines={lines[-10:]}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    def start_when_ready() -> bool:
        if not app.spells_loaded:
            return True
        return start_bot()

    GLib.timeout_add(1500, start_when_ready)
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
