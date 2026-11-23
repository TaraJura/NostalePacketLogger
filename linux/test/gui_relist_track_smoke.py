"""Headless smoke test: choosing which NosBazar items are relisted.

The fake server sends the same inventory as the inventory-scan smoke. The
GUI must:

* add every inventory item as monitor-only (no relist flag);
* let a Relist row tick put one item in the relist set and persist it;
* add from the Items selection and from the ticked Inventory rows;
* remove from relisting without deleting the item or its floor;
* filter the list to the relist set only;
* hand the controller exactly the relist set for a Relist Start pass.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import os
import tempfile

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk

from gui_inventory_scan_smoke import InventoryServer
from ntpl.bazar import load_config
from ntpl.gui import Application
from ntpl.nosmall import ItemInfo

TEST_NAMES = {
    2174: ItemInfo(2174, "Speech Bubble", 3000, 0, 2),
    1362: ItemInfo(1362, "Soulstone Blessing", 100, 0),
    2282: ItemInfo(2282, "Angel's Feather", 100, 0),
    2072: ItemInfo(2072, "Bell of Sweet Home", 400, 0),
    4962: ItemInfo(4962, "Sealed Hellord Spell Gun", 1200000, 1),
    885: ItemInfo(885, "Snow White Tiger Hat", 2400, 0),
    905: ItemInfo(905, "Red Magician Specialist Card", 0, 0),
}


def _rows(listbox) -> list:
    rows = []
    child = listbox.get_first_child()
    while child is not None:
        rows.append(child)
        child = child.get_next_sibling()
    return rows


def _row_for(listbox, name: str):
    for row in _rows(listbox):
        if getattr(row, "item_name", None) == name:
            return row
    return None


def main() -> int:
    handle = tempfile.NamedTemporaryFile(
        prefix="ntpl-bazar-", suffix=".json", delete=False
    )
    handle.write(b'{"version": 1, "items": []}\n')
    handle.close()
    config_path = handle.name
    os.environ["NTPL_BAZAR_CONFIG"] = config_path
    os.environ.setdefault(
        "NTPL_INVENTORY_DIR", tempfile.mkdtemp(prefix="ntpl-inventory-")
    )
    server = InventoryServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result: dict[str, object] = {"ok": False, "detail": "not checked"}
    state = {"started": False, "budget": 60, "name_ticks": 0}

    def wait_attach() -> bool:
        state["budget"] -= 1
        if not any(
            line == "PORTALS|0" or line.startswith("SPEED")
            for line in server.received
        ):
            if state["budget"] <= 0:
                result["detail"] = "GUI never attached"
                app.quit()
                return False
            return True
        if state["started"]:
            return False
        state["started"] = True
        try:
            if app.bazar_page is not None and app.notebook is not None:
                app.notebook.set_current_page(
                    app.notebook.page_num(app.bazar_page)
                )
            GLib.timeout_add(100, wait_for_names)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"setup exception: {exc!r}"
            app.quit()
        return False

    def wait_for_names() -> bool:
        # Let the automatic lookup finish first; injecting early would be
        # overwritten by its result.
        if app.item_info_loaded and app.item_info:
            begin()
            return False
        state["name_ticks"] += 1
        if state["name_ticks"] > 300:  # 30 s
            result["detail"] = "item names did not load"
            app.quit()
            return False
        return True

    def begin() -> bool:
        try:
            app.item_info = dict(TEST_NAMES)
            app.item_info_loaded = True
            app._open_inventory_picker()
            app._inventory_render()
            app._inventory_add(app._inventory_slots())
            GLib.timeout_add(200, check_initial)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"begin exception: {exc!r}"
            app.quit()
        return False

    def check_initial() -> bool:
        try:
            items = list(app.bazar.config.items)
            result["items"] = len(items)
            result["initial_relist"] = [
                item.name for item in items if item.relist
            ]
            result["initial_ready"] = sum(
                1 for item in items if app._bazar_relist_ready(item)
            )
            result["enabled"] = sum(1 for item in items if item.enabled)
            app._bazar_show_section("relist")
            result["section"] = (
                app.bazar_section_notebook.get_current_page()
            )
            result["section_expected"] = app._bazar_section_index.get(
                "relist"
            )
            result["rows"] = len(_rows(app.bazar_relist_list))
            # No checkbox may be ticked before the user chooses an item.
            result["ticks_off"] = all(
                not row.relist_check.get_active()
                for row in _rows(app.bazar_relist_list)
                if hasattr(row, "relist_check")
            )
            bubble = app._bazar_item_by_vnum(2174)
            if bubble is None:
                result["detail"] = "Speech Bubble was not added"
                app.quit()
                return False
            row = _row_for(app.bazar_relist_list, bubble.name)
            if row is None:
                result["detail"] = f"no relist row for {bubble.name}"
                app.quit()
                return False
            # Tick the row: this alone must join the relist set and save.
            row.relist_check.set_active(True)
            result["after_tick"] = bubble.relist
            result["saved_after_tick"] = _saved_relist(bubble.name)
            # The tick must update the row in place: a rebuilt list is what
            # used to throw a scrolled list back to the top.
            result["row_kept"] = (
                _row_for(app.bazar_relist_list, bubble.name) is row
            )
            # Select it and set the floor through the compact editor.
            app._bazar_select_relist_item(bubble.name)
            app.bazar_relist_form["min"].set_value(1000)
            app._bazar_relist_save()
            result["floor_saved"] = bubble.min_price
            result["ready_after_floor"] = app._bazar_relist_ready(bubble)
            result["badge_ready"] = app._bazar_relist_badge(bubble)[0]
            # Filter: only one item is relisting right now.
            app.bazar_relist_filter.set_active(True)
            result["filtered_rows"] = len(_rows(app.bazar_relist_list))
            app.bazar_relist_filter.set_active(False)
            # Add from Items: select Soulstone Blessing in Items.
            blessing = app._bazar_item_by_vnum(1362)
            if blessing is None:
                result["detail"] = "Soulstone Blessing was not added"
                app.quit()
                return False
            app._bazar_select_item(blessing.name)
            app._bazar_relist_add_from_items()
            result["items_selection"] = app.bazar_selected_name
            result["from_items"] = blessing.relist
            result["from_items_badge"] = app._bazar_relist_badge(blessing)[0]
            # Add from Inventory: Etc slot 0 holds Angel's Feather (2282).
            feather = app._bazar_item_by_vnum(2282)
            if feather is None:
                result["detail"] = "Angel's Feather was not added"
                app.quit()
                return False
            app._inventory_checks = {(2, 0)}
            app._bazar_relist_add_from_inventory()
            result["from_inventory"] = feather.relist
            result["feather_tab"] = feather.inv_tab
            result["feather_amount"] = feather.amount
            # Remove Speech Bubble from relisting: the item and its floor
            # must survive.
            app._bazar_select_relist_item(bubble.name)
            app._bazar_relist_remove_selected()
            result["removed"] = bubble.relist
            removed_row = _row_for(app.bazar_relist_list, bubble.name)
            result["removed_tick"] = (
                None
                if removed_row is None
                else removed_row.relist_check.get_active()
            )
            result["removed_floor"] = bubble.min_price
            result["still_configured"] = any(
                item.name == bubble.name for item in app.bazar.config.items
            )
            result["queue"] = sorted(
                item.name for item in app.bazar.relist_items()
            )
            result["expected_queue"] = sorted(
                [blessing.name, feather.name]
            )
            result["monitor_count"] = len(app.bazar.enabled_items())
            saved = load_config(config_path)
            by_name = {item.name: item for item in saved.items}
            result["saved_flags"] = {
                name: (
                    by_name[name].relist,
                    by_name[name].min_price,
                    by_name[name].inv_tab,
                )
                for name in by_name
            }
            # Group actions: Ctrl+click equivalent (select every row), then
            # stop/start relisting for the whole selection or the whole list.
            result["multi_select"] = (
                app.bazar_relist_list.get_selection_mode()
                == Gtk.SelectionMode.MULTIPLE
            )
            app._bazar_relist_select_shown()
            result["selected_shown"] = len(
                app.bazar_relist_list.get_selected_rows()
            )
            result["shown_rows"] = len(_rows(app.bazar_relist_list))
            app._bazar_relist_set_selected(False)
            result["bulk_stop"] = sum(
                1 for item in app.bazar.config.items if item.relist
            )
            app._bazar_relist_set_all_shown(True)
            result["bulk_all"] = sum(
                1 for item in app.bazar.config.items if item.relist
            )
            app._bazar_relist_unselect_all()
            result["unselected"] = len(
                app.bazar_relist_list.get_selected_rows()
            )
            result["group_button"] = app.bazar_relist_group_button is not None
            # A repeated click on the only selected row unselects it.
            toggle_row = _row_for(app.bazar_relist_list, feather.name)
            app.bazar_relist_list.unselect_all()
            app.bazar_relist_list.select_row(toggle_row)
            app._relist_press_row = toggle_row
            app._relist_press_only = True
            app._bazar_relist_row_activated(
                app.bazar_relist_list, toggle_row
            )
            result["toggle_unselect"] = len(
                app.bazar_relist_list.get_selected_rows()
            )
            # The first c_reg constant is repaired from Item.dat before a
            # relist pass: INDEX[0] 2 -> UNK1 10. UNK2 is item-specific and
            # must be left exactly as configured.
            bubble.unk1 = 9
            bubble.unk2 = 3
            result["repaired"] = app._bazar_repair_relist_fields()
            result["fixed_fields"] = (bubble.unk1, bubble.unk2)
            repaired_saved = {
                item.name: item for item in load_config(config_path).items
            }
            result["saved_fields"] = (
                repaired_saved[bubble.name].unk1,
                repaired_saved[bubble.name].unk2,
            )
            result["ok"] = (
                result["items"] == 7
                and result["initial_relist"] == []
                and result["initial_ready"] == 0
                and result["ticks_off"]
                and result["after_tick"] is True
                and result["saved_after_tick"] is True
                and result["row_kept"] is True
                and result["floor_saved"] == 1000
                and result["ready_after_floor"] is True
                and result["badge_ready"] == "ready"
                and result["filtered_rows"] == 1
                and result["from_items"] is True
                and result["from_items_badge"] == "needs floor"
                and result["from_inventory"] is True
                and result["feather_tab"] == 2
                and result["feather_amount"] == 11
                and result["removed"] is False
                and result["removed_tick"] is False
                and result["removed_floor"] == 1000
                and result["still_configured"] is True
                and result["queue"] == result["expected_queue"]
                and result["monitor_count"] == 7
                and result["multi_select"] is True
                and result["selected_shown"] == result["shown_rows"]
                and result["bulk_stop"] == 0
                and result["bulk_all"] == 7
                and result["unselected"] == 0
                and result["toggle_unselect"] == 0
                and result["group_button"] is True
                and result["repaired"] == 1
                and result["fixed_fields"] == (10, 3)
                and result["saved_fields"] == (10, 3)
                and by_name[bubble.name].relist is False
                and by_name[bubble.name].min_price == 1000
                and by_name[feather.name].relist is True
                and by_name[feather.name].inv_tab == 2
            )
            result["detail"] = (
                f"ok={result['ok']} items={result['items']} "
                f"initial={result['initial_relist']} "
                f"tick={result['after_tick']} row_kept={result['row_kept']} "
                f"badge={result['badge_ready']} "
                f"filtered={result['filtered_rows']} "
                f"from_items={result['from_items']} "
                f"from_inventory={result['from_inventory']} "
                f"removed={result['removed']} "
                f"queue={result['queue']} "
                f"selected={result['selected_shown']}/"
                f"{result['shown_rows']} bulk_stop={result['bulk_stop']} "
                f"bulk_all={result['bulk_all']}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    def _saved_relist(name: str) -> bool | None:
        saved = load_config(config_path)
        for item in saved.items:
            if item.name == name:
                return item.relist
        return None

    GLib.timeout_add(100, wait_attach)
    app.run([])
    server.close()
    print(
        "BAZAR RELIST TRACK SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
