"""Headless smoke test: the NosBazar Monitor item set.

The Monitor section has its own list of configured items: ticking a row
enables its searches, bulk actions select/switch rows, and items can be
added from the Items selection, the Inventory scan, the whole inventory or
the full Item.dat database.  The smoke drives every path against a fake
server and checks the saved config.

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

from ntpl.bazar import BazarItem, build_search_packet, load_config
from ntpl.gui import Application
from ntpl.nosmall import ItemInfo

SELF_ID = 14881134


class MonitorServer:
    """Serves the map spawns and the inventory the GUI needs to attach."""

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
                b"STATUS|ready\n"
                b"RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
                b"RECV|in 2 793 9584 25 117 3 100 100 460 0 0 -1 1 0 -1 - 0\n"
                b"RECV|in 1 root1 - 14881134 2 2 2 0 0 0 8 3\n"
                b"RECV|inv 1 0.2282.11 1.1362.10\n"
                b"RECV|gold 100000 0\n"
            )
        except OSError:
            return
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
                self.received.append(line.decode("utf-8", "replace"))

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
    # A fresh config: dev-test-bot.sh shares one temp file across smokes.
    handle = tempfile.NamedTemporaryFile(
        prefix="ntpl-bazar-", suffix=".json", delete=False
    )
    handle.write(b'{"version": 1, "items": []}\n')
    handle.close()
    config_path = handle.name
    os.environ["NTPL_BAZAR_CONFIG"] = config_path
    cache_dir = tempfile.mkdtemp(prefix="ntpl-inventory-")
    os.environ["NTPL_INVENTORY_DIR"] = cache_dir
    server = MonitorServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result: dict[str, object] = {"ok": False, "detail": "not checked"}
    state = {"budget": 100}

    def rows() -> dict[str, object]:
        found: dict[str, object] = {}
        listbox = app.bazar_monitor_items_list
        child = listbox.get_first_child() if listbox is not None else None
        while child is not None:
            name = getattr(child, "item_name", None)
            if name:
                found[name] = child
            child = child.get_next_sibling()
        return found

    def find_item(vnum: int):
        for item in app.bazar.config.items:
            if item.vnum == vnum or vnum in item.vnums:
                return item
        return None

    def wait_attach() -> bool:
        if not app._inventory_slots():
            state["budget"] -= 1
            if state["budget"] <= 0:
                result["detail"] = "GUI never attached or got an inventory"
                app.quit()
                return False
            return True
        GLib.timeout_add(300, begin)
        return False

    def begin() -> bool:
        try:
            if app.bazar_monitor_items_list is None:
                result["detail"] = "the Monitor items panel was not built"
                app.quit()
                return False
            app.item_info = {
                2174: ItemInfo(2174, "Speech Bubble", 3000, 0),
                1362: ItemInfo(1362, "Soulstone Blessing", 100, 0),
                2282: ItemInfo(2282, "Angel's Feather", 100, 0),
                905: ItemInfo(905, "Red Magician Specialist Card", 0, 0),
            }
            app.item_info_loaded = True
            bubble = BazarItem(
                "bubbl",
                build_search_packet([2174]),
                vnum=2174,
                inv_tab=1,
                amount=5,
                min_price=0,
                nos_cost=0.5,
                enabled=True,
                relist=False,
            )
            stone = BazarItem(
                "stone",
                build_search_packet([1362]),
                vnum=1362,
                inv_tab=1,
                amount=10,
                min_price=5,
                enabled=False,
                relist=True,
            )
            app._bazar_set_items([bubble, stone])
            GLib.timeout_add(200, check_panel)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"setup exception: {exc!r}"
            app.quit()
        return False

    def check_panel() -> bool:
        try:
            shown = rows()
            ticked = {
                name: bool(getattr(row, "monitor_check").get_active())
                for name, row in shown.items()
            }
            info = app.bazar_monitor_items_info.get_text()
            panel_ok = (
                set(shown) == {"bubbl", "stone"}
                and ticked == {"bubbl": True, "stone": False}
                and info == "2 shown · 1 monitored · 2 configured"
            )
            result["panel"] = panel_ok

            # Ticking a row enables its searches; unticking disables it but
            # keeps the configuration and its floor.
            getattr(shown["stone"], "monitor_check").set_active(True)
            getattr(shown["bubbl"], "monitor_check").set_active(False)
            items = {item.name: item for item in app.bazar.config.items}
            toggles_ok = (
                items["stone"].enabled is True
                and items["bubbl"].enabled is False
                and items["bubbl"].min_price == 0
                and app.bazar_monitor_items_info.get_text()
                == "2 shown · 1 monitored · 2 configured"
            )
            result["toggles"] = toggles_ok

            # An unticked item has no live price: the Monitor views only
            # show enabled items.
            app._bazar_monitor_set_many(["bubbl", "stone"], False)
            app._bazar_render_results()
            empty_ok = (
                app.bazar_results_infos["nosmall"].get_text() == "No items"
            )
            app._bazar_monitor_set_all_shown(True)
            result["hidden"] = empty_ok

            # Only monitored filters the list; Select all feeds the selection
            # group actions.
            app._bazar_monitor_set_many(["bubbl"], False)
            app.bazar_monitor_filter.set_active(True)
            filtered = rows()
            filtered_info = app.bazar_monitor_items_info.get_text()
            app.bazar_monitor_filter.set_active(False)
            app._bazar_monitor_select_shown()
            selected = set(app._bazar_monitor_selected)
            app._bazar_monitor_set_selected(False)
            disabled = [
                item.enabled for item in app.bazar.config.items
            ]
            app._bazar_monitor_unselect_all()
            cleared = not app._bazar_monitor_selected
            app._bazar_monitor_set_all_shown(True)
            selection_ok = (
                set(filtered) == {"stone"}
                and filtered_info == "1 shown · 1 monitored · 2 configured"
                and selected == {"bubbl", "stone"}
                and disabled == [False, False]
                and cleared
            )
            result["selection"] = selection_ok

            # Add from Items re-enables the selected configured row.
            app._bazar_monitor_set_many(["bubbl"], False)
            app.bazar_selected_name = "bubbl"
            app._bazar_monitor_add_from_items()
            from_items_ok = find_item(2174).enabled is True
            result["from_items"] = from_items_ok

            # Add from Inventory enables a configured row and creates a new
            # row with the slot's tab and stack.
            app._bazar_monitor_set_many(["stone"], False)
            app._inventory_checks.add((1, 0))
            app._inventory_checks.add((1, 1))
            app._bazar_monitor_add_from_inventory()
            app._inventory_checks.clear()
            angel = find_item(2282)
            from_inv_ok = (
                find_item(1362).enabled is True
                and angel is not None
                and angel.enabled is True
                and angel.relist is False
                and angel.inv_tab == 1
                and angel.amount == 11
                and angel.name == "Angel's Feather"
            )
            result["from_inventory"] = from_inv_ok
            GLib.timeout_add(200, check_database)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"panel exception: {exc!r}"
            app.quit()
        return False

    def check_database() -> bool:
        try:
            app._open_item_database_picker("relist")
            window = app.item_db_window
            if window is None:
                result["detail"] = "the item database picker did not open"
                app.quit()
                return False
            app._item_db_render("magician")
            vnums = [vnum for vnum, _info in app._item_db_matches]
            app._item_db_add_selected(905)
            card = find_item(905)
            status = app._item_db_status.get_text()
            database_ok = (
                vnums == [905]
                and card is not None
                and card.name == "Red Magician Specialist Card"
                and card.enabled is True
                and card.relist is True
                and card.min_price == 1
                and "vnum 905" in status
            )
            window.close()
            closed_ok = app.item_db_window is None
            result["database"] = database_ok and closed_ok
            GLib.timeout_add(200, check_group_actions)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"database exception: {exc!r}"
            app.quit()
        return False

    def check_group_actions() -> bool:
        try:
            # Items group actions: Relist all also switches monitoring back
            # on (the relist pass skips disabled items) and sets floor 1 on
            # rows without a floor.
            app._bazar_items_set_all("enabled", False)
            app._bazar_items_set_all("relist", True)
            items = list(app.bazar.config.items)
            group_ok = (
                len(items) == 4
                and all(item.enabled for item in items)
                and all(item.relist for item in items)
                and all(item.min_price >= 1 for item in items)
                and find_item(1362).min_price == 5
            )
            app._bazar_items_set_all("enabled", False)
            monitor_off = not any(
                item.enabled for item in app.bazar.config.items
            )
            app._bazar_items_set_all("enabled", True)
            result["group_actions"] = group_ok and monitor_off

            app._bazar_save_config(quiet=True)
            loaded = load_config(config_path)
            saved = {item.name: item for item in loaded.items}
            saved_ok = (
                set(saved)
                == {
                    "bubbl",
                    "stone",
                    "Angel's Feather",
                    "Red Magician Specialist Card",
                }
                and saved["bubbl"].enabled is True
                and saved["stone"].min_price == 5
                and saved["Red Magician Specialist Card"].relist is True
            )
            result["saved"] = saved_ok
            ok = all(
                bool(result.get(key))
                for key in (
                    "panel",
                    "toggles",
                    "hidden",
                    "selection",
                    "from_items",
                    "from_inventory",
                    "database",
                    "group_actions",
                    "saved",
                )
            )
            result["ok"] = ok
            result["detail"] = (
                f"panel={result['panel']} toggles={result['toggles']} "
                f"hidden={result['hidden']} selection={result['selection']} "
                f"from_items={result['from_items']} "
                f"from_inventory={result['from_inventory']} "
                f"database={result['database']} "
                f"group={result['group_actions']} saved={result['saved']}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"group exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1200, wait_attach)
    app.run([])
    server.close()
    print(
        "BAZAR MONITOR ITEMS SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
