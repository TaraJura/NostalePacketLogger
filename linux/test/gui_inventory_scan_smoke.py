"""Headless smoke test: NosBazar inventory scan and price-all.

The fake server sends a live inventory with the four shapes seen on the
server:

* ``inv 1`` / ``inv 2`` three-field tokens, including a bound (negative) vnum;
* ``inv 0`` seven-field equipment tokens (quantity 0, negative limited item);
* ``inv 6`` five-field equipment-style tokens.

The GUI must list every slot with its Item.dat name and details, let one
item be added to the bazaar list, and then price every inventory item in one
action without duplicating the first one.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import os
import shutil
import socket
import tempfile
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.bazar import load_config
from ntpl.gui import Application
from ntpl.nosmall import ItemInfo
from ntpl.spawnlog import (
    inventory_cache_path,
    load_inventory_cache,
)

SELF_ID = 14881134


class InventoryServer:
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
        try:
            self.connection.sendall(
                b"STATUS|ready\n"
                b"RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
                # A Bibi Basar on the map lets Monitor Start resolve an NPC.
                b"RECV|in 2 793 9584 25 117 3 100 100 460 0 0 -1 1 0 -1 - 0\n"
                b"RECV|in 1 root1 - 14881134 2 2 2 0 0 0 8 3\n"
                b"RECV|inv 1 0.2174.5 1.1362.10 2.-2072.4\n"
                b"RECV|inv 2 0.2282.11\n"
                b"RECV|inv 0 27.-4962.5.7.1.0.0 30.885.0.0.0.0.0\n"
                b"RECV|inv 6 0.905.0.5.0\n"
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
    # Always use a fresh config: dev-test-bot.sh shares one temp file across
    # smokes, and the NosMall import smoke leaves 18 items in it. Loading
    # those here would make the first inventory item look "already tracked".
    handle = tempfile.NamedTemporaryFile(
        prefix="ntpl-bazar-", suffix=".json", delete=False
    )
    handle.write(b'{"version": 1, "items": []}\n')
    handle.close()
    config_path = handle.name
    os.environ["NTPL_BAZAR_CONFIG"] = config_path
    cache_dir = tempfile.mkdtemp(prefix="ntpl-inventory-")
    os.environ["NTPL_INVENTORY_DIR"] = cache_dir
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
            # Simulate the user opening the NosBazar tab: the page map signal
            # must start the item-name lookup by itself.
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
        # Wait for the automatic lookup itself: item_info can be large and the
        # Item.dat fallback takes ~1 s. Synchronizing here also guarantees no
        # worker is still in flight when begin_scan injects its test names
        # (a late finish would otherwise overwrite them).
        if app.item_info_loaded and app.item_info:
            begin_scan()
            return False
        state["name_ticks"] += 1
        if state["name_ticks"] > 300:  # 30 s
            result["detail"] = (
                "item names did not load automatically "
                f"(loaded={app.item_info_loaded}, "
                f"count={len(app.item_info)})"
            )
            app.quit()
            return False
        return True

    def begin_scan() -> bool:
        try:
            # The page map must have loaded names (DB first, Item.dat
            # fallback) before we replace them with deterministic test names.
            result["auto_count"] = len(app.item_info)
            auto = app.item_info.get(1092)
            result["auto_name"] = None if auto is None else auto.name
            app.item_info = {
                2174: ItemInfo(2174, "Speech Bubble", 3000, 0),
                1362: ItemInfo(1362, "Soulstone Blessing", 100, 0),
                2282: ItemInfo(2282, "Angel's Feather", 100, 0),
                4962: ItemInfo(4962, "Sealed Hellord Spell Gun", 1200000, 1),
                885: ItemInfo(885, "Snow White Tiger Hat", 2400, 0),
                905: ItemInfo(905, "Red Magician Specialist Card", 0, 0),
            }
            app.item_info_loaded = True
            app._open_inventory_picker()
            app._inventory_render()
            result["sections"] = app.bazar_section_notebook.get_n_pages()
            GLib.timeout_add(200, check_scan)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"begin_scan exception: {exc!r}"
            app.quit()
        return False

    def find_item(search_vnum: int):
        for item in app.bazar.config.items:
            if search_vnum in item.vnums or item.vnum == search_vnum:
                return item
        return None

    def check_scan() -> bool:
        try:
            slots = app._inventory_slots()
            names = {
                (slot.bag, slot.index): app._item_name_for(slot.vnum)
                for slot in slots
            }
            result["slots"] = len(slots)
            result["names"] = names
            result["negative_name"] = app._item_name_for(-4962)
            result["equipment_fields"] = {
                (slot.bag, slot.index): slot.fields
                for slot in slots
                if slot.fields
            }
            result["status"] = (
                app._inventory_status.get_text()
                if app._inventory_status is not None
                else ""
            )
            # Select the Speech Bubble slot in Main and add it.
            app._inventory_checks.add((1, 0))
            app._inventory_add(app._inventory_checked_slots())
            GLib.timeout_add(150, check_one)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"scan exception: {exc!r}"
            app.quit()
        return False

    def check_one() -> bool:
        try:
            items = list(app.bazar.config.items)
            first = find_item(2174)
            result["after_one"] = len(items)
            result["first_name"] = None if first is None else first.name
            result["first_tab"] = None if first is None else first.inv_tab
            result["first_amount"] = None if first is None else first.amount
            result["first_nos"] = None if first is None else first.nos_cost
            result["first_packet"] = (
                None if first is None else first.search_packet
            )
            # Now price every inventory item in one action.
            app._bazar_price_inventory()
            GLib.timeout_add(250, check_all)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"one exception: {exc!r}"
            app.quit()
        return False

    def check_all() -> bool:
        try:
            items = list(app.bazar.config.items)
            by_name = {item.name: item for item in items}
            guns = find_item(4962)
            hat = find_item(885)
            card = find_item(905)
            blessing = find_item(1362)
            feather = find_item(2282)
            bound = find_item(2072)
            result["after_all"] = len(items)
            result["names_all"] = sorted(by_name)
            result["gun_tab"] = None if guns is None else guns.inv_tab
            result["gun_amount"] = None if guns is None else guns.amount
            result["hat_tab"] = None if hat is None else hat.inv_tab
            result["hat_amount"] = None if hat is None else hat.amount
            result["card_tab"] = None if card is None else card.inv_tab
            result["blessing_nos"] = (
                None if blessing is None else blessing.nos_cost
            )
            result["feather_npc"] = (
                None if feather is None else feather.npc_cost
            )
            result["bound_tab"] = None if bound is None else bound.inv_tab
            result["bound_amount"] = (
                None if bound is None else bound.amount
            )
            result["mode"] = app.bazar.mode
            app._bazar_save_config(quiet=True)
            loaded = load_config(os.environ["NTPL_BAZAR_CONFIG"])
            result["saved"] = len(loaded.items)
            result["unique_packets"] = len(
                {item.search_packet for item in items}
            )
            app._save_inventory_cache(force=True)
            cached = load_inventory_cache(
                inventory_cache_path(SELF_ID), SELF_ID
            )
            result["cache_saved"] = (
                None
                if cached is None
                else sum(len(slots) for slots in cached[0].values())
            )
            # Simulate a restart after the DLL log was truncated: no live
            # inventory and no log dump, so only the cache can restore it.
            app.engine.world.inventory = {}
            app._inventory_cache_loaded = False
            app._inventory_seeded = True
            app._inventory_core_seen = False
            app._inventory_full_bags.clear()
            app._maybe_load_inventory_cache()
            result["cache_slots"] = len(app._inventory_slots())
            result["cache_note"] = app._inventory_cache_note
            # Select one inventory item and set up relisting through the new
            # second-level section navbar.
            app._inventory_checks = {(1, 0)}
            app._inventory_set_up_relist()
            result["relist_page"] = app.bazar_section_notebook.get_current_page()
            result["relist_expected"] = app._bazar_section_index.get("relist")
            result["relist_selected"] = app.bazar_relist_selected
            form = app.bazar_relist_form
            result["relist_vnum"] = form["vnum"].get_text()
            result["relist_tab"] = form["tab"].get_selected()
            result["relist_amount"] = int(form["amount"].get_value())
            form["min"].set_value(1000)
            app._bazar_relist_save()
            relist_item = find_item(2174)
            result["relist_saved_min"] = (
                None if relist_item is None else relist_item.min_price
            )
            result["relist_saved_tab"] = (
                None if relist_item is None else relist_item.inv_tab
            )
            result["relist_saved_amount"] = (
                None if relist_item is None else relist_item.amount
            )
            app.bazar_character_entry.set_text("root1")
            app._bazar_start_relist()
            result["relist_mode"] = app.bazar.mode
            # A list rebuilt from later updates alone must be marked partial
            # and must never replace the complete cache with a partial list.
            try:
                inventory_cache_path(SELF_ID).unlink()
            except OSError:
                pass
            app.engine.world.inventory = {}
            app._inventory_core_seen = False
            app._inventory_full_bags = {1}
            app.engine.feed_text("RECV", "inv 1 0.2174.5")
            app._inventory_render()
            result["partial_status"] = (
                app._inventory_status.get_text()
                if app._inventory_status is not None
                else ""
            )
            app._inventory_cache_dirty = True
            app._save_inventory_cache(force=True)
            result["partial_not_cached"] = not inventory_cache_path(
                SELF_ID
            ).exists()
            # Regression: with no live inventory, renders must seed the log
            # once, not recurse through seed -> render -> seed (live
            # 2026-11-23: 109k log lines and a frozen window).
            app.engine.world.inventory = {}
            app._inventory_seeded = False
            seed_calls = {"n": 0}
            original_seed = app._bazar_seed_from_log

            def counting_seed(force: bool = False) -> None:
                seed_calls["n"] += 1
                return original_seed(force)

            app._bazar_seed_from_log = counting_seed
            for _unused in range(5):
                app._inventory_render()
            app._bazar_seed_from_log = original_seed
            result["seed_calls"] = seed_calls["n"]
            ok = (
                result["seed_calls"] <= 1
                and result["slots"] == 7
                and result["negative_name"] == "Sealed Hellord Spell Gun"
                and result["names"].get((1, 0)) == "Speech Bubble"
                and result["names"].get((1, 1)) == "Soulstone Blessing"
                and result["names"].get((2, 0)) == "Angel's Feather"
                and result["names"].get((0, 27)) == "Sealed Hellord Spell Gun"
                and result["names"].get((0, 30)) == "Snow White Tiger Hat"
                and result["names"].get((6, 0)) == "Red Magician Specialist Card"
                and result["equipment_fields"].get((0, 27))
                == ("7", "1", "0", "0")
                and "7 slot(s)" in result["status"]
                and result["after_one"] == 1
                and result["first_name"] == "Speech Bubble"
                and result["first_tab"] == 1
                and result["first_amount"] == 5
                and result["first_nos"] == 0.5
                and result["after_all"] == 7
                and result["gun_tab"] is None
                and result["gun_amount"] == 1
                and result["hat_tab"] == 0
                and result["hat_amount"] == 1
                and result["card_tab"] is None
                and result["blessing_nos"] == 1.0
                and result["feather_npc"] == 100.0
                # A bound negative vnum cannot be found by find_inventory
                # for c_reg, so the planner leaves it monitor-only.
                and result["bound_tab"] is None
                and result["bound_amount"] == 1
                and result["mode"] == "monitor"
                and result["saved"] == 7
                and result["unique_packets"] == 7
                and result["cache_saved"] == 7
                and result["cache_slots"] == 7
                and bool(result["cache_note"])
                and "partial inventory" in result["partial_status"]
                and result["partial_not_cached"] is True
                and result["relist_page"] == result["relist_expected"]
                and result["relist_selected"] == "Speech Bubble"
                and result["relist_vnum"] == "2174"
                and result["relist_tab"] == 2
                and result["relist_amount"] == 5
                and result["relist_saved_min"] == 1000
                and result["relist_saved_tab"] == 1
                and result["relist_saved_amount"] == 5
                and result["relist_mode"] == "relist"
                and result["sections"] == 4
                # The page map must have started the real lookup by itself.
                and result["auto_count"]
                and result["auto_name"] == "Group Time-Space Piece"
            )
            result["ok"] = bool(ok)
            result["detail"] = (
                f"slots={result['slots']} after_one={result['after_one']} "
                f"after_all={result['after_all']} mode={result['mode']} "
                f"saved={result['saved']} unique={result['unique_packets']} "
                f"seed_calls={result['seed_calls']} "
                f"sections={result['sections']} "
                f"auto_names={result['auto_count']}/"
                f"{result['auto_name']!r} "
                f"status={result['status']!r} names={result['names']!r} "
                f"first=({result['first_tab']},{result['first_amount']},"
                f"{result['first_nos']}) gun=({result['gun_tab']},"
                f"{result['gun_amount']}) hat=({result['hat_tab']},"
                f"{result['hat_amount']}) card_tab={result['card_tab']} "
                f"bound=({result['bound_tab']},{result['bound_amount']}) "
                f"blessing_nos={result['blessing_nos']} "
                f"feather_npc={result['feather_npc']} "
                f"cache_saved={result['cache_saved']} "
                f"cache_slots={result['cache_slots']} "
                f"cache_note={result['cache_note']!r} "
                f"partial={result['partial_status']!r} "
                f"partial_not_cached={result['partial_not_cached']} "
                f"relist=({result['relist_page']},"
                f"{result['relist_selected']},{result['relist_vnum']},"
                f"{result['relist_tab']},{result['relist_amount']},"
                f"{result['relist_saved_min']},"
                f"{result['relist_saved_tab']},"
                f"{result['relist_saved_amount']},"
                f"{result['relist_mode']}) "
                f"fields={result['equipment_fields']!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"all exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(250, wait_attach)
    app.run([])
    server.close()
    try:
        os.unlink(config_path)
    except OSError:
        pass
    shutil.rmtree(cache_dir, ignore_errors=True)
    print(
        "INVENTORY SCAN SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
