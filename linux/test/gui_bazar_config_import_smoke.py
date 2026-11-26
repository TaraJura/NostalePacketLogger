"""Headless smoke test: importing a saved bazar config into Items.

The Items catalog shows the whole Item.dat database; a monitoring set is a
JSON config (the old 19-item curation, the full NosMall export, ...).  The
smoke checks both import modes on the new UI:

* add merges the file's items and skips duplicates by name or vnum;
* replace swaps the whole list after the confirmation path;
* old files without the ``relist`` flag load monitor-only;
* a broken file changes nothing.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import json
import os
import tempfile

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.bazar import BazarItem, build_search_packet, load_config
from ntpl.gui import Application
from ntpl.nosmall import ItemInfo

from gui_bazar_monitor_items_smoke import MonitorServer


def _config_file(items: list[dict]) -> str:
    handle = tempfile.NamedTemporaryFile(
        prefix="ntpl-import-", suffix=".json", delete=False, mode="w",
        encoding="utf-8",
    )
    json.dump(
        {
            "version": 1,
            "character": "ignored",
            "refresh": 12.0,
            "npc_id": 1234,
            "items": items,
        },
        handle,
    )
    handle.close()
    return handle.name


def main() -> int:
    handle = tempfile.NamedTemporaryFile(
        prefix="ntpl-bazar-", suffix=".json", delete=False
    )
    handle.write(b'{"version": 1, "items": []}\n')
    handle.close()
    config_path = handle.name
    os.environ["NTPL_BAZAR_CONFIG"] = config_path
    os.environ["NTPL_INVENTORY_DIR"] = tempfile.mkdtemp(
        prefix="ntpl-import-inv-"
    )
    names = {
        2174: ItemInfo(2174, "Speech Bubble", 3000, 0),
        1362: ItemInfo(1362, "Soulstone Blessing", 100, 0),
        2282: ItemInfo(2282, "Angel's Feather", 100, 0),
    }
    add_path = _config_file(
        [
            # Already covered by the existing row's vnum: skipped.
            {
                "name": "Speech Bubble",
                "search_packet": build_search_packet([2174]),
                "vnum": 2174,
                "enabled": True,
            },
            # Old format: no relist flag -> monitor-only.
            {
                "name": "stone",
                "search_packet": build_search_packet([1362]),
                "vnum": 1362,
                "nos_cost": 1.0,
                "enabled": True,
            },
            {
                "name": "angel",
                "search_packet": build_search_packet([2282]),
                "vnum": 2282,
                "enabled": True,
            },
        ]
    )
    replace_path = _config_file(
        [
            {
                "name": "Soulstone Blessing",
                "search_packet": build_search_packet([1362]),
                "vnum": 1362,
                "min_price": 500,
                "enabled": True,
                "relist": True,
            },
            {
                "name": "Angel's Feather",
                "search_packet": build_search_packet([2282]),
                "vnum": 2282,
                "enabled": True,
                "relist": False,
            },
        ]
    )
    bad_path = _config_file([])
    with open(bad_path, "w") as broken:
        broken.write("{not json")
    server = MonitorServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result: dict[str, object] = {"ok": False, "detail": "not checked"}
    state = {"budget": 100}

    def catalog_ticked() -> dict[int, bool]:
        found: dict[int, bool] = {}
        store = app.bazar_items_store
        for position in range(store.get_n_items()):
            row = store.get_item(position)
            if row is not None and row.item is not None:
                found[row.vnum] = bool(row.monitor)
        return found

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
            app.item_info = dict(names)
            app.item_info_loaded = True
            seed = BazarItem(
                "bubbl", build_search_packet([2174]), vnum=2174
            )
            app._bazar_set_items([seed])
            result["seed"] = len(app.bazar.config.items)

            # Add: the file's Speech Bubble is covered by the seeded vnum,
            # the other two are new. Old files have no relist flag.
            added = app._bazar_import_config_path(add_path, "add")
            items = {item.name: item for item in app.bazar.config.items}
            result["add_ok"] = added
            result["add_names"] = sorted(items)
            result["add_stone_relist"] = (
                items.get("stone") is not None
                and items["stone"].relist is False
            )
            result["add_monitor"] = sum(
                1 for item in items.values() if item.enabled
            )
            result["catalog_rows"] = app.bazar_items_store.get_n_items()
            result["catalog_ticks"] = catalog_ticked()
            result["monitor_rows"] = sum(
                1
                for _ in iter_monitor_rows()
            )
            # Replace: exactly the file's two items, flags as written.
            replaced = app._bazar_import_config_path(
                replace_path, "replace"
            )
            items = {item.name: item for item in app.bazar.config.items}
            result["replace_ok"] = replaced
            result["replace_names"] = sorted(items)
            result["replace_relist"] = (
                items.get("Soulstone Blessing") is not None
                and items["Soulstone Blessing"].relist is True
                and items["Soulstone Blessing"].min_price == 500
            )
            result["replace_ticks"] = catalog_ticked()
            result["info"] = app.bazar_items_info.get_text()
            # A broken file leaves the replace result alone.
            before = len(app.bazar.config.items)
            bad = app._bazar_import_config_path(bad_path, "replace")
            result["bad_ok"] = bad is False
            result["bad_kept"] = len(app.bazar.config.items) == before
            saved = load_config(config_path)
            result["saved"] = sorted(item.name for item in saved.items)
            result["ok"] = bool(
                result["seed"] == 1
                and result["add_ok"] is True
                and result["add_names"]
                == ["angel", "bubbl", "stone"]
                and result["add_stone_relist"] is True
                and result["add_monitor"] == 3
                and result["catalog_rows"] == 3
                and result["catalog_ticks"].get(1362) is True
                and result["catalog_ticks"].get(2282) is True
                and result["monitor_rows"] == 3
                and result["replace_ok"] is True
                and result["replace_names"]
                == ["Angel's Feather", "Soulstone Blessing"]
                and result["replace_relist"] is True
                and result["replace_ticks"].get(1362) is True
                and result["replace_ticks"].get(2282) is True
                and "2 monitored" in result["info"]
                and result["bad_ok"] is True
                and result["bad_kept"] is True
                and result["saved"]
                == ["Angel's Feather", "Soulstone Blessing"]
            )
            result["detail"] = (
                f"seed={result['seed']} add={result['add_ok']} "
                f"names={result['add_names']} "
                f"stone_relist={result['add_stone_relist']} "
                f"monitor={result['add_monitor']} "
                f"catalog={result['catalog_rows']}/{result['catalog_ticks']} "
                f"monitor_rows={result['monitor_rows']} "
                f"replace={result['replace_ok']} "
                f"names={result['replace_names']} "
                f"relist={result['replace_relist']} "
                f"ticks={result['replace_ticks']} "
                f"bad={result['bad_ok']}/{result['bad_kept']} "
                f"saved={result['saved']} info={result['info']!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        for path in (add_path, replace_path, bad_path):
            try:
                os.unlink(path)
            except OSError:
                pass
        app.quit()
        return False

    def iter_monitor_rows():
        child = app.bazar_monitor_items_list.get_first_child()
        while child is not None:
            yield child
            child = child.get_next_sibling()

    GLib.timeout_add(1200, wait_attach)
    app.run([])
    server.close()
    print(
        "BAZAR CONFIG IMPORT SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
