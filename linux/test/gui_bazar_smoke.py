"""Headless NosBazar monitor smoke test.

A fake server spawns a Bibi Basar NPC (vnum 793) next to the character, opens
the bazaar on the four-packet NPC sequence, answers the initial listing and
one price search, and the GUI must show the cheapest listing and the gold/ND
profit.  Monitor mode must never send ``c_reg``.
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk

from ntpl.bazar import BazarItem, build_search_packet, load_config
from ntpl.gui import Application
from ntpl.nosmall import ItemInfo


def _rc_blist(
    price: int, seller: str = "Seller", vnum: int = 2174
) -> bytes:
    # rc_blist <page> <15-field item>; field 6 is the price, field 2 the owner.
    entry = (
        f"1001|2002|{seller}|{vnum}|5|0|{price}|120|0|0|0|0|0|0|^0"
    )
    return f"RECV|rc_blist 0 {entry}\n".encode()


class BazarServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        #: Own seller-tab entries (``rc_slist``) the fake server reports.
        self.seller_entries: list[str] = []
        self.connection = None
        self.stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _send(self, text: str) -> None:
        if self.connection is not None:
            try:
                self.connection.sendall(text.encode())
            except OSError:
                pass

    def _run(self) -> None:
        try:
            self.connection, _ = self.listener.accept()
        except OSError:
            return
        self.connection.sendall(
            b"STATUS|ready\n"
            b"RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
            # Bibi Basar (vnum 793) next to the character.
            b"RECV|in 2 793 9584 25 117 3 100 100 460 0 0 -1 1 0 -1 - 0\n"
            b"RECV|inv 2 7.2174.5\n"
            b"RECV|gold 100000 0\n"
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
                self._handle(text)

    def _handle(self, text: str) -> None:
        if text == "POS":
            self._send("STATUS|POS 25 117 14881134 0\n")
            return
        if text.startswith("SEND|npc_req 2 9584"):
            self._send("RECV|npc_req 2 9584 460\n")
            return
        if text.startswith("SEND|n_run 60 0 2 9584"):
            # Type 32 is the NosBazar window (a warehouse is 28; the live
            # server ignores c_reg without this window).
            self._send("RECV|wopen 32 2 642\n")
            return
        if text.startswith("SEND|c_reg "):
            # The live server acknowledges an accepted c_reg with rc_reg 1.
            self._send("RECV|rc_reg 1\n")
            return
        if text.startswith("SEND|c_slist "):
            # The seller tab: own listings, as the smoke configured them.
            body = " ".join(self.seller_entries)
            self._send(f"RECV|rc_slist 0  0 {body}\n".rstrip() + "\n")
            return
        if text.startswith("SEND|c_mod "):
            # The Change action: answer with a refreshed seller list that
            # carries the new price, like the live server (2026-11-23).
            parts = text.split()
            if len(parts) >= 7:
                listing, vnum, amount, price = (
                    parts[3],
                    parts[4],
                    parts[5],
                    parts[6],
                )
                self.seller_entries = [
                    entry
                    for entry in self.seller_entries
                    if not entry.startswith(f"{listing}|")
                ]
                self.seller_entries.append(
                    f"{listing}|14881134|{vnum}|0|{amount}|0|{price}|1|"
                    "600|2|2|0"
                )
                self._send(
                    "RECV|rc_slist 0  0 "
                    + " ".join(self.seller_entries)
                    + "\n"
                )
            return
        if text.startswith("SEND|c_blist "):
            parts = text.split()
            if len(parts) >= 12 and parts[10] == "0":
                # Initial no-filter listing sent by the open sequence.
                self._send("RECV|rc_blist 0\n")
            elif parts and parts[-1] == "2174":
                self._send(_rc_blist(13798).decode())
            elif parts and parts[-1] == "2077":
                self._send(_rc_blist(1500, vnum=2077).decode())
            else:
                self._send("RECV|rc_blist 0\n")
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
    # Keep the smoke away from the developer's real bazar-<pid>.json.
    if not os.environ.get("NTPL_BAZAR_CONFIG"):
        handle = tempfile.NamedTemporaryFile(
            prefix="ntpl-bazar-", suffix=".json", delete=False
        )
        handle.close()
        os.environ["NTPL_BAZAR_CONFIG"] = handle.name
    server = BazarServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    state = {"started": False}

    def start_monitor() -> bool:
        if state["started"]:
            return False
        # Wait until the fake server's `in 2 793 ...` reached the engine.
        if not any(
            entity.vnum == 793
            for entity in app.engine.world.entities.values()
        ):
            return True
        state["started"] = True
        try:
            # The configured name is a shortcut; the Monitor must show the
            # real Item.dat name for the vnum instead.
            app.item_info = {
                2174: ItemInfo(2174, "Speech Bubble", 3000, 0),
                2077: ItemInfo(2077, "Pet Food", 300, 0),
                2072: ItemInfo(2072, "Bell of Sweet Home", 400, 0),
            }
            app.item_info_loaded = True
            item = BazarItem(
                "bubbl",
                build_search_packet([2174]),
                vnum=2174,
                inv_tab=2,
                amount=5,
                min_price=43000,
                nos_cost=0.5,
            )
            # An NPC-resale item: gold cost, so it belongs in the second
            # Monitor view, not the NosMall one.
            npc_item = BazarItem(
                "petfood",
                build_search_packet([2077]),
                vnum=2077,
                inv_tab=2,
                amount=5,
                min_price=0,
                npc_cost=300.0,
            )
            app.bazar_character_entry.set_text("root1")
            # The real UI path: this renders the Monitor and both item lists.
            app._bazar_set_items([item, npc_item])
            app._bazar_start_monitor()
            # One 2.5 s search per item after the open sequence; 8.5 s covers
            # both the NosMall and the NPC-resale answer.
            GLib.timeout_add(8500, check)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def separator_count() -> int:
        """The rule lines the results grid draws (one under the header plus
        one between each pair of rows)."""
        box = app.bazar_results_box
        grid = box.get_first_child() if box is not None else None
        if grid is None:
            return 0
        count = 0
        child = grid.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Separator) and child.has_css_class(
                "bazar-sep"
            ):
                count += 1
            child = child.get_next_sibling()
        return count

    def check() -> bool:
        try:
            lines = list(server.received)
            searches = [
                line for line in lines
                if line.startswith("SEND|c_blist ") and line.endswith(" 2174")
            ]
            npc_searches = [
                line for line in lines
                if line.startswith("SEND|c_blist ") and line.endswith(" 2077")
            ]
            opened = [
                line for line in lines
                if line.startswith("SEND|npc_req 2 9584")
            ]
            empty_init = [
                line for line in lines
                if line.startswith("SEND|c_blist 0 0 0 0 0 0 0 0 0")
            ]
            quote = app.bazar.results.get("bubbl")
            price = None if quote is None else quote.price
            seller = None if quote is None else quote.seller
            profit = None if quote is None else quote.profit_text()
            no_register = not any(
                line.startswith("SEND|c_reg ") for line in lines
            )
            separators = separator_count()

            def first_item_cell():
                grid = app.bazar_results_box.get_first_child()
                cell = (
                    grid.get_child_at(0, 2)
                    if isinstance(grid, Gtk.Grid)
                    else None
                )
                return cell if isinstance(cell, Gtk.Label) else None

            cell = first_item_cell()
            item_text = None if cell is None else cell.get_text()
            tooltip = (
                cell.get_tooltip_text() if cell is not None else ""
            ) or ""
            name_ok = item_text == "Speech Bubble" and "bubbl" in tooltip

            def cell_at(source: str, column: int, row: int = 2):
                target = app.bazar_results_boxes.get(source)
                grid = (
                    target.get_first_child()
                    if target is not None
                    else None
                )
                if not isinstance(grid, Gtk.Grid):
                    return None
                label = grid.get_child_at(column, row)
                return label if isinstance(label, Gtk.Label) else None

            def child_at(source: str, column: int, row: int = 2):
                target = app.bazar_results_boxes.get(source)
                grid = (
                    target.get_first_child()
                    if target is not None
                    else None
                )
                if not isinstance(grid, Gtk.Grid):
                    return None
                return grid.get_child_at(column, row)

            # Every Monitor row carries a one-click Copy button for the real
            # item name, and the controller got the vnum->name resolver so a
            # mixed search group cannot report another item's price.
            copy_cell = child_at("nosmall", 6)
            copy_ok = (
                isinstance(copy_cell, Gtk.Button)
                and copy_cell.get_label() == "Copy"
                and app.bazar._vnum_name is not None
                and app.bazar._vnum_name(2174) == "Speech Bubble"
            )

            npc_quote = app.bazar.results.get("petfood")
            npc_price = None if npc_quote is None else npc_quote.price
            npc_profit = (
                None if npc_quote is None else npc_quote.profit_text()
            )
            nosmall_name = cell_at("nosmall", 0)
            npc_name = cell_at("npc", 0)
            npc_cost = cell_at("npc", 1)
            npc_markup = cell_at("npc", 3)
            npc_header = cell_at("npc", 3, 0)
            notebook = app.bazar_monitor_notebook
            tabs_ok = (
                notebook is not None
                and notebook.get_n_pages() == 2
                and notebook.get_tab_label_text(
                    notebook.get_nth_page(0)
                )
                == "NosMall"
                and notebook.get_tab_label_text(
                    notebook.get_nth_page(1)
                )
                == "NPC resale"
            )
            notebook.set_current_page(1)
            switched_ok = (
                app.bazar_results_box
                is app.bazar_results_boxes.get("npc")
            )
            active_npc_name = first_item_cell()
            notebook.set_current_page(0)
            back_ok = (
                app.bazar_results_box
                is app.bazar_results_boxes.get("nosmall")
            )
            split_ok = bool(
                tabs_ok
                and switched_ok
                and back_ok
                and nosmall_name is not None
                and nosmall_name.get_text() == "Speech Bubble"
                and npc_name is not None
                and npc_name.get_text() == "Pet Food"
                and npc_cost is not None
                and npc_cost.get_text() == "300 g"
                and npc_markup is not None
                and npc_markup.get_text() == "5.0x"
                and npc_header is not None
                and npc_header.get_text() == "Markup"
                and npc_price == 1500
                and npc_profit == "5.0x"
                and active_npc_name is not None
                and active_npc_name.get_text() == "Pet Food"
            )

            # The Monitor search narrows both tabs by item name or vnum.
            search = app.bazar_monitor_search
            search_ok = False
            if search is not None:
                search.set_text("speech")
                app._bazar_render_results()
                nosmall_info = app.bazar_results_infos["nosmall"].get_text()
                npc_info = app.bazar_results_infos["npc"].get_text()
                matched_row = first_item_cell()
                search.set_text("2174")
                app._bazar_render_results()
                vnum_info = app.bazar_results_infos["nosmall"].get_text()
                search.set_text("no such item")
                app._bazar_render_results()
                miss_info = app.bazar_results_infos["nosmall"].get_text()
                search.set_text("")
                app._bazar_render_results()
                reset_info = app.bazar_results_infos["nosmall"].get_text()
                search_ok = (
                    "1 match(es)" in nosmall_info
                    and "1 priced" in nosmall_info
                    and npc_info == "No match"
                    and matched_row is not None
                    and matched_row.get_text() == "Speech Bubble"
                    and "1 match(es)" in vnum_info
                    and miss_info == "No match"
                    and reset_info.startswith("Most profitable first")
                )

            # The editor auto-fills an NPC gold cost from Item.dat, so
            # adding Bell of Sweet Home needs only the vnum.
            app.bazar_form["name"].set_text("bell")
            app.bazar_form["vnums"].set_text("2072")
            app.bazar_form["raw"].set_text("")
            app.bazar_form["vnum"].set_text("2072")
            app.bazar_form["tab"].set_selected(0)
            app.bazar_form["cost_type"].set_selected(2)
            app.bazar_form["cost"].set_value(0)
            auto_item = app._bazar_item_from_form()
            autofill_ok = (
                auto_item is not None
                and auto_item.npc_cost == 400.0
                and app.bazar_form["cost"].get_value() == 400
            )

            def row_name(row):
                label = getattr(row, "name_label", None)
                return None if label is None else label.get_text()

            # Configuration -> Reload names must drop the one-shot latch and
            # start a fresh lookup (a stale non-empty database otherwise stays
            # cached until a restart).
            reload_calls: list = []
            ensure = app._ensure_item_info_loaded
            app._ensure_item_info_loaded = (
                lambda after=None: reload_calls.append(after)
            )
            app.item_info_loaded = True
            app._bazar_reload_names()
            reload_ok = (
                app.item_info_loaded is False and reload_calls == [None]
            )
            app._ensure_item_info_loaded = ensure

            # The real path: the Monitor may render before the DB/Item.dat
            # lookup finishes. A late load must switch the Monitor and both
            # item lists from the configured shortcut to the real name
            # without a restart, and must not overwrite unsaved editor values.
            app.item_info = {}
            app.item_info_loaded = False
            app._bazar_render_results()
            app._bazar_refresh_item_labels()
            app._bazar_select_item("bubbl")
            app.bazar_form["min"].set_value(12345)
            app._bazar_select_relist_item("bubbl")
            app.bazar_relist_form["min"].set_value(9876)
            late_cell = first_item_cell()
            late_before = None if late_cell is None else late_cell.get_text()
            items_before = row_name(app.bazar_items_list.get_first_child())
            relist_before = row_name(
                app.bazar_relist_list.get_first_child()
            )
            app._finish_item_info_load(
                {2174: ItemInfo(2174, "Speech Bubble", 3000, 0)}
            )
            late_cell = first_item_cell()
            late_after = None if late_cell is None else late_cell.get_text()
            items_after = row_name(app.bazar_items_list.get_first_child())
            relist_after = row_name(
                app.bazar_relist_list.get_first_child()
            )
            editor_label = (
                "" if app.bazar_relist_editor_label is None
                else app.bazar_relist_editor_label.get_text()
            )
            late_ok = (
                late_before == "bubbl"
                and late_after == "Speech Bubble"
                and items_before == "bubbl"
                and relist_before == "bubbl"
                and items_after == "Speech Bubble"
                and relist_after == "Speech Bubble"
                and "Speech Bubble" in editor_label
            )
            forms_ok = (
                app.bazar_form["min"].get_value() == 12345
                and app.bazar_relist_form["min"].get_value() == 9876
            )
            copy_label_ok = (
                app._bazar_copy_label_for("bubbl") == "Speech Bubble"
            )
            config_path = os.environ.get("NTPL_BAZAR_CONFIG")
            saved = False
            if config_path:
                app._bazar_save_config()
                try:
                    loaded = load_config(config_path)
                except Exception:  # noqa: BLE001
                    loaded = None
                saved = loaded is not None and len(loaded.items) == 2
            result["ok"] = (
                bool(opened)
                and bool(empty_init)
                and bool(searches)
                and bool(npc_searches)
                and split_ok
                and autofill_ok
                and price == 13798
                and seller == "Seller"
                and profit == "27,596 g/ND"
                and no_register
                and saved
                and separators == 1
                and name_ok
                and late_ok
                and forms_ok
                and copy_label_ok
                and copy_ok
                and search_ok
                and reload_ok
                and app.bazar.mode == "monitor"
            )
            result["detail"] = (
                f"opened={bool(opened)} init={bool(empty_init)} "
                f"search={bool(searches)} npc_search={bool(npc_searches)} "
                f"price={price} seller={seller} "
                f"profit={profit!r} npc={npc_name.get_text() if npc_name is not None else None!r}/"
                f"{npc_price!r}/{npc_profit!r} split={split_ok} "
                f"autofill={autofill_ok} no_register={no_register} "
                f"item_name={item_text!r} tooltip={tooltip!r} "
                f"late={late_before!r}->{late_after!r} "
                f"lists={items_before!r}/{relist_before!r}->"
                f"{items_after!r}/{relist_after!r} "
                f"forms_kept={forms_ok} copy={copy_label_ok} "
                f"copy_button={copy_ok} search={search_ok} "
                f"reload={reload_ok} "
                f"saved={saved} separators={separators} "
                f"mode={app.bazar.mode} phase={app.bazar.phase} "
                f"status={app.bazar_status.get_text()!r}"
            )
            # The Delete confirmation must not resurface the shortcut, and it
            # must survive the stop log emitted by deleting during a pass.
            app.bazar_selected_name = "bubbl"
            app._bazar_delete_item()
            delete_status = app.bazar_status.get_text() or ""
            delete_ok = "deleted Speech Bubble" in delete_status
            result["ok"] = bool(result["ok"] and delete_ok)
            result["detail"] += (
                f" deleted={delete_ok} delete_status={delete_status!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1200, start_monitor)
    app.run([])
    server.close()
    print("BAZAR SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
