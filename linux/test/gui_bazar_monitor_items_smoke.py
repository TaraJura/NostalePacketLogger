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
                # A same-name search alias of item 2174.
                10029: ItemInfo(10029, "Speech Bubble", 3000, 0),
                1362: ItemInfo(1362, "Soulstone Blessing", 100, 0),
                2282: ItemInfo(2282, "Angel's Feather", 100, 0),
                905: ItemInfo(905, "Red Magician Specialist Card", 0, 0),
            }
            app.item_info_loaded = True
            bubble = BazarItem(
                "bubbl",
                build_search_packet([2174, 10029]),
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

    def item_rows() -> dict[str, object]:
        """Catalog rows keyed by item name or ``vnum N``."""
        found: dict[str, object] = {}
        store = app.bazar_items_store
        for position in range(store.get_n_items()):
            row = store.get_item(position)
            if row is None:
                continue
            key = (
                row.item.name
                if row.item is not None
                else f"vnum {row.vnum}"
            )
            found[key] = row
        return found

    def select_item_rows(*keys: str) -> None:
        selection = app.bazar_items_selection
        store = app.bazar_items_store
        selection.unselect_all()
        for key in keys:
            for position in range(store.get_n_items()):
                row = store.get_item(position)
                if row is None:
                    continue
                row_key = (
                    row.item.name
                    if row.item is not None
                    else f"vnum {row.vnum}"
                )
                if row_key == key:
                    selection.select_item(position, False)
                    break

    def toggle_row(row, field: str, active: bool) -> None:
        config_field = "enabled" if field == "monitor" else "relist"
        app._bazar_apply_row_flags(
            [(row.item, row.vnum, row.slot)], config_field, active
        )

    def check_panel() -> bool:
        try:
            shown = rows()
            items = item_rows()
            info = app.bazar_items_info.get_text()
            panel_ok = (
                set(shown) == {"bubbl"}
                and {"bubbl", "stone"} <= set(items)
                and "vnum 2282" in items
                and "1 monitored" in info
                and "0 relisting" in info
            )
            result["panel"] = panel_ok

            # A configured item is one catalog row even when its search
            # group lists several DB vnums with the same name; searching an
            # alias vnum still finds that row.
            def catalog_rows():
                store = app.bazar_items_store
                return [
                    store.get_item(position)
                    for position in range(store.get_n_items())
                ]

            bubble_rows = [
                row
                for row in catalog_rows()
                if row is not None
                and row.item is not None
                and row.item.name == "bubbl"
            ]
            app._inventory_search.set_text("10029")
            app._bazar_render_items()
            alias_found = any(
                row is not None
                and row.item is not None
                and row.item.name == "bubbl"
                for row in catalog_rows()
            )
            app._inventory_search.set_text("")
            app._bazar_render_items()
            result["dedupe"] = len(bubble_rows) == 1 and alias_found

            # The ticks live in the Items catalog: ticking enables the
            # searches, unticking removes the row from the Monitor list.
            toggle_row(items["stone"], "monitor", True)
            toggle_row(items["bubbl"], "monitor", False)
            GLib.timeout_add(250, check_toggles)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"panel exception: {exc!r}"
            app.quit()
        return False

    def check_toggles() -> bool:
        try:
            items = {item.name: item for item in app.bazar.config.items}
            toggles_ok = (
                items["stone"].enabled is True
                and items["bubbl"].enabled is False
                and items["bubbl"].min_price == 0
                and set(rows()) == {"stone"}
            )
            result["toggles"] = toggles_ok

            # An unticked item has no live price: Monitor tables show
            # enabled items only.
            app._bazar_monitor_set_many(["stone"], False)
            app._bazar_render_results()
            result["hidden"] = (
                app.bazar_results_infos["nosmall"].get_text() == "No items"
            )

            # A Relist tick on an unconfigured inventory row creates the
            # item with the slot's tab/stack and floor 1.
            found = item_rows()
            toggle_row(found["vnum 2282"], "relist", True)
            GLib.timeout_add(250, check_relist_tick)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"toggle exception: {exc!r}"
            app.quit()
        return False

    def check_relist_tick() -> bool:
        try:
            angel = find_item(2282)
            result["relist_tick"] = (
                angel is not None
                and angel.enabled is True
                and angel.relist is True
                and angel.min_price == 1
                and angel.inv_tab == 1
                and angel.amount == 11
                and angel.name == "Angel's Feather"
            )
            relist_rows = []
            child = app.bazar_relist_list.get_first_child()
            while child is not None:
                if getattr(child, "item_name", None):
                    relist_rows.append(child.item_name)
                child = child.get_next_sibling()
            result["relist_list"] = relist_rows == ["Angel's Feather"]

            # Monitor selection + group actions: stop the shown rows, then
            # re-enable through the new Items multi-selection buttons.
            app._bazar_monitor_set_many(["Angel's Feather"], True)
            app._bazar_monitor_select_shown()
            selected = set(app._bazar_monitor_selected)
            app._bazar_monitor_set_selected(False)
            stopped = not any(
                item.enabled for item in app.bazar.config.items
            )
            select_item_rows("bubbl", "stone")
            app._bazar_add_selected_to("enabled")
            reenabled = (
                find_item(2174).enabled is True
                and find_item(1362).enabled is True
            )
            result["selection"] = (
                selected == {"Angel's Feather"} and stopped and reenabled
            )

            # Add from Items works on the Items selection too.
            app._bazar_monitor_set_many(["stone"], False)
            select_item_rows("stone")
            app._bazar_monitor_add_from_items()
            result["from_items"] = find_item(1362).enabled is True
            GLib.timeout_add(200, check_database)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"relist exception: {exc!r}"
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
                    "dedupe",
                    "toggles",
                    "hidden",
                    "relist_tick",
                    "relist_list",
                    "selection",
                    "from_items",
                    "database",
                    "group_actions",
                    "saved",
                )
            )
            result["ok"] = ok
            result["detail"] = (
                f"panel={result['panel']} dedupe={result['dedupe']} "
                f"toggles={result['toggles']} "
                f"hidden={result['hidden']} "
                f"relist_tick={result['relist_tick']} "
                f"relist_list={result['relist_list']} "
                f"selection={result['selection']} "
                f"from_items={result['from_items']} "
                f"database={result['database']} "
                f"group={result['group_actions']} saved={result['saved']}"
            )
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
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
