"""The NosBazar page: Items catalog, Relist and Monitor.

Extracted from gui.py; every method still runs on the Application
instance, so it uses self state exactly like the core methods.
"""


from .bazar import BIBI_VNUM
from .bazar import BazarError
from .bazar import BazarItem
from .bazar import BazarQuote
from .bazar import MAX_ITEMS
from .bazar import build_search_packet
from .bazar import default_config_path as default_bazar_config_path
from .bazar import inventory_search_items
from .bazar import load_config as load_bazar_config
from .bazar import monitor_source
from .bazar import normalize_search_packet
from .bazar import save_config as save_bazar_config
from .bazar import search_vnums
from .bazar import tracked_vnums
from .client import ConnectionLost
from .engine import InventorySlot
from .guicommon import BAG_LABELS
from .guicommon import BazarItemRowView
from .guicommon import ITEM_INFO_RETRY_S
from .nosmall import ItemInfo
from .nosmall import load_item_info
from .protocol import Packet
from .spawnlog import inventory_cache_path
from .spawnlog import load_bazaar_open
from .spawnlog import load_bazaar_skill
from .spawnlog import load_inventory as load_log_inventory
from .spawnlog import load_inventory_cache
from .spawnlog import load_self_name
from .spawnlog import log_inventory_bags
from .spawnlog import log_was_truncated
from .spawnlog import save_inventory_cache
from gi.repository import GLib
from gi.repository import Gio
from gi.repository import Gtk
from gi.repository import Pango
from pathlib import Path
import os
import threading
import time

class BazarPageMixin:
    def _bazar_copy_menu(self, anchor: Gtk.Widget, name: str) -> None:
        """A right-click menu with the one action that matters here."""
        menu = Gio.Menu()
        item = Gio.MenuItem.new("Copy item name", None)
        item.set_action_and_target_value(
            "app.copy-item-name", GLib.Variant("s", name)
        )
        menu.append_item(item)
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_has_arrow(False)
        popover.set_parent(anchor)
        popover.connect("closed", lambda p: p.unparent())
        popover.popup()

    def _bazar_send(self, packet: str) -> bool:
        """Controller send callback: one game packet through the client."""
        if self.client is None or not self.client.connected:
            self._bazar_log("not connected")
            return False
        try:
            self.client.send_packet(packet)
        except (ConnectionLost, ValueError) as exc:
            self._bazar_log(f"send failed: {exc}")
            return False
        return True

    def _bazar_log(self, text: str) -> None:
        # One status bar per window: the NosBazar page reads its live state
        # from bazar_info (top) and actions from the window status bar,
        # exactly like every other tab.
        self._set_status(text)

    @staticmethod
    def _bazar_path_for(winpid: int) -> Path:
        override = os.environ.get("NTPL_BAZAR_CONFIG")
        if override:
            return Path(override)
        # Honour XDG_CONFIG_HOME via the shared default (bazar.json lives in
        # the same directory); only the file name changes per client.
        return default_bazar_config_path().with_name(
            f"bazar-{winpid}.json"
        )

    def _bazar_load_default_config(self) -> None:
        path = default_bazar_config_path()
        try:
            if not path.is_file() or path.stat().st_size <= 2:
                return
        except OSError:
            return
        self.bazar_config_path = path
        self._bazar_load_config(path, fallback=False)

    def _bazar_load_config(
        self, path: str | Path | None = None, fallback: bool = True
    ) -> bool:
        """Load the per-client config, optionally seeding from bazar.json."""
        target = Path(path) if path is not None else self.bazar_config_path
        candidates = [target]
        default = default_bazar_config_path()
        if fallback and default != target:
            candidates.append(default)
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                config = load_bazar_config(candidate)
            except BazarError as exc:
                self._bazar_log(f"config: {exc}")
                return False
            self._bazar_stop()
            self.bazar.config = config
            self.bazar.set_items(
                config.items,
                character=config.character,
                refresh=config.refresh,
            )
            self.bazar.config.npc_id = config.npc_id
            self.bazar.config.open_option = config.open_option
            self.bazar.config.open_page = config.open_page
            if self.bazar_character_entry is not None:
                self.bazar_character_entry.set_text(config.character)
            if self.bazar_npc_entry is not None:
                self.bazar_npc_entry.set_text(
                    "" if config.npc_id is None else str(config.npc_id)
                )
            self._bazar_npc_autofilled = False
            self._bazar_npc_autofill_value = ""
            if self.bazar_refresh_spin is not None:
                self.bazar_refresh_spin.set_value(config.refresh)
            self.bazar_config_path = target
            self._bazar_config_loaded = True
            if self.bazar_config_label is not None:
                self.bazar_config_label.set_text(str(target))
            self._bazar_render_items()
            self._bazar_render_results()
            self._bazar_render_relist_items()
            self._update_bazar_inventory_info()
            self._bazar_update_relist_info()
            if self._inventory_list is not None:
                self._inventory_schedule_render()
            self._bazar_update_info()
            self._bazar_log(
                f"loaded {len(config.items)} NosBazar item(s) from "
                f"{candidate.name}"
            )
            return True

        self._bazar_stop()
        self.bazar.set_items([])
        self.bazar_selected_name = None
        self._bazar_selected_slot_vnum = None
        if self.bazar_config_label is not None:
            self.bazar_config_label.set_text(str(target))
        self._bazar_render_items()
        self._bazar_render_results()
        self._bazar_render_relist_items()
        self._bazar_render_monitor_items()
        self._bazar_update_relist_info()
        return False

    def _bazar_apply_settings(self) -> None:
        """Copy the character / NPC / refresh widgets into the config."""
        if self.bazar_character_entry is not None:
            # The attached character is authoritative: its own `in 1` (or a
            # party list) names it, so a relist never needs the name typed.
            # The field/config stay as the fallback for a client attached
            # before that packet was seen.
            active = (self.engine.world.self_name or "").strip()
            text = active or self.bazar_character_entry.get_text().strip()
            if text and self.bazar_character_entry.get_text().strip() != text:
                self.bazar_character_entry.set_text(text)
            self.bazar.config.character = text
        if self.bazar_refresh_spin is not None:
            self.bazar.config.refresh = float(
                self.bazar_refresh_spin.get_value()
            )
        if self.bazar_npc_entry is not None:
            text = self.bazar_npc_entry.get_text().strip()
            if text.isdigit() and int(text) > 0:
                self.bazar.config.npc_id = int(text)
            else:
                self.bazar.config.npc_id = None
        self._bazar_update_relist_info()

    def _bazar_save_config(self, quiet: bool = False) -> bool:
        self._bazar_apply_settings()
        state = self.engine.world.bazar
        if state.option_learned:
            # Persist the dialog option actually used on this server; the
            # entity id itself is per map, so only the option/page go in.
            self.bazar.config.open_option = state.open_option
            self.bazar.config.open_page = state.open_page
        try:
            save_bazar_config(self.bazar_config_path, self.bazar.config)
        except BazarError as exc:
            self._bazar_log(f"config: {exc}")
            return False
        self._bazar_config_loaded = True
        if self.bazar_config_label is not None:
            self.bazar_config_label.set_text(str(self.bazar_config_path))
        if not quiet:
            self._bazar_log(f"saved {self.bazar_config_path}")
        return True

    def _bazar_start_timer(self) -> None:
        if self.bazar_timer is None:
            self.bazar_timer = GLib.timeout_add(100, self._bazar_tick)

    def _bazar_stop(self, reason: str = "") -> None:
        if self.bazar_timer is not None:
            GLib.source_remove(self.bazar_timer)
            self.bazar_timer = None
        if self.bazar.running:
            self.bazar.stop(reason)
        self._bazar_update_info()
        self._bazar_update_relist_info()

    def _bazar_open(self) -> None:
        if self.client is None or not self.client.connected:
            self._bazar_log("not connected")
            return
        self._bazar_apply_settings()
        if not self.bazar.open_bazaar(self.engine.world):
            self._bazar_log(self.bazar.last_error or "cannot open NosBazar")
            return
        self._bazar_start_timer()
        self._bazar_update_info()

    def _bazar_start_monitor(self) -> None:
        if self.client is None or not self.client.connected:
            self._bazar_log("not connected")
            return
        self._bazar_apply_settings()
        # The controller falls back to the NosBazar medal when no Bibi is
        # visible; make sure the inventory (and so the medal slot) is known.
        self._inventory_slots()
        if self.bazar.mode == "relist":
            self._bazar_log(
                "Monitor replaces Relist; no listing changes until Relist "
                "Start is pressed again"
            )
        if not self.bazar.start_monitor(self.engine.world):
            self._bazar_log(self.bazar.last_error or "cannot start monitor")
            return
        self._bazar_start_timer()
        self._bazar_render_results()
        self._bazar_update_info()

    def _bazar_repair_relist_fields(self) -> int:
        """Align the first ``c_reg`` item constant with Item.dat.

        ``unk1`` is ``8 + Item.dat INDEX[0]`` for every accepted packet
        observed (live 2026-11-23); the other constants are item-specific
        and are left exactly as configured. A wrong tuple makes the server
        ignore the c_reg silently, which the rc_reg timeout reports.
        """
        repaired = 0
        for item in self.bazar.config.items:
            if not item.relist or not item.enabled:
                continue
            if item.inv_tab not in (1, 2) or not item.vnum:
                continue
            info = self.item_info.get(abs(item.vnum))
            if info is None or info.index0 <= 0:
                continue
            unk1 = min(1000, 8 + info.index0)
            if item.unk1 != unk1:
                item.unk1 = unk1
                repaired += 1
        if repaired:
            self._bazar_save_config(quiet=True)
            self._bazar_log(
                f"relist: fixed the c_reg UNK1 for {repaired} item(s) "
                "from Item.dat"
            )
        return repaired

    def _bazar_start_relist(self) -> None:
        if self.client is None or not self.client.connected:
            self._bazar_log("not connected")
            return
        self._bazar_apply_settings()
        self._bazar_repair_relist_fields()
        self._inventory_slots()
        if self.bazar.mode == "monitor":
            self._bazar_log("Relist replaces Monitor")
        if not self.bazar.start_relist(self.engine.world):
            self._bazar_log(self.bazar.last_error or "cannot start relist")
            return
        self._bazar_start_timer()
        self._bazar_render_results()
        self._bazar_update_info()

    def _bazar_tick(self) -> bool:
        if self._closing:
            self.bazar_timer = None
            return False
        if not self.bazar.running:
            self.bazar_timer = None
            self._bazar_update_info()
            return False
        if self.client is None or not self.client.connected:
            self.bazar.stop("disconnected")
            self.bazar_timer = None
            self._bazar_update_info()
            return False
        self.bazar.tick(self.engine.world)
        if self.bazar.version != self._bazar_render_version:
            self._bazar_render_results()
        self._bazar_update_info()
        if not self.bazar.running:
            # "Open bazaar" stops by itself once the window answers.
            self.bazar_timer = None
            return False
        return True

    def _bazar_update_info(self) -> None:
        if self.bazar_info is None:
            return
        if not self.bazar.running:
            state = "bazaar open" if self.engine.world.bazar.open else "idle"
            self.bazar_info.set_text(f"NosBazar {state}")
            return
        phase = {
            "open": "opening",
            "waiting": "waiting for Bibi Basar",
            "pass": "searching",
            "cooldown": "refresh wait",
            "idle": "idle",
        }.get(self.bazar.phase, self.bazar.phase)
        summary = self.bazar.summary
        suffix = f" · {summary}" if summary else ""
        self.bazar_info.set_text(
            f"{self.bazar.mode} · {phase}{suffix} · "
            f"refresh {self.bazar.config.refresh:g}s"
        )

    def _world_inventory_slots(self) -> list[InventorySlot]:
        """Every known slot, ordered by bag and slot index.

        The engine keeps all bags it received, including the equipment bags
        (``0`` Equip and the ``6``/``7`` extras); the inventory picker and the
        summary need them, not only the Main/Etc bags relisting uses.
        """
        world = self.engine.world
        return [
            slot
            for bag in sorted(world.inventory)
            for _index, slot in sorted(world.inventory[bag].items())
        ]

    def _inventory_slots(self) -> list[InventorySlot]:
        """Live slots, backfilled from the client log when none arrived."""
        slots = self._world_inventory_slots()
        if not slots and not self._inventory_seeded:
            # Seed once. Re-seeding from every render used to loop: the seed
            # scheduled another inventory render, which found no live slots
            # and seeded again (109k log lines and a frozen window, live
            # 2026-11-23). Rescan forces a re-read explicitly.
            self._bazar_seed_from_log()
            slots = self._world_inventory_slots()
        return slots

    @staticmethod
    def _inventory_distinct(slots: list[InventorySlot]) -> set[int]:
        return {abs(slot.vnum) for slot in slots if slot.vnum != 0}

    def _update_bazar_inventory_info(self) -> None:
        """Refresh the Items summary after an inventory/config change."""
        if self.bazar_items_view is not None:
            # A name load or cache merge can add the whole DB catalog; the
            # virtualized rebuild is cheap and keeps unsaved editor values
            # (the selection restore does not refill the form).
            self._bazar_render_items()
            return
        if self.bazar_price_all_button is not None:
            self.bazar_price_all_button.set_sensitive(
                bool(self._world_inventory_slots())
            )

    def _maybe_load_inventory_cache(self) -> bool:
        """Use this character's last complete inventory when nothing is live."""
        if (
            not self._inventory_seeded
            or self._inventory_cache_loaded
            or self._inventory_core_seen
        ):
            return False
        char_id = self.engine.world.self_id
        if not char_id:
            return False
        self._inventory_cache_loaded = True
        try:
            cached = load_inventory_cache(
                inventory_cache_path(char_id), char_id
            )
        except Exception:  # noqa: BLE001 - the cache is optional
            cached = None
        if cached is None or not cached[0]:
            return False
        bags, saved_at = cached
        # A full bag dump from this session is newer than the snapshot; keep
        # it whole. A bag known only from ivn updates is merged slot by slot,
        # so the snapshot fills the slots the host never saw.
        merged = {bag: dict(slots) for bag, slots in bags.items()}
        for bag, live_slots in self.engine.world.inventory.items():
            if bag in self._inventory_full_bags:
                merged[bag] = dict(live_slots)
            else:
                merged.setdefault(bag, {}).update(live_slots)
        self.engine.world.inventory = merged
        self._inventory_cache_dirty = False
        self._inventory_full_bags.clear()
        self._inventory_core_seen = True
        self._inventory_cache_note = (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(saved_at))
            if saved_at > 0
            else "unknown time"
        )
        self._bazar_log(
            f"inventory: cached snapshot from "
            f"{self._inventory_cache_note}"
        )
        self._update_bazar_inventory_info()
        if self._inventory_list is not None:
            self._inventory_schedule_render()
        return True

    def _save_inventory_cache(self, force: bool = False) -> None:
        if not self._inventory_core_seen:
            return
        if not force and not self._inventory_cache_dirty:
            return
        char_id = self.engine.world.self_id
        if not char_id or not self._world_inventory_slots():
            return
        try:
            save_inventory_cache(
                self.engine.world.inventory,
                char_id,
                inventory_cache_path(char_id),
            )
        except Exception as exc:  # noqa: BLE001 - never break the GUI
            self._bazar_log(f"inventory cache: {exc}")
            return
        self._inventory_cache_dirty = False

    def _schedule_inventory_cache_save(self) -> None:
        if self._inventory_cache_source is not None or self._closing:
            return
        self._inventory_cache_source = GLib.timeout_add(
            2000, self._inventory_cache_flush
        )

    def _inventory_cache_flush(self) -> bool:
        self._inventory_cache_source = None
        self._save_inventory_cache()
        return False

    def _ensure_item_info_loaded(self, after=None) -> None:
        """Resolve item names once, off the GTK loop, then run ``after``.

        A completed lookup with no data retries on a later page show or
        Inventory section switch after ``ITEM_INFO_RETRY_S``. A non-empty
        lookup is otherwise final; after rebuilding a stale database use the
        Configuration section's **Reload names** button
        (:meth:`_bazar_reload_names`), which drops the latch explicitly.
        """
        if self.item_info_loaded and self.item_info:
            if after is not None:
                after()
            return
        if self._item_info_loading:
            if after is not None:
                self._item_info_callbacks.append(after)
            return
        if self.item_info_loaded:
            now = time.monotonic()
            if now < self._item_info_retry_at:
                if after is not None:
                    after()
                return
            self.item_info_loaded = False
        if after is not None:
            self._item_info_callbacks.append(after)
        self._item_info_loading = True
        self._item_info_retry_at = time.monotonic() + ITEM_INFO_RETRY_S

        def work() -> None:
            info: dict[int, ItemInfo] = {}
            # The local SQLite DB is already built and resolves all item
            # names in milliseconds. Fall back to Item.dat only when the DB
            # is missing or empty.
            try:
                from . import database

                if database.DEFAULT_DB_PATH.is_file():
                    connection = database.connect()
                    try:
                        info = database.item_info(connection)
                    finally:
                        connection.close()
            except Exception:  # noqa: BLE001 - names are optional
                info = {}
            if not info:
                try:
                    info = load_item_info()
                except Exception:  # noqa: BLE001 - names are optional
                    info = {}
            GLib.idle_add(self._finish_item_info_load, info)

        threading.Thread(target=work, daemon=True).start()

    def _bazar_reload_names(self) -> None:
        """Drop the name-cache latch and look item names up again.

        A non-empty lookup is normally final, so a database rebuilt after the
        first load (e.g. one that was missing a vnum) would otherwise need a
        GUI restart. This is the explicit retry for that case.
        """
        if self._item_info_loading:
            self._bazar_log("item names are still loading")
            return
        self.item_info_loaded = False
        self._item_info_retry_at = 0.0
        self._bazar_log("reloading item names…")
        self._ensure_item_info_loaded()

    def _finish_item_info_load(self, info: dict[int, ItemInfo]) -> bool:
        if self._closing:
            return False
        had_names = bool(self.item_info)
        if info:
            self.item_info = info
        elif not self.item_info:
            self.item_info = {}
        # A failed explicit reload keeps the names already on screen.
        self.item_info_loaded = True
        self._item_info_loading = False
        if not info and had_names:
            self._bazar_log(
                "item names: reload found no source; keeping the names "
                "already shown"
            )
        elif not self.item_info:
            self._bazar_log(
                "inventory: item names unavailable; set NTPL_NSGTD or run: "
                "PYTHONPATH=linux/host python3 -m ntpl db build"
            )
        callbacks = self._item_info_callbacks
        self._item_info_callbacks = []
        # The Monitor table and both item lists show the real item names;
        # replace the shortcuts as soon as the lookup finishes. The lists are
        # refreshed in place: rebuilding them would re-select the current row
        # and overwrite unsaved editor values (and reset scroll positions).
        if self.bazar_results_box is not None:
            self._bazar_render_results()
        self._bazar_refresh_item_labels()
        self._update_bazar_inventory_info()
        for callback in callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001 - never break the load
                pass
        return False

    def _item_name_map(self) -> dict[int, str]:
        """Real Item.dat names, as ``{vnum: name}`` for the name matching."""
        return {
            vnum: info.name
            for vnum, info in getattr(self, "item_info", {}).items()
            if info.name
        }

    def _item_name_for(self, vnum: int) -> str:
        info = self.item_info.get(abs(vnum))
        if info is not None and info.name:
            return info.name
        return f"item {abs(vnum)}"

    def _bazar_vnum_name(self, vnum: int) -> str | None:
        """The real Item.dat name of one vnum, or None while unresolved.

        The controller uses this to keep a row's price on the item the row
        displays: search groups saved by older configs can mix different
        items that share a NosMall base vnum.  A placeholder name (``item
        2174``) counts as unknown, so it never becomes a match key.
        """
        value = abs(int(vnum))
        info = getattr(self, "item_info", {}).get(value)
        if info is None or not info.name:
            return None
        name = info.name.strip()
        if not name or name == f"item {value}":
            return None
        return name

    def _bazar_item_label(self, item: BazarItem) -> str:
        """The real item name for a configured bazaar item.

        ``BazarItem.name`` is the owner's shortcut ("bubbl", "Wings of f");
        the Monitor table and every read-only list show the Item.dat name of
        the item's vnum instead.  The exact item vnum wins over the search
        group; a group falls back through its vnums until one resolves.  The
        configured label is only the last resort, so no row is ever blank.
        """
        candidates: list[int] = []
        if item.vnum:
            candidates.append(abs(int(item.vnum)))
        for vnum in item.vnums:
            value = abs(int(vnum))
            if value and value not in candidates:
                candidates.append(value)
        placeholder = ""
        for vnum in candidates:
            info = getattr(self, "item_info", {}).get(vnum)
            if info is None or not info.name:
                continue
            if info.name != f"item {vnum}":
                return info.name
            placeholder = placeholder or info.name
        return placeholder or item.name

    @staticmethod
    def _bazar_item_tooltip(item: BazarItem) -> str:
        """Configured label and search vnums, for a row's hover text."""
        parts = [f"Configured as: {item.name}"]
        vnums = item.vnums
        if vnums:
            parts.append("Search vnums: " + " ".join(str(v) for v in vnums))
        elif item.vnum:
            parts.append(f"Item vnum: {item.vnum}")
        return "\n".join(parts)

    def _item_npc_price(self, vnum: int) -> int:
        info = self.item_info.get(abs(vnum))
        return 0 if info is None else max(0, info.npc_price)

    def _open_inventory_picker(self) -> None:
        """Show the Items section (the old inventory picker entry point)."""
        self._bazar_show_section("items")
        self._ensure_item_info_loaded()
        if self.bazar_items_view is not None:
            self._bazar_render_items()

    def _inventory_reload(self) -> None:
        """Read the log again, refreshing slots missed before the attach."""
        self._bazar_seed_from_log(force=True)
        self._update_bazar_inventory_info()
        self._inventory_render()

    def _inventory_render(self) -> None:
        """Repaint the unified Items list (kept for the old callers).

        The Inventory section and the Items editor are one list now: every
        configured row plus every inventory template, with Monitor/Relist
        ticks. Inventory data changes call this; it repaints all of it.
        """
        self._bazar_render_items()

    @staticmethod
    def _inventory_restore_scroll(
        adjustment: Gtk.Adjustment, value: float
    ) -> bool:
        top = adjustment.get_upper() - adjustment.get_page_size()
        adjustment.set_value(min(max(value, 0.0), max(0.0, top)))
        return False

    def _inventory_checked_slots(self) -> list[InventorySlot]:
        if not self._inventory_checks:
            return []
        return [
            slot
            for slot in self._inventory_slots()
            if (slot.bag, slot.index) in self._inventory_checks
        ]

    def _inventory_schedule_render(self) -> None:
        if self._inventory_render_pending:
            return
        self._inventory_render_pending = True
        GLib.idle_add(self._inventory_idle_render)

    def _inventory_idle_render(self) -> bool:
        self._inventory_render_pending = False
        if not self._closing and self._inventory_list is not None:
            self._inventory_render()
        return False

    def _inventory_add(
        self, slots: list[InventorySlot], start: bool = False
    ) -> list[BazarItem]:
        """Add the distinct items in ``slots`` to the bazaar item list.

        Returns the newly created items (empty when everything was already
        tracked); the Relist setup uses that to select the right row.
        """
        if not slots:
            self._bazar_log("inventory: no slots to add")
            return []
        if not self.item_info_loaded:
            if self._inventory_status is not None:
                self._inventory_status.set_text("Loading item names…")
            self._ensure_item_info_loaded(
                lambda: self._inventory_add(slots, start)
            )
            return []
        existing = list(self.bazar.config.items)
        names = {
            vnum: info.name
            for vnum, info in self.item_info.items()
            if info.name
        }
        npc_prices = {
            vnum: info.npc_price
            for vnum, info in self.item_info.items()
            if info.npc_price > 0
        }
        plan = inventory_search_items(
            slots,
            names,
            existing,
            self._nosmall_costs(),
            npc_prices,
        )
        created = list(plan.items)
        if plan.items:
            self._bazar_set_items(existing + plan.items)
            self._bazar_save_config(quiet=True)
        self._bazar_log(
            f"inventory: {len(plan.items)} item(s) added · "
            f"{plan.tracked} already tracked"
        )
        self._update_bazar_inventory_info()
        if self._inventory_list is not None:
            self._inventory_render()
        if self._inventory_status is not None:
            # After the re-render: the render writes the live summary and
            # would otherwise hide the add result.
            self._inventory_status.set_text(
                f"Added {len(plan.items)} · {plan.tracked} already tracked · "
                f"{len(self.bazar.config.items)} configured"
            )
        if start:
            self._bazar_start_monitor()
        return created

    def _inventory_set_up_relist(self) -> None:
        """Add one checked inventory slot and open its relist settings."""
        slots = self._inventory_checked_slots()
        if len(slots) != 1:
            self._bazar_log(
                "select exactly one inventory item to set up relisting"
            )
            return
        slot = slots[0]
        if slot.vnum <= 0:
            self._bazar_log(
                "bound/limited item (negative vnum) cannot be relisted"
            )
            return
        if slot.bag not in (0, 1, 2):
            self._inventory_add([slot])
            self._bazar_log(
                f"bag {slot.bag} is not a relistable tab; item added as "
                "monitor-only"
            )
            self._bazar_show_section("items")
            return
        if not self.item_info_loaded:
            self._ensure_item_info_loaded(self._inventory_set_up_relist)
            return
        created = self._inventory_add([slot])
        item = (
            created[0]
            if created
            else self._bazar_item_by_vnum(abs(slot.vnum))
        )
        if item is None:
            self._bazar_log("could not add the item to the bazaar list")
            return
        vnum = abs(slot.vnum)
        quantity = int(getattr(slot, "amount", slot.quantity) or 0)
        amount = min(1000, max(1, quantity))
        changed = False
        # Set up relist is an explicit "put this in the relist set" action;
        # a disabled item gets switched back on, otherwise the pass would
        # never search it.
        if not item.relist:
            item.relist = True
            changed = True
        if not item.enabled:
            item.enabled = True
            changed = True
        if item.vnum != vnum:
            item.vnum = vnum
            changed = True
        if item.inv_tab != slot.bag:
            item.inv_tab = slot.bag
            changed = True
        if item.amount != amount:
            item.amount = amount
            changed = True
        if changed:
            self._bazar_set_items(list(self.bazar.config.items))
            self._bazar_save_config(quiet=True)
        self._bazar_render_relist_items()
        self._bazar_select_relist_item(item.name)
        self._bazar_show_section("relist")
        self._bazar_update_relist_info()
        if item.min_price <= 0:
            self._bazar_log(
                f"{self._bazar_item_label(item)}: set a positive Min price, "
                "then Relist Start"
            )
            if self.bazar_relist_form:
                self.bazar_relist_form["min"].grab_focus()
        else:
            self._bazar_log(
                f"{self._bazar_item_label(item)}: relist settings ready "
                f"(floor {item.min_price:,})"
            )

    def _bazar_item_by_vnum(self, vnum: int) -> BazarItem | None:
        target = abs(int(vnum))
        names = self._item_name_map()
        for item in self.bazar.config.items:
            if target in tracked_vnums([item], names):
                return item
        return None

    def _bazar_item_by_name(self, name: str | None) -> BazarItem | None:
        if not name:
            return None
        for item in self.bazar.config.items:
            if item.name == name:
                return item
        return None

    def _bazar_copy_label_for(self, name: str | None) -> str:
        """The name the item row's right-click menu copies."""
        item = self._bazar_item_by_name(name)
        return (name or "") if item is None else self._bazar_item_label(item)

    def _bazar_price_inventory(self) -> None:
        """Add every inventory item and start the monitor."""
        slots = self._inventory_slots()
        if not slots:
            self._bazar_log(
                "inventory is empty; relog or open the inventory once"
            )
            self._update_bazar_inventory_info()
            return
        self._inventory_add(slots, start=True)

    def _bazar_on_packet(self, packet: Packet) -> None:
        if packet.opcode in ("c_map", "mapout"):
            # Entity ids belong to a map instance. Drop an auto-filled id so
            # the controller uses the new map's Bibi (or the user reopens).
            if (
                self._bazar_npc_autofilled
                and self.bazar_npc_entry is not None
            ):
                self.bazar_npc_entry.set_text("")
                self._bazar_npc_autofilled = False
                self._bazar_npc_autofill_value = ""
            return
        if packet.opcode in ("inv", "ivn"):
            if packet.opcode == "inv":
                try:
                    bag = int(packet.args[0])
                except (IndexError, ValueError):
                    bag = None
                if bag is not None:
                    self._inventory_full_bags.add(bag)
                # Only the login burst (bags 0, 1 and 2) makes the whole
                # inventory live; one updated bag must not hide that the
                # rest came from the cached snapshot.
                if {0, 1, 2} <= self._inventory_full_bags:
                    self._inventory_core_seen = True
                    self._inventory_cache_note = ""
            self._inventory_cache_dirty = True
            self._schedule_inventory_cache_save()
            self._update_bazar_inventory_info()
            if self._inventory_list is not None:
                self._inventory_schedule_render()
        if packet.opcode in (
            "rc_blist", "c_blist", "c_slist", "npc_req", "n_run", "in",
            "wopen", "inv", "ivn",
        ):
            self._bazar_sync_from_world()

    def _bazar_sync_from_world(self) -> None:
        world = self.engine.world
        # The login `in 1 <name> - <charId>` can be older than the DLL log
        # tail read at attach. Once the self id is known, look through the
        # whole (16 MB-capped) log for the name; the party/raid list also
        # feeds WorldState.player_names live.
        if (
            not world.self_name
            and world.self_id
            and not self._self_name_scanned
            and self.log_file
        ):
            self._self_name_scanned = True
            name = load_self_name(world.self_id, self.log_file)
            if name:
                world.player_names[world.self_id] = name
        if (
            self.bazar_character_entry is not None
            and not self._bazar_character_autofilled
            and not self.bazar_character_entry.get_text().strip()
            and world.self_name
        ):
            self.bazar_character_entry.set_text(world.self_name)
            self._bazar_character_autofilled = True
        if (
            self.bazar_npc_entry is not None
            and world.bazar.npc_id is not None
        ):
            current = self.bazar_npc_entry.get_text().strip()
            if not current or (
                self._bazar_npc_autofilled
                and current == self._bazar_npc_autofill_value
            ):
                value = str(world.bazar.npc_id)
                self.bazar_npc_entry.set_text(value)
                self._bazar_npc_autofilled = True
                self._bazar_npc_autofill_value = value
        self._bazar_update_relist_info()

    def _bazar_refresh_item_labels(self) -> None:
        """Update the Relist and Monitor lists' names in place.

        The Items catalog is a virtualized ColumnView; it is rebuilt once
        the name load finishes instead of having its rows patched.
        Rebuilding the other lists would re-select the remembered row and
        overwrite unsaved editor values, so they are updated in place.
        """
        items = {item.name: item for item in self.bazar.config.items}
        for listbox in (
            self.bazar_relist_list,
            self.bazar_monitor_items_list,
        ):
            if listbox is None:
                continue
            child = listbox.get_first_child()
            while child is not None:
                item = items.get(getattr(child, "item_name", None))
                label = getattr(child, "name_label", None)
                if item is not None and label is not None:
                    label.set_text(self._bazar_item_label(item))
                    label.set_tooltip_text(self._bazar_item_tooltip(item))
                child = child.get_next_sibling()
        item = items.get(self.bazar_relist_selected)
        if item is not None and self.bazar_relist_editor_label is not None:
            searches = " ".join(str(v) for v in item.vnums) or "no search"
            self.bazar_relist_editor_label.set_text(
                f"{self._bazar_item_label(item)} · search {searches}"
            )
        # The Items catalog is virtualized; rebuild it from the new names.
        # The selection restore does not refill the editor, so unsaved
        # values survive.
        if self.bazar_items_view is not None:
            self._bazar_render_items()

    @staticmethod
    def _bazar_representative_slot(
        slots: list[InventorySlot],
    ) -> InventorySlot:
        """The slot a row displays/uses: positive vnum, Main, Etc, Equip."""
        bag_rank = {1: 0, 2: 1, 0: 2}
        return min(
            slots,
            key=lambda slot: (
                0 if slot.vnum > 0 else 1,
                bag_rank.get(slot.bag, 3),
                slot.index,
            ),
        )

    @staticmethod
    def _bazar_locations(slots: list[InventorySlot]) -> str:
        if not slots:
            return ""
        parts = [
            f"{BAG_LABELS.get(slot.bag, f'Bag {slot.bag}')} {slot.index} "
            f"×{slot.amount}"
            for slot in slots[:3]
        ]
        if len(slots) > 3:
            parts.append(f"+{len(slots) - 3}")
        return ", ".join(parts)

    def _bazar_inventory_groups(self) -> dict[int, list[InventorySlot]]:
        """abs vnum -> its live slots, in bag/slot order."""
        groups: dict[int, list[InventorySlot]] = {}
        for slot in self._inventory_slots():
            if slot.vnum == 0:
                continue
            groups.setdefault(abs(slot.vnum), []).append(slot)
        return groups

    @staticmethod
    def _bazar_row_vnum(item: BazarItem | None) -> int | None:
        if item is None:
            return None
        if item.vnum:
            return abs(int(item.vnum))
        for vnum in item.vnums:
            if vnum:
                return abs(int(vnum))
        return None

    @staticmethod
    def _bazar_row_targets(rows) -> list[tuple]:
        return [
            (
                getattr(row, "item", None),
                getattr(row, "vnum", None),
                getattr(row, "slot", None),
            )
            for row in rows
        ]

    def _bazar_selected_rows(self) -> list:
        store = self.bazar_items_store
        selection = self.bazar_items_selection
        if store is None or selection is None:
            return []
        rows = []
        for position in range(store.get_n_items()):
            if selection.is_selected(position):
                row = store.get_item(position)
                if row is not None:
                    rows.append(row)
        return rows

    def _bazar_selected_targets(self) -> list[tuple]:
        return self._bazar_row_targets(self._bazar_selected_rows())

    def _bazar_item_from_vnum(
        self, vnum: int, names: dict[int, str] | None = None
    ) -> BazarItem:
        """A monitor-only item for a DB row that is not configured yet."""
        value = abs(int(vnum))
        info = self.item_info.get(value)
        base = (info.name if info is not None else None) or f"item {value}"
        used = {item.name for item in self.bazar.config.items}
        candidate = base[:80]
        counter = 2
        while candidate in used:
            candidate = (
                f"{base[:72]} [{value}]"
                if counter == 2
                else f"{base[:64]} [{value}/{counter}]"
            )[:80]
            counter += 1
        cost = self._nosmall_costs().get(value)
        npc = info.npc_price if info is not None else 0
        return BazarItem(
            name=candidate,
            search_packet=build_search_packet([value]),
            vnum=value,
            inv_tab=None,
            amount=5,
            min_price=0,
            nos_cost=float(cost) if cost is not None else None,
            npc_cost=(
                float(npc) if cost is None and npc > 0 else None
            ),
            enabled=True,
            relist=False,
        )

    def _bazar_draft_from_vnum(
        self, vnum: int, slot: InventorySlot | None = None
    ) -> BazarItem | None:
        names = {
            key: info.name
            for key, info in self.item_info.items()
            if info.name
        }
        if slot is not None:
            npc_prices = {
                key: info.npc_price
                for key, info in self.item_info.items()
                if info.npc_price > 0
            }
            plan = inventory_search_items(
                [slot], names, [], self._nosmall_costs(), npc_prices
            )
            if plan.items:
                return plan.items[0]
        return self._bazar_item_from_vnum(vnum, names)

    def _bazar_edit_row(self, row) -> None:
        if getattr(row, "item", None) is not None:
            item = row.item
            self.bazar_selected_name = item.name
            self._bazar_selected_slot_vnum = None
            self._bazar_form_from_item(item)
            return
        vnum = getattr(row, "vnum", None)
        if vnum is None:
            return
        draft = self._bazar_draft_from_vnum(vnum, getattr(row, "slot", None))
        if draft is None:
            return
        self._bazar_selected_slot_vnum = vnum
        self._bazar_form_from_item(draft)
        # Saving the draft creates a new item.
        self.bazar_selected_name = None

    def _bazar_items_selection_changed(
        self, selection: Gtk.MultiSelection, position: int, n_items: int
    ) -> None:
        if n_items == 0 or position == Gtk.INVALID_LIST_POSITION:
            return
        if not selection.is_selected(position):
            return
        if self._bazar_restoring_selection:
            return
        store = self.bazar_items_store
        row = store.get_item(position) if store is not None else None
        if row is not None:
            self._bazar_edit_row(row)

    def _bazar_items_filter_mode(self) -> str:
        """The Items view filter: all rows, monitored only, or relist only."""
        dropdown = self.bazar_items_filter
        if dropdown is None:
            return "all"
        return {
            0: "all",
            1: "monitored",
            2: "relist",
        }.get(dropdown.get_selected(), "all")

    def _bazar_catalog_rows(self, needle: str) -> tuple[list, int]:
        """Build the filtered catalog rows; returns ``(rows, total)``."""
        items = list(self.bazar.config.items)
        names = self._item_name_map()
        groups = self._bazar_inventory_groups()
        costs = self._nosmall_costs()
        mode = self._bazar_items_filter_mode()
        lookup: dict[int, BazarItem] = {}
        for item in items:
            for vnum in tracked_vnums([item], names):
                lookup.setdefault(vnum, item)
            vnum = self._bazar_row_vnum(item)
            if vnum is not None:
                lookup.setdefault(vnum, item)
        rows: list[BazarItemRowView] = []
        total = 0
        seen_items: set[int] = set()

        def add_row(item, vnum, slot, group) -> None:
            nonlocal total
            total += 1
            if mode == "monitored" and not (
                item is not None and item.enabled
            ):
                return
            if mode == "relist" and not (
                item is not None and item.enabled and item.relist
            ):
                return
            if item is not None:
                display = self._bazar_item_label(item)
                tooltip = self._bazar_item_tooltip(item)
                can_relist = True
            else:
                display = self._item_name_for(vnum or 0)
                tooltip = (
                    f"{display} · vnum {vnum}"
                    + (f" · {self._bazar_locations(group)}" if group else "")
                )
                can_relist = bool(
                    slot is not None
                    and slot.vnum > 0
                    and slot.bag in (0, 1, 2)
                ) or (slot is None and (vnum or 0) > 0)
            location = self._bazar_locations(group)
            if not location:
                location = "not in inventory" if item is not None else "—"
            if item is not None:
                value, _unit = item.cost()
                cost_text = item.cost_text() if value is not None else "—"
            else:
                cost_text = "—"
                info = self.item_info.get(abs(int(vnum))) if vnum else None
                known = costs.get(abs(int(vnum))) if vnum else None
                if known is not None:
                    cost_text = f"{known:g} ND"
                elif info is not None and info.npc_price > 0:
                    cost_text = f"{info.npc_price:,} g"
            haystack = " ".join(
                (
                    display,
                    str(vnum or ""),
                    item.name if item is not None else "",
                    location,
                )
            ).casefold()
            if needle and needle not in haystack:
                return
            rows.append(
                BazarItemRowView(
                    vnum,
                    item,
                    slot,
                    display,
                    location,
                    cost_text,
                    can_relist,
                    tooltip,
                )
            )

        for vnum in self.item_info:
            if vnum <= 0:
                continue
            item = lookup.get(vnum)
            if item is not None:
                seen_items.add(id(item))
            group = groups.get(vnum, [])
            slot = (
                self._bazar_representative_slot(group) if group else None
            )
            add_row(item, vnum, slot, group)
        covered = set(lookup)
        for item in items:
            if id(item) in seen_items:
                continue
            vnum = self._bazar_row_vnum(item)
            group = [
                live
                for tracked in tracked_vnums([item], names)
                for live in groups.get(tracked, [])
            ]
            add_row(
                item,
                vnum,
                self._bazar_representative_slot(group) if group else None,
                group,
            )
        for vnum, group in groups.items():
            if vnum in self.item_info or vnum in covered:
                continue
            add_row(
                None,
                vnum,
                self._bazar_representative_slot(group),
                group,
            )
        rows.sort(
            key=lambda row: (row.display_name.casefold(), row.vnum or 0)
        )
        return rows, total

    def _bazar_render_items(self) -> None:
        """The Items catalog: every DB item plus configured/inventory extras.

        A ColumnView keeps the 7,805 rows cheap; the Monitor/Relist ticks
        are the first two columns and the selected row opens the editor.
        """
        if self.bazar_items_view is None:
            return
        adjustment = None
        scroll_position = 0.0
        if self.bazar_items_scroll is not None:
            adjustment = self.bazar_items_scroll.get_vadjustment()
            scroll_position = adjustment.get_value()
        needle = ""
        if self._inventory_search is not None:
            needle = self._inventory_search.get_text().strip().casefold()
        rows, total = self._bazar_catalog_rows(needle)
        store = self.bazar_items_store
        store.splice(0, store.get_n_items(), rows)
        slots = self._inventory_slots()
        groups = self._bazar_inventory_groups()
        self._bazar_update_items_info(total, store.get_n_items(), slots, groups, needle)
        self._inventory_matches = [
            slot
            for slot in slots
            if not needle
            or needle
            in (
                f"{self._item_name_for(slot.vnum)} {slot.vnum} "
                f"{abs(slot.vnum)} {slot.bag} {slot.index} "
                f"{BAG_LABELS.get(slot.bag, '')}"
            ).casefold()
        ]
        selection = self.bazar_items_selection
        selection.unselect_all()
        target = self._bazar_selected_position(rows)
        if target is not None:
            self._bazar_restoring_selection = True
            try:
                selection.select_item(target, True)
            finally:
                self._bazar_restoring_selection = False
        if adjustment is not None:
            GLib.idle_add(
                self._inventory_restore_scroll, adjustment, scroll_position
            )

    def _bazar_selected_position(self, rows: list) -> int | None:
        if self.bazar_selected_name is not None:
            for position, row in enumerate(rows):
                if (
                    row.item is not None
                    and row.item.name == self.bazar_selected_name
                ):
                    return position
            return None
        if self._bazar_selected_slot_vnum is not None:
            for position, row in enumerate(rows):
                if (
                    row.item is None
                    and row.vnum == self._bazar_selected_slot_vnum
                ):
                    return position
        return None

    def _bazar_update_items_info(
        self,
        total: int,
        shown: int,
        slots: list[InventorySlot],
        groups: dict[int, list[InventorySlot]],
        needle: str,
    ) -> None:
        label = self.bazar_items_info
        if label is None:
            return
        items = self.bazar.config.items
        monitored = sum(1 for item in items if item.enabled)
        relisting = sum(
            1 for item in items if item.enabled and item.relist
        )
        text = (
            f"{len(slots)} slot(s) · {len(groups)} owned · "
            f"{monitored} monitored · {relisting} relisting"
        )
        mode = self._bazar_items_filter_mode()
        narrowed = bool(needle) or mode != "all"
        text += (
            f" · {shown}/{total} items" if narrowed else f" · {total} items"
        )
        if mode == "monitored":
            text += " · monitored only"
        elif mode == "relist":
            text += " · relisting only"
        if not slots:
            text += (
                " · inventory not received: relog or change channel with "
                "this window open"
                if not self._inventory_log_truncated
                else " · DLL log truncated before the login inventory"
            )
        else:
            if self._inventory_cache_note and self._inventory_core_seen:
                text += f" · cached {self._inventory_cache_note}"
            if not self._inventory_core_seen:
                text += (
                    " · partial inventory: the login dump is missing; "
                    "relog with this window open"
                )
        if self._item_info_loading:
            text = "Loading item names… · " + text
        label.set_text(text)
        if self.bazar_price_all_button is not None:
            self.bazar_price_all_button.set_sensitive(bool(slots))

    def _bazar_row_flag_toggled(
        self,
        button: Gtk.CheckButton,
        field: str,
        item: BazarItem | None,
        vnum: int | None,
        slot: InventorySlot | None,
    ) -> None:
        GLib.idle_add(
            self._bazar_apply_row_flags,
            [(item, vnum, slot)],
            field,
            button.get_active(),
        )

    def _bazar_item_row_toggled(
        self, button: Gtk.CheckButton, row, field: str
    ) -> None:
        config_field = "enabled" if field == "monitor" else "relist"
        GLib.idle_add(
            self._bazar_apply_row_flags,
            [(row.item, row.vnum, row.slot)],
            config_field,
            button.get_active(),
        )

    def _bazar_apply_row_flags(
        self, targets: list[tuple], field: str, active: bool
    ) -> bool:
        """Set the Monitor/Relist flag for rows, creating items as needed."""
        if field not in ("enabled", "relist"):
            return False
        items = list(self.bazar.config.items)
        names = {
            vnum: info.name
            for vnum, info in self.item_info.items()
            if info.name
        }
        lookup: dict[int, BazarItem] = {}
        for candidate in items:
            for tracked_vnum in tracked_vnums([candidate], names):
                lookup.setdefault(tracked_vnum, candidate)
            vnum = self._bazar_row_vnum(candidate)
            if vnum is not None:
                lookup.setdefault(vnum, candidate)
        missing: dict[int, InventorySlot | None] = {}
        for item, vnum, slot in targets:
            if item is None and vnum is not None and active:
                value = abs(int(vnum))
                if value not in lookup:
                    missing.setdefault(value, slot)
        if missing and not self.item_info_loaded:
            self._ensure_item_info_loaded(
                lambda: self._bazar_apply_row_flags(
                    targets, field, active
                )
            )
            return False
        created: list[BazarItem] = []
        for vnum, slot in missing.items():
            item = None
            if slot is not None:
                npc_prices = {
                    key: info.npc_price
                    for key, info in self.item_info.items()
                    if info.npc_price > 0
                }
                plan = inventory_search_items(
                    [slot],
                    names,
                    items,
                    self._nosmall_costs(),
                    npc_prices,
                )
                if plan.items:
                    item = plan.items[0]
            if item is None:
                item = self._bazar_item_from_vnum(vnum, names)
            items.append(item)
            created.append(item)
            lookup.setdefault(vnum, item)
        changed = 0
        for item, vnum, slot in targets:
            target = item
            if target is None and vnum is not None:
                target = lookup.get(abs(int(vnum)))
            if target is None:
                continue
            if field == "enabled":
                if target.enabled != active:
                    target.enabled = active
                    changed += 1
                continue
            if target.relist != active:
                target.relist = active
                changed += 1
            if active:
                if not target.enabled:
                    target.enabled = True
                    changed += 1
                if target.min_price <= 0:
                    target.min_price = 1
                    changed += 1
                if (
                    slot is not None
                    and slot.vnum > 0
                    and slot.bag in (0, 1, 2)
                ):
                    value = abs(int(slot.vnum))
                    if target.vnum != value:
                        target.vnum = value
                        changed += 1
                    if target.inv_tab != slot.bag:
                        target.inv_tab = slot.bag
                        changed += 1
                    if target.amount != slot.amount:
                        target.amount = slot.amount
                        changed += 1
        if not created and not changed:
            GLib.idle_add(self._bazar_render_items)
            return False
        if created:
            self._bazar_set_items(items)
        else:
            self._bazar_save_config(quiet=True)
            self._bazar_render_results()
            self._bazar_render_relist_items()
            self._bazar_render_monitor_items()
            self._bazar_update_relist_info()
            GLib.idle_add(self._bazar_render_items)
        label = "monitor" if field == "enabled" else "relist"
        self._bazar_log(
            f"{label}: {changed} item(s) {'on' if active else 'off'}"
        )
        return False

    def _bazar_add_selected_to(self, field: str) -> None:
        targets = self._bazar_selected_targets()
        if not targets:
            self._bazar_log(
                "select one or more Items rows first (Ctrl+click adds to "
                "the selection)"
            )
            return
        self._bazar_apply_row_flags(targets, field, True)

    def _bazar_render_relist_items(self) -> None:
        if self.bazar_relist_list is None:
            return
        # Ticking a row used to rebuild this list, which threw the scroll
        # position back to the top. Every render now keeps it (the same
        # pattern as the Inventory list).
        adjustment = None
        scroll_position = 0.0
        if self.bazar_relist_scroll is not None:
            adjustment = self.bazar_relist_scroll.get_vadjustment()
            scroll_position = adjustment.get_value()
        selected = self.bazar_relist_selected
        child = self.bazar_relist_list.get_first_child()
        while child is not None:
            self.bazar_relist_list.remove(child)
            child = self.bazar_relist_list.get_first_child()
        items = list(self.bazar.config.items)
        if self.bazar_relist_filter is not None and (
            self.bazar_relist_filter.get_active()
        ):
            # Relist Start needs both flags: an item whose Monitor tick is
            # off is skipped by the pass, so it is not in the chosen set.
            items = [
                item for item in items if item.relist and item.enabled
            ]
        if not items:
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            row.set_selectable(False)
            if self.bazar.config.items:
                text = (
                    "No item is in the relist set yet. Tick Relist for an "
                    "item in the Items section, or use Add from Items."
                )
            else:
                text = "No items yet. Add them in the Items section."
            empty = Gtk.Label(label=text)
            empty.set_xalign(0)
            empty.set_wrap(True)
            empty.add_css_class("dim-label")
            row.set_child(empty)
            self.bazar_relist_list.append(row)
            self.bazar_relist_selected = None
            self._bazar_relist_item_selected(self.bazar_relist_list, None)
            self._bazar_update_relist_info()
            if adjustment is not None:
                GLib.idle_add(
                    self._inventory_restore_scroll,
                    adjustment,
                    scroll_position,
                )
            return
        for item in items:
            row = Gtk.ListBoxRow()
            row.item_name = item.name  # type: ignore[attr-defined]
            box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=1
            )
            name = Gtk.Label(label=self._bazar_item_label(item))
            name.set_xalign(0)
            name.set_hexpand(True)
            name.set_tooltip_text(self._bazar_item_tooltip(item))
            row.name_label = name  # type: ignore[attr-defined]
            name.set_wrap(True)
            name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            name.set_max_width_chars(36)
            right = Gtk.GestureClick()
            right.set_button(3)

            def on_right(
                _gesture, _n_press, _x, _y, label=name, key=item.name
            ) -> None:
                # Resolve at click time, like the Items list: a late name
                # load updates the row in place.
                self._bazar_copy_menu(
                    label, self._bazar_copy_label_for(key)
                )

            right.connect("pressed", on_right)
            name.add_controller(right)
            detail = Gtk.Label(label=self._bazar_relist_detail(item))
            detail.set_xalign(0)
            detail.add_css_class("dim-label")
            detail.set_wrap(True)
            detail.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            detail.set_max_width_chars(44)
            box.append(name)
            box.append(detail)
            badge = Gtk.Label(label=self._bazar_relist_badge(item)[0])
            badge.add_css_class(self._bazar_relist_badge(item)[1])
            badge.set_valign(Gtk.Align.START)
            # Kept on the row so a tick can update it in place instead of
            # rebuilding the list under the user's cursor.
            row.detail_label = detail  # type: ignore[attr-defined]
            row.badge_label = badge  # type: ignore[attr-defined]
            # The tick is the item's membership in the relist set; the
            # badge explains why a ticked item still cannot send c_reg.
            track = Gtk.CheckButton()
            track.set_active(bool(item.relist))
            track.set_valign(Gtk.Align.START)
            track.set_tooltip_text(
                "Track this item for relisting. Untick to keep watching "
                "its price without changing the listing; the floor is kept."
            )
            handler = track.connect(
                "toggled",
                lambda button, name=item.name: (
                    self._bazar_relist_tracked_toggled(
                        name, button.get_active()
                    )
                ),
            )
            row.relist_check = track  # type: ignore[attr-defined]
            row.relist_handler = handler  # type: ignore[attr-defined]
            content = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )
            content.set_margin_end(10)
            content.append(track)
            content.append(box)
            # The name to paste into the in-game bazaar search when listing.
            copy = Gtk.Button(label="Copy")
            copy.add_css_class("flat")
            copy.set_valign(Gtk.Align.START)
            copy.set_tooltip_text("Copy the item name")
            copy.connect(
                "clicked",
                lambda _button, key=item.name: self._copy_text(
                    self._bazar_copy_label_for(key)
                ),
            )
            content.append(copy)
            content.append(badge)
            row.set_child(content)
            self.bazar_relist_list.append(row)
        if selected:
            self._bazar_select_relist_item(selected)
        else:
            self._bazar_relist_item_selected(self.bazar_relist_list, None)
        self._bazar_update_relist_info()
        if adjustment is not None:
            GLib.idle_add(
                self._inventory_restore_scroll, adjustment, scroll_position
            )

    @staticmethod
    def _bazar_relist_detail(item: BazarItem) -> str:
        parts = []
        if item.vnum:
            parts.append(f"vnum {item.vnum}")
        if item.inv_tab in (0, 1, 2):
            tab = {0: "Equip", 1: "Main", 2: "Etc"}[item.inv_tab]
            parts.append(tab)
        parts.append(f"amount {item.amount}")
        parts.append(f"min {item.min_price:,}")
        if not item.search_packet:
            parts.append("no search packet")
        if not item.enabled:
            parts.append("disabled")
        return " · ".join(parts)

    @staticmethod
    def _bazar_relist_ready(item: BazarItem) -> bool:
        return bool(
            item.relist
            and item.enabled
            and item.search_packet
            and item.vnum
            and item.vnum > 0
            and item.inv_tab in (0, 1, 2)
            and item.min_price > 0
        )

    def _bazar_relist_badge(self, item: BazarItem) -> tuple[str, str]:
        if not item.relist:
            return "not relisting", "dim-label"
        if not item.enabled:
            return "disabled", "dim-label"
        if not item.search_packet:
            return "no search", "bazar-warn"
        if self._bazar_relist_ready(item):
            return "ready", "bazar-ok"
        if item.vnum and item.inv_tab in (0, 1, 2):
            return "needs floor", "bazar-warn"
        return "needs vnum/tab", "bazar-warn"

    def _bazar_relist_tracked_toggled(self, name: str, tracked: bool) -> None:
        """A row tick: add to or remove from the relist set immediately."""
        item = self._bazar_relist_set_tracked(name, tracked)
        if item is None:
            return
        # Update the row in place; a rebuild here was what threw a scrolled
        # list back to the top.
        self._bazar_relist_refresh_item(item)

    def _bazar_relist_row_by_name(self, name: str):
        if self.bazar_relist_list is None:
            return None
        child = self.bazar_relist_list.get_first_child()
        while child is not None:
            if getattr(child, "item_name", None) == name:
                return child
            child = child.get_next_sibling()
        return None

    def _bazar_relist_refresh_item(self, item: BazarItem) -> None:
        """Rewrite one row's badge/detail without rebuilding the list.

        A rebuild is only needed when the active filter now hides or shows
        the row; that render keeps the scroll position anyway.
        """
        row = self._bazar_relist_row_by_name(item.name)
        if row is not None:
            badge = getattr(row, "badge_label", None)
            if badge is not None:
                for css in ("bazar-ok", "bazar-warn", "dim-label"):
                    badge.remove_css_class(css)
                text, css = self._bazar_relist_badge(item)
                badge.set_text(text)
                badge.add_css_class(css)
            detail = getattr(row, "detail_label", None)
            if detail is not None:
                detail.set_text(self._bazar_relist_detail(item))
            # A button-driven change (Remove from relisting) must move the
            # row's tick too; block the handler so it is not a user action.
            check = getattr(row, "relist_check", None)
            if check is not None and check.get_active() != bool(item.relist):
                handler = getattr(row, "relist_handler", None)
                if handler is not None:
                    check.handler_block(handler)
                check.set_active(bool(item.relist))
                if handler is not None:
                    check.handler_unblock(handler)
        self._bazar_update_relist_info()
        filtered = (
            self.bazar_relist_filter is not None
            and self.bazar_relist_filter.get_active()
        )
        if row is None or (filtered and not item.relist):
            self._bazar_render_relist_items()

    def _bazar_relist_set_tracked(
        self, name: str | None, tracked: bool
    ) -> BazarItem | None:
        """Put one configured item in or out of the relist set.

        Removing keeps the item configured, monitored and its floor intact,
        so re-adding restores the previous setup. Adding re-enables an item
        that was switched off entirely, because tracking it is pointless
        while the Monitor skips it.
        """
        item = self._bazar_item_by_name(name)
        if item is None:
            return None
        if bool(item.relist) == bool(tracked) and (
            not tracked or item.enabled
        ):
            # Re-rendering can emit toggled for the state just set; only a
            # real change is worth a config write and a log line.
            return item
        changed = False
        if item.relist != bool(tracked):
            item.relist = bool(tracked)
            changed = True
        if tracked and not item.enabled:
            item.enabled = True
            changed = True
        if not changed:
            return item
        self._bazar_save_config(quiet=True)
        self._bazar_update_relist_info()
        label = self._bazar_item_label(item)
        if tracked:
            if self._bazar_relist_ready(item):
                self._bazar_log(
                    f"{label}: added to relisting (floor "
                    f"{item.min_price:,})"
                )
            else:
                self._bazar_log(
                    f"{label}: added to relisting; "
                    f"{self._bazar_relist_hint_text(item)}"
                )
        else:
            self._bazar_log(
                f"{label}: removed from relisting (still monitored; "
                "floor kept)"
            )
        return item

    def _bazar_relist_set_tracked_many(
        self, names: list[str], tracked: bool
    ) -> int:
        """Apply one relist action to several items with a single save."""
        changed = 0
        for name in names:
            item = self._bazar_item_by_name(name)
            if item is None:
                continue
            if bool(item.relist) == bool(tracked) and (
                not tracked or item.enabled
            ):
                continue
            item.relist = bool(tracked)
            if tracked:
                item.enabled = True
            changed += 1
        if not changed:
            return 0
        self._bazar_save_config(quiet=True)
        self._bazar_render_relist_items()
        self._bazar_log(
            f"{changed} item(s) "
            + ("added to relisting" if tracked else "removed from relisting")
        )
        return changed

    def _bazar_relist_shown_names(self) -> list[str]:
        if self.bazar_relist_list is None:
            return []
        names: list[str] = []
        child = self.bazar_relist_list.get_first_child()
        while child is not None:
            name = getattr(child, "item_name", None)
            if name:
                names.append(name)
            child = child.get_next_sibling()
        return names

    def _bazar_relist_selected_names(self) -> list[str]:
        if self.bazar_relist_list is None:
            return []
        return [
            name
            for row in self.bazar_relist_list.get_selected_rows()
            if (name := getattr(row, "item_name", None))
        ]

    def _bazar_relist_select_shown(self) -> None:
        if self.bazar_relist_list is None:
            return
        select_all = getattr(self.bazar_relist_list, "select_all", None)
        if select_all is not None:
            select_all()
            return
        child = self.bazar_relist_list.get_first_child()
        while child is not None:
            self.bazar_relist_list.select_row(child)
            child = child.get_next_sibling()

    def _bazar_relist_unselect_all(self) -> None:
        if self.bazar_relist_list is not None:
            self.bazar_relist_list.unselect_all()

    def _bazar_relist_set_all_shown(self, tracked: bool) -> None:
        names = self._bazar_relist_shown_names()
        if not names:
            self._bazar_log("the relist list is empty")
            return
        if not self._bazar_relist_set_tracked_many(names, tracked):
            self._bazar_log(
                "nothing changed: every shown item is already "
                + ("relisting" if tracked else "out of relisting")
            )

    def _bazar_relist_set_selected(self, tracked: bool) -> None:
        names = self._bazar_relist_selected_names()
        if not names:
            self._bazar_log(
                "select one or more rows in the relist list first "
                "(Ctrl+click for more)"
            )
            return
        if not self._bazar_relist_set_tracked_many(names, tracked):
            self._bazar_log("nothing changed for the selected rows")

    def _bazar_relist_remove_selected(self) -> None:
        name = self.bazar_relist_selected
        if not name:
            self._bazar_log("select an item in the relist list first")
            return
        item = self._bazar_relist_set_tracked(name, False)
        if item is not None:
            self._bazar_relist_refresh_item(item)

    def _bazar_relist_add_from_items(self) -> list[BazarItem]:
        """Move the Items section's selection into the relist set."""
        targets = self._bazar_selected_targets()
        if not targets:
            item = self._bazar_item_by_name(self.bazar_selected_name)
            if item is None:
                self._bazar_log("select an item in the Items section first")
                return []
            targets = [(item, self._bazar_row_vnum(item), None)]
        self._bazar_apply_row_flags(targets, "relist", True)
        first = targets[0][0]
        if first is None and targets[0][1] is not None:
            first = self._bazar_item_by_vnum(abs(targets[0][1]))
        if first is not None:
            self._bazar_select_relist_item(first.name)
        return [first] if first is not None else []

    def _bazar_relist_add_whole_inventory(self) -> None:
        """Put every distinct inventory item into the relist set."""
        slots = self._inventory_slots()
        if not slots:
            self._bazar_log(
                "inventory is empty; relog or change channel with this "
                "window open"
            )
            return
        valid = [
            slot
            for slot in slots
            if slot.vnum > 0 and slot.bag in (0, 1, 2)
        ]
        skipped = len(slots) - len(valid)
        if not valid:
            self._bazar_log(
                "no relistable inventory item (bound vnums and bags 6/7 "
                "cannot be relisted)"
            )
            return
        distinct = len({abs(slot.vnum) for slot in valid})
        if skipped:
            self._bazar_log(
                f"{skipped} slot(s) cannot be relisted (bound vnum or "
                "bag 6/7); skipped"
            )
        now = time.monotonic()
        armed = (
            self._bazar_relist_inventory_armed_at > 0.0
            and now - self._bazar_relist_inventory_armed_at <= 5.0
        )
        if not armed:
            self._bazar_relist_inventory_armed_at = now
            self._bazar_log(
                f"click Add whole inventory again to put {distinct} item(s) "
                "into the relist set with a floor of 1 where unset"
            )
            return
        self._bazar_relist_inventory_armed_at = 0.0
        if not self.item_info_loaded:
            self._ensure_item_info_loaded(
                self._bazar_relist_add_whole_inventory
            )
            return
        created = self._inventory_add(valid, start=False)
        # Representative slot per template: Main, then Etc, then Equip.
        groups: dict[int, object] = {}
        for bag in (1, 2, 0):
            for slot in valid:
                if slot.bag == bag:
                    groups.setdefault(abs(slot.vnum), slot)
        changed = bool(created)
        for vnum, slot in groups.items():
            item = self._bazar_item_by_vnum(vnum)
            if item is None:
                continue
            quantity = int(getattr(slot, "amount", slot.quantity) or 0)
            amount = min(1000, max(1, quantity))
            for attribute, value in (
                ("relist", True),
                ("enabled", True),
                ("vnum", vnum),
                ("inv_tab", slot.bag),
                ("amount", amount),
            ):
                if getattr(item, attribute) != value:
                    setattr(item, attribute, value)
                    changed = True
            if item.min_price <= 0:
                item.min_price = 1
                changed = True
        if changed:
            self._bazar_save_config(quiet=True)
        self._bazar_render_items()
        self._bazar_render_relist_items()
        self._bazar_render_monitor_items()
        self._bazar_render_results()
        self._bazar_update_relist_info()
        self._bazar_log(
            f"relist: whole inventory imported · {len(groups)} item(s), "
            f"{len(created)} new · floor 1 where unset"
        )

    def _bazar_relist_add_from_inventory(self) -> list[BazarItem]:
        """Add the Inventory section's ticked rows to the relist set."""
        slots = self._inventory_checked_slots()
        if not slots:
            self._bazar_log(
                "tick one or more inventory rows in the Inventory section "
                "first"
            )
            return []
        valid = [
            slot
            for slot in slots
            if slot.vnum > 0 and slot.bag in (0, 1, 2)
        ]
        skipped = len(slots) - len(valid)
        if skipped:
            self._bazar_log(
                f"{skipped} slot(s) cannot be relisted (bound vnum or "
                "bag 6/7); skipped"
            )
        if not valid:
            return []
        if not self.item_info_loaded:
            # Adding resolves Item.dat names; retry once they arrive.
            self._ensure_item_info_loaded(
                self._bazar_relist_add_from_inventory
            )
            return []
        created = self._inventory_add(valid)
        added: list[BazarItem] = []
        changed = bool(created)
        for slot in valid:
            item = self._bazar_item_by_vnum(abs(slot.vnum))
            if item is None:
                continue
            vnum = abs(slot.vnum)
            quantity = int(getattr(slot, "amount", slot.quantity) or 0)
            amount = min(1000, max(1, quantity))
            for attribute, value in (
                ("relist", True),
                ("enabled", True),
                ("vnum", vnum),
                ("inv_tab", slot.bag),
                ("amount", amount),
            ):
                if getattr(item, attribute) != value:
                    setattr(item, attribute, value)
                    changed = True
            added.append(item)
        if changed:
            self._bazar_save_config(quiet=True)
        self._bazar_render_items()
        self._bazar_render_relist_items()
        self._bazar_update_relist_info()
        if not added:
            return []
        self._bazar_select_relist_item(added[0].name)
        floors = [item for item in added if item.min_price <= 0]
        text = f"{len(added)} item(s) added to relisting"
        if floors:
            text += f"; {len(floors)} still need a Min price"
        self._bazar_log(text)
        return added

    @staticmethod
    def _bazar_relist_hint_text(item: BazarItem) -> str:
        if not item.search_packet:
            return "no search packet; add one in Items"
        if item.vnum is None or item.vnum <= 0:
            return "set the item vnum"
        if item.inv_tab not in (0, 1, 2):
            return "choose Equip, Main or Etc"
        if item.min_price <= 0:
            return "set a positive Min price to enable relisting"
        if item.vnum not in item.vnums:
            return (
                "item vnum is not in the search group; edit Search vnums "
                "in Items or use the exact inventory vnum"
            )
        return f"ready · floor {item.min_price:,} per unit"

    @staticmethod
    def _bazar_monitor_detail(item: BazarItem) -> str:
        search = " ".join(str(vnum) for vnum in item.vnums) or "no search"
        text = f"{search} · {item.cost_text()}"
        if item.relist:
            text += " · relisting"
        return text

    def _bazar_render_monitor_items(self) -> None:
        listbox = self.bazar_monitor_items_list
        if listbox is None:
            return
        adjustment = None
        scroll = 0.0
        if self.bazar_monitor_items_scroll is not None:
            adjustment = self.bazar_monitor_items_scroll.get_vadjustment()
            scroll = adjustment.get_value()
        selected = set(self._bazar_monitor_selected)
        child = listbox.get_first_child()
        while child is not None:
            listbox.remove(child)
            child = listbox.get_first_child()
        items = [item for item in self.bazar.config.items if item.enabled]
        present: set[str] = set()
        for item in items:
            present.add(item.name)
            row = Gtk.ListBoxRow()
            row.item_name = item.name  # type: ignore[attr-defined]
            content = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=6
            )
            check = Gtk.CheckButton()
            check.set_valign(Gtk.Align.START)
            check.set_active(bool(item.enabled))
            check.set_tooltip_text(
                "Search this item during Monitor Start. Unticking keeps it "
                "configured and its floor, but stops monitoring and relisting."
            )
            handler = check.connect(
                "toggled",
                lambda button, name=item.name: (
                    self._bazar_monitor_tracked_toggled(
                        name, button.get_active()
                    )
                ),
            )
            row.monitor_check = check  # type: ignore[attr-defined]
            row.monitor_handler = handler  # type: ignore[attr-defined]
            labels = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=1
            )
            name = Gtk.Label(label=self._bazar_item_label(item))
            name.set_xalign(0)
            name.set_hexpand(True)
            name.set_wrap(True)
            name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            name.set_max_width_chars(32)
            name.set_tooltip_text(self._bazar_item_tooltip(item))
            row.name_label = name  # type: ignore[attr-defined]
            detail = Gtk.Label(label=self._bazar_monitor_detail(item))
            detail.set_xalign(0)
            detail.add_css_class("dim-label")
            detail.set_wrap(True)
            detail.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            detail.set_max_width_chars(38)
            labels.append(name)
            labels.append(detail)
            content.append(check)
            content.append(labels)
            row.set_child(content)
            listbox.append(row)
        selected &= present
        self._bazar_monitor_selected = selected
        child = listbox.get_first_child()
        while child is not None:
            if getattr(child, "item_name", None) in selected:
                listbox.select_row(child)
            child = child.get_next_sibling()
        self._bazar_update_monitor_items_info()
        if adjustment is not None:
            GLib.idle_add(
                self._inventory_restore_scroll, adjustment, scroll
            )

    def _bazar_update_monitor_items_info(self) -> None:
        if self.bazar_monitor_items_info is None:
            return
        monitored = sum(
            1 for item in self.bazar.config.items if item.enabled
        )
        rows = 0
        child = (
            self.bazar_monitor_items_list.get_first_child()
            if self.bazar_monitor_items_list is not None
            else None
        )
        while child is not None:
            rows += 1
            child = child.get_next_sibling()
        self.bazar_monitor_items_info.set_text(
            f"{rows} shown · {monitored} monitored · "
            f"{len(self.bazar.config.items)} configured"
        )

    def _bazar_monitor_tracked_toggled(
        self, name: str, active: bool
    ) -> None:
        item = self._bazar_item_by_name(name)
        if item is None or item.enabled == active:
            return
        item.enabled = active
        self._bazar_save_config(quiet=True)
        self._bazar_update_info()
        self._bazar_render_results()
        self._bazar_update_monitor_items_info()
        # The list shows monitored items only, so both directions repaint.
        GLib.idle_add(self._bazar_render_monitor_items)
        self._bazar_log(
            f"{self._bazar_item_label(item)}: monitoring "
            f"{'on' if active else 'off'}"
            + ("" if active else " (also out of relisting)")
        )

    def _bazar_monitor_selection_changed(
        self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None
    ) -> None:
        if row is not None and getattr(row, "item_name", None):
            self.bazar_monitor_selected_name = row.item_name
        self._bazar_monitor_selected = {
            getattr(selected, "item_name", "")
            for selected in listbox.get_selected_rows()
        }
        self._bazar_monitor_selected.discard("")

    def _bazar_monitor_shown_names(self) -> list[str]:
        names: list[str] = []
        if self.bazar_monitor_items_list is None:
            return names
        child = self.bazar_monitor_items_list.get_first_child()
        while child is not None:
            name = getattr(child, "item_name", None)
            if name:
                names.append(name)
            child = child.get_next_sibling()
        return names

    def _bazar_monitor_selected_names(self) -> list[str]:
        shown = set(self._bazar_monitor_shown_names())
        return [
            name for name in shown if name in self._bazar_monitor_selected
        ]

    def _bazar_monitor_select_shown(self) -> None:
        if self.bazar_monitor_items_list is None:
            return
        self.bazar_monitor_items_list.select_all()
        self._bazar_monitor_selected = set(
            self._bazar_monitor_shown_names()
        )

    def _bazar_monitor_unselect_all(self) -> None:
        if self.bazar_monitor_items_list is not None:
            self.bazar_monitor_items_list.unselect_all()
        self._bazar_monitor_selected.clear()

    def _bazar_monitor_set_many(
        self, names: list[str], active: bool
    ) -> None:
        wanted = set(names)
        changed = 0
        for item in self.bazar.config.items:
            if item.name in wanted and item.enabled != active:
                item.enabled = active
                changed += 1
        if not changed:
            return
        self._bazar_save_config(quiet=True)
        self._bazar_render_monitor_items()
        self._bazar_render_results()
        self._bazar_update_info()
        self._bazar_log(
            f"monitor: {changed} item(s) "
            f"{'on' if active else 'off'}"
        )

    def _bazar_monitor_set_all_shown(self, active: bool) -> None:
        self._bazar_monitor_set_many(
            self._bazar_monitor_shown_names(), active
        )

    def _bazar_monitor_set_selected(self, active: bool) -> None:
        self._bazar_monitor_set_many(
            self._bazar_monitor_selected_names(), active
        )

    def _bazar_monitor_add_from_items(self) -> None:
        targets = self._bazar_selected_targets()
        if not targets:
            item = self._bazar_item_by_name(self.bazar_selected_name)
            if item is None:
                self._bazar_log("select an item in the Items section first")
                return
            targets = [(item, self._bazar_row_vnum(item), None)]
        self._bazar_apply_row_flags(targets, "enabled", True)

    def _bazar_monitor_add_from_inventory(self) -> None:
        slots = self._inventory_checked_slots()
        if not slots:
            self._bazar_log(
                "tick one or more inventory rows in the Inventory section "
                "first"
            )
            return
        self._bazar_monitor_add_slots(slots, "selected inventory rows")

    def _bazar_monitor_add_whole_inventory(self) -> None:
        slots = self._inventory_slots()
        if not slots:
            self._bazar_log(
                "inventory is empty; relog or change channel with this "
                "window open"
            )
            return
        distinct = len(
            {abs(slot.vnum) for slot in slots if slot.vnum != 0}
        )
        now = time.monotonic()
        armed = (
            self._bazar_monitor_inventory_armed_at > 0.0
            and now - self._bazar_monitor_inventory_armed_at <= 5.0
        )
        if not armed:
            self._bazar_monitor_inventory_armed_at = now
            self._bazar_log(
                f"click Add whole inventory again to add and monitor "
                f"{distinct} inventory item(s)"
            )
            return
        self._bazar_monitor_inventory_armed_at = 0.0
        self._bazar_monitor_add_slots(slots, "whole inventory")

    def _bazar_monitor_add_slots(
        self, slots: list, why: str
    ) -> None:
        if not self.item_info_loaded:
            self._ensure_item_info_loaded(
                lambda: self._bazar_monitor_add_slots(slots, why)
            )
            return
        created = self._inventory_add(slots, start=False)
        inventory_vnums = {
            abs(int(slot.vnum)) for slot in slots if int(slot.vnum) != 0
        }
        names = self._item_name_map()
        enabled_more = 0
        for item in self.bazar.config.items:
            if item.enabled:
                continue
            if tracked_vnums([item], names) & inventory_vnums:
                item.enabled = True
                enabled_more += 1
        if enabled_more:
            self._bazar_save_config(quiet=True)
        self._bazar_render_monitor_items()
        self._bazar_render_results()
        self._bazar_update_info()
        self._bazar_update_monitor_items_info()
        self._bazar_log(
            f"monitor: {len(created)} item(s) added, {enabled_more} "
            f"existing row(s) enabled from {why}"
        )

    def _bazar_monitor_open_editor(self) -> None:
        name = self.bazar_monitor_selected_name
        if not name:
            name = next(iter(self._bazar_monitor_selected), None)
        if not name:
            self._bazar_log("select a Monitor row first")
            return
        self._bazar_show_section("items")
        self._bazar_select_item(name)

    def _bazar_relist_pressed(
        self, _gesture, _n_press: int, _x: float, y: float
    ) -> None:
        if self.bazar_relist_list is None:
            return
        row = self.bazar_relist_list.get_row_at_y(int(y))
        selected = self.bazar_relist_list.get_selected_rows()
        self._relist_press_row = row
        self._relist_press_only = bool(
            row is not None
            and row.is_selected()
            and len(selected) == 1
            and selected[0] is row
        )

    def _bazar_relist_row_activated(
        self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow
    ) -> None:
        # A repeated click on the only selected row clears it; clicking an
        # unselected row (or one of several) selects normally.
        if (
            row is self._relist_press_row
            and self._relist_press_only
            and row.is_selected()
            and len(listbox.get_selected_rows()) == 1
        ):
            listbox.unselect_all()
        self._relist_press_row = None
        self._relist_press_only = False

    def _bazar_relist_item_selected(
        self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None
    ) -> None:
        if row is None:
            # In multiple-selection mode this signal also fires when one of
            # several rows is deselected; keep editing a row that remains
            # selected instead of disabling the whole editor.
            remaining = (
                listbox.get_selected_rows() if listbox is not None else []
            )
            if remaining:
                row = remaining[0]
            else:
                self.bazar_relist_selected = None
                if self.bazar_relist_editor_label is not None:
                    self.bazar_relist_editor_label.set_text(
                        "No item selected"
                    )
                if self.bazar_relist_hint is not None:
                    self.bazar_relist_hint.set_text("")
                self._bazar_set_relist_form_sensitive(False)
                return
        name = getattr(row, "item_name", None)
        if not name:
            return
        item = next(
            (
                existing
                for existing in self.bazar.config.items
                if existing.name == name
            ),
            None,
        )
        if item is None:
            return
        self.bazar_relist_selected = item.name
        self._bazar_relist_form_from_item(item)

    def _bazar_relist_form_from_item(self, item: BazarItem) -> None:
        form = self.bazar_relist_form
        if not form:
            return
        form["vnum"].set_text(
            "" if item.vnum is None else str(item.vnum)
        )
        form["tab"].set_selected(
            0 if item.inv_tab is None else item.inv_tab + 1
        )
        form["amount"].set_value(item.amount)
        form["min"].set_value(item.min_price)
        searches = " ".join(str(vnum) for vnum in item.vnums) or "no search"
        if self.bazar_relist_editor_label is not None:
            self.bazar_relist_editor_label.set_text(
                f"{self._bazar_item_label(item)} · search {searches}"
            )
        self._bazar_set_relist_form_sensitive(True)
        if self.bazar_relist_hint is not None:
            self.bazar_relist_hint.set_text(self._bazar_relist_hint_text(item))

    def _bazar_set_relist_form_sensitive(self, enabled: bool) -> None:
        for widget in self.bazar_relist_form.values():
            widget.set_sensitive(bool(enabled))
        if self.bazar_relist_save_button is not None:
            self.bazar_relist_save_button.set_sensitive(bool(enabled))
        if self.bazar_relist_open_button is not None:
            self.bazar_relist_open_button.set_sensitive(bool(enabled))

    def _bazar_select_relist_item(self, name: str | None) -> None:
        if self.bazar_relist_list is None:
            return
        if not name:
            self.bazar_relist_list.unselect_all()
            self._bazar_relist_item_selected(self.bazar_relist_list, None)
            return
        child = self.bazar_relist_list.get_first_child()
        while child is not None:
            if getattr(child, "item_name", None) == name:
                # A programmatic selection is single, even though the list
                # itself supports Ctrl+click groups.
                self.bazar_relist_list.unselect_all()
                self.bazar_relist_list.select_row(child)
                return
            child = child.get_next_sibling()
        self.bazar_relist_selected = None
        self.bazar_relist_list.unselect_all()
        self._bazar_relist_item_selected(self.bazar_relist_list, None)

    def _bazar_relist_save(self) -> None:
        name = self.bazar_relist_selected
        item = next(
            (
                existing
                for existing in self.bazar.config.items
                if existing.name == name
            ),
            None,
        )
        if item is None:
            self._bazar_log("select an item to save its relist settings")
            return
        form = self.bazar_relist_form
        if not form:
            return
        text = form["vnum"].get_text().strip()
        vnum = None
        if text:
            if not text.isdigit() or int(text) <= 0:
                self._bazar_log("item vnum must be a positive number")
                return
            vnum = int(text)
        tab_index = form["tab"].get_selected()
        inv_tab = None if tab_index == 0 else tab_index - 1
        amount = int(form["amount"].get_value())
        minimum = int(form["min"].get_value())
        item.vnum = vnum
        item.inv_tab = inv_tab
        item.amount = amount
        item.min_price = minimum
        if vnum is not None and item.search_packet:
            current = item.vnums
            if len(current) == 1 and current[0] != vnum:
                # A single-vnum search follows the relist target; a group or
                # raw packet is deliberately left alone.
                item.search_packet = build_search_packet([vnum])
        self._bazar_set_items(list(self.bazar.config.items))
        self._bazar_save_config(quiet=True)
        self._bazar_render_relist_items()
        self._bazar_select_relist_item(item.name)
        self._bazar_update_relist_info()
        if minimum <= 0:
            self._bazar_log(
                f"{self._bazar_item_label(item)}: set a positive Min price "
                "to enable relisting"
            )
        elif not item.relist:
            self._bazar_log(
                f"{self._bazar_item_label(item)}: relist settings saved "
                f"(floor {minimum:,} per unit); tick Relist to include it "
                "in the relist set"
            )
        else:
            self._bazar_log(
                f"{self._bazar_item_label(item)}: relist settings saved "
                f"(floor {minimum:,} per unit)"
            )

    def _bazar_open_full_editor(self) -> None:
        name = self.bazar_relist_selected
        self._bazar_show_section("items")
        if name:
            self._bazar_select_item(name)

    def _bazar_select_item(self, name: str) -> None:
        store = self.bazar_items_store
        selection = self.bazar_items_selection
        if store is None or selection is None:
            return
        self._bazar_selected_slot_vnum = None
        selection.unselect_all()
        found = self._bazar_select_item_position(name)
        if found is None and self._inventory_search is not None:
            # The row may be hidden by the search; clear it and rebuild.
            if self._inventory_search.get_text():
                self._inventory_search.set_text("")
                self._bazar_render_items()
                found = self._bazar_select_item_position(name)
        if found is not None:
            selection.select_item(found, True)

    def _bazar_select_item_position(self, name: str) -> int | None:
        store = self.bazar_items_store
        if store is None:
            return None
        for position in range(store.get_n_items()):
            row = store.get_item(position)
            if (
                row is not None
                and row.item is not None
                and row.item.name == name
            ):
                return position
        return None

    def _bazar_update_relist_info(self) -> None:
        label = self.bazar_relist_info
        if label is None:
            return
        character = (
            self.engine.world.self_name
            or self.bazar.config.character
            or ""
        ).strip()
        if not character and self.bazar_character_entry is not None:
            character = self.bazar_character_entry.get_text().strip()
        npc = ""
        if self.bazar_npc_entry is not None:
            npc = self.bazar_npc_entry.get_text().strip()
        if not npc and self.engine.world.bazar.npc_id:
            npc = str(self.engine.world.bazar.npc_id)
        if not npc:
            npc = next(
                (
                    str(entity.entity_id)
                    for entity in self.engine.world.entities.values()
                    if getattr(entity, "vnum", 0) == 793
                ),
                "",
            )
        tracked = sum(
            1
            for item in self.bazar.config.items
            if item.relist and item.enabled
        )
        ready = sum(
            1
            for item in self.bazar.config.items
            if self._bazar_relist_ready(item)
        )
        parts = []
        if character:
            parts.append(f"character {character}")
        parts.extend(
            [
                f"Bibi {npc}" if npc else "Bibi id missing",
                f"{tracked} relisting",
                f"{ready} ready",
            ]
        )
        label.set_text(" · ".join(parts))

    @staticmethod
    def _bazar_standard_search(packet: str | None) -> bool:
        if not packet:
            return True
        parts = packet.split()
        return (
            len(parts) >= 10
            and parts[0] == "c_blist"
            and all(part == "0" for part in parts[1:9])
        )

    def _bazar_form_from_item(self, item: BazarItem) -> None:
        form = self.bazar_form
        if not form:
            return
        self.bazar_selected_name = item.name
        form["name"].set_text(item.name)
        if self._bazar_standard_search(item.search_packet):
            form["vnums"].set_text(
                " ".join(str(vnum) for vnum in item.vnums)
            )
            form["raw"].set_text("")
        else:
            form["vnums"].set_text("")
            form["raw"].set_text(item.search_packet or "")
        form["vnum"].set_text("" if item.vnum is None else str(item.vnum))
        form["tab"].set_selected(
            0 if item.inv_tab is None else item.inv_tab + 1
        )
        form["amount"].set_value(item.amount)
        form["min"].set_value(item.min_price)
        form["enabled"].set_active(item.enabled)
        form["relist"].set_active(item.relist)
        value, unit = item.cost()
        if unit == "ND":
            form["cost_type"].set_selected(1)
            form["cost"].set_value(value or 0)
        elif unit == "g":
            form["cost_type"].set_selected(2)
            form["cost"].set_value(value or 0)
        else:
            form["cost_type"].set_selected(0)
            form["cost"].set_value(0)
        form["unk1"].set_value(item.unk1)
        form["unk2"].set_value(item.unk2)
        form["durability"].set_value(item.durability)
        form["medal"].set_value(item.medal)

    def _bazar_new_item(self) -> None:
        self.bazar_selected_name = None
        self._bazar_selected_slot_vnum = None
        if self.bazar_items_selection is not None:
            self.bazar_items_selection.unselect_all()
        form = self.bazar_form
        if not form:
            return
        form["name"].set_text("")
        form["vnums"].set_text("")
        form["raw"].set_text("")
        form["vnum"].set_text("")
        form["tab"].set_selected(0)
        form["amount"].set_value(5)
        form["min"].set_value(0)
        form["enabled"].set_active(True)
        form["relist"].set_active(False)
        form["cost_type"].set_selected(0)
        form["cost"].set_value(0)
        form["unk1"].set_value(9)
        form["unk2"].set_value(4)
        form["durability"].set_value(1)
        form["medal"].set_value(2)
        form["name"].grab_focus()

    def _bazar_delete_item(self) -> None:
        name = self.bazar_selected_name
        if not name:
            self._bazar_log("select an item to delete")
            return
        item = self._bazar_item_by_name(name)
        label = self._bazar_item_label(item) if item is not None else name
        items = [
            entry for entry in self.bazar.config.items if entry.name != name
        ]
        self.bazar_selected_name = None
        self._bazar_set_items(items)
        self._bazar_new_item()
        self._bazar_save_config(quiet=True)
        # Last: _bazar_set_items stops a running pass and logs the stop, which
        # would otherwise replace this confirmation in the status bar.
        self._bazar_log(f"deleted {label}")

    def _bazar_item_from_form(self) -> BazarItem | None:
        form = self.bazar_form
        if not form:
            return None
        name = form["name"].get_text().strip()
        if not name:
            self._bazar_log("item name is required")
            return None
        raw = form["raw"].get_text().strip()
        try:
            if raw:
                search_packet = normalize_search_packet(raw)
            else:
                vnums: list[int] = []
                for text in form["vnums"].get_text().replace(",", " ").split():
                    value = int(text)
                    if value <= 0:
                        raise ValueError(text)
                    vnums.append(value)
                search_packet = build_search_packet(vnums) if vnums else None
        except (ValueError, BazarError) as exc:
            self._bazar_log(f"search packet: {exc}")
            return None
        vnum_text = form["vnum"].get_text().strip()
        vnum = None
        if vnum_text:
            if not vnum_text.isdigit() or int(vnum_text) <= 0:
                self._bazar_log("item vnum must be a positive number")
                return None
            vnum = int(vnum_text)
        if search_packet is None and vnum is None:
            self._bazar_log("an item needs a search and/or an item vnum")
            return None
        tab_index = form["tab"].get_selected()
        inv_tab = None if tab_index == 0 else tab_index - 1
        cost_index = form["cost_type"].get_selected()
        cost = float(form["cost"].get_value())
        # NPC resale: an Item.dat price is the usual gold cost. Fill it when
        # the user leaves the cost at 0, so adding Pet Food or Bell of Sweet
        # Home needs only the vnum and Cost = NPC gold.
        if cost_index == 2 and cost <= 0:
            lookup = vnum
            if lookup is None and search_packet:
                candidates = search_vnums(search_packet)
                if len(candidates) == 1:
                    lookup = candidates[0]
            if lookup is not None:
                info = self.item_info.get(abs(lookup))
                if info is not None and info.npc_price > 0:
                    cost = float(info.npc_price)
                    form["cost"].set_value(cost)
                    self._bazar_log(
                        f"NPC price for vnum {lookup} is "
                        f"{info.npc_price:,} g (Item.dat)"
                    )
                else:
                    self._bazar_log(
                        f"NPC price unknown for vnum {lookup}; enter it "
                        "by hand or wait for item names to load"
                    )
        return BazarItem(
            name=name,
            search_packet=search_packet,
            vnum=vnum,
            inv_tab=inv_tab,
            amount=int(form["amount"].get_value()),
            min_price=int(form["min"].get_value()),
            nos_cost=cost if cost_index == 1 and cost > 0 else None,
            npc_cost=cost if cost_index == 2 and cost > 0 else None,
            unk1=int(form["unk1"].get_value()),
            unk2=int(form["unk2"].get_value()),
            durability=int(form["durability"].get_value()),
            medal=int(form["medal"].get_value()),
            enabled=bool(form["enabled"].get_active()),
            relist=bool(form["relist"].get_active()),
        )

    def _bazar_save_item(self) -> None:
        item = self._bazar_item_from_form()
        if item is None:
            return
        items = list(self.bazar.config.items)
        old_name = self.bazar_selected_name
        if old_name is not None:
            index = next(
                (
                    index
                    for index, existing in enumerate(items)
                    if existing.name == old_name
                ),
                None,
            )
        else:
            index = None
        for position, existing in enumerate(items):
            if existing.name == item.name and position != index:
                self._bazar_log(f"an item named '{item.name}' already exists")
                return
        if index is None:
            items.append(item)
        else:
            items[index] = item
        self._bazar_apply_settings()
        self.bazar_selected_name = item.name
        self._bazar_set_items(items)
        self._bazar_save_config(quiet=True)

    def _bazar_set_items(self, items: list[BazarItem]) -> None:
        # A running pass holds the old item objects; stop rather than mix the
        # old pending search with a new item list.
        if self.bazar.running:
            self._bazar_stop("items changed")
        self.bazar.set_items(
            items,
            character=self.bazar.config.character,
            refresh=self.bazar.config.refresh,
        )
        self._bazar_render_items()
        self._bazar_render_results()
        self._bazar_render_relist_items()
        self._bazar_render_monitor_items()
        self._update_bazar_inventory_info()
        self._bazar_update_relist_info()
        if self._inventory_list is not None:
            self._inventory_schedule_render()

    def _load_nosmall_groups(self) -> list:
        """Read groups from the SQLite DB, or parse the client data."""
        try:
            from . import database
            if database.DEFAULT_DB_PATH.is_file():
                connection = database.connect()
                try:
                    groups = database.nosmall_groups(connection, limit=5000)
                finally:
                    connection.close()
                if groups:
                    return groups
        except Exception:  # noqa: BLE001 - fall back to the archive parser
            pass
        from .nosmall import load_catalog
        return load_catalog()

    def _nosmall_costs(self) -> dict[int, float]:
        """ND costs known for catalog groups.

        The curated reference map is the base; an item already in this
        window's list (for example one added by hand with its Cost edited)
        contributes its own vnums, so the importer can learn new prices.
        """
        try:
            from .nosmall import known_reference_costs
            costs = dict(known_reference_costs())
        except Exception:  # noqa: BLE001
            costs = {}
        for item in self.bazar.config.items:
            if item.nos_cost is None or item.nos_cost <= 0:
                continue
            vnums = item.vnums or (
                [item.vnum] if item.vnum is not None else []
            )
            for vnum in vnums:
                costs.setdefault(int(vnum), float(item.nos_cost))
        return costs

    @staticmethod
    def _nosmall_group_cost(group, costs: dict[int, float]) -> float | None:
        from .nosmall import group_cost
        return group_cost(group, costs)

    def _nosmall_importable(self) -> list:
        """Catalog groups worth importing: priced, non-pet items only.

        A group without an ND cost cannot produce a profit figure, so it is
        left out entirely; pets, fairies and partner supplies are companion
        items and are always excluded.
        """
        from .nosmall import importable_groups

        return importable_groups(self._nosmall_groups, self._nosmall_costs())

    def _open_nosmall_picker(self) -> None:
        if self.nosmall_window is not None:
            self.nosmall_window.present()
            return
        groups = self._load_nosmall_groups()
        self._nosmall_groups = groups
        importable = len(self._nosmall_importable())
        window = Gtk.Window(title="NosMall catalog")
        window.set_application(self)
        window.set_transient_for(self.window)
        window.set_modal(True)
        window.set_default_size(780, 620)
        window.connect("close-request", self._close_nosmall_picker)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        search = Gtk.SearchEntry()
        search.set_placeholder_text(
            "search catalog by item name, vnum or base vnum"
        )
        search.connect(
            "search-changed",
            lambda entry: self._nosmall_render(entry.get_text()),
        )
        self._nosmall_search = search
        search.set_hexpand(True)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        top.append(search)
        top.append(
            self._help_button(
                "NosMall catalog",
                f"{importable} importable NosMall item(s) with a known ND "
                "cost. Items without an ND cost cannot show a profit and are "
                "not listed; pets, fairies and partner supplies are excluded. "
                "To offer another item, add it with New and set its Cost "
                "first, or maintain the curated price map, then reopen this "
                "window.",
            )
        )
        box.append(top)
        self._nosmall_status = Gtk.Label(label="")
        self._nosmall_status.set_xalign(0)
        self._nosmall_status.add_css_class("dim-label")
        box.append(self._nosmall_status)
        self._nosmall_list = Gtk.ListBox()
        self._nosmall_list.set_activate_on_single_click(True)
        self._nosmall_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self._nosmall_list)
        scrolled.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        scrolled.set_vexpand(True)
        box.append(scrolled)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._nosmall_add_all_button = Gtk.Button(
            label="Add all matching"
        )
        self._nosmall_add_all_button.set_tooltip_text(
            "Add every matching group; a full pass is paced at 2.5 s."
        )
        self._nosmall_add_all_button.connect(
            "clicked", lambda *_: self._nosmall_add_all()
        )
        buttons.append(self._nosmall_add_all_button)
        prune = Gtk.Button(label="Remove unpriced & pets")
        prune.set_tooltip_text(
            "Remove unpriced items and pets. Click twice to confirm."
        )
        prune.connect("clicked", lambda *_: self._nosmall_prune())
        buttons.append(prune)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: window.close())
        buttons.append(close)
        box.append(buttons)
        window.set_child(box)
        self.nosmall_window = window
        self._nosmall_render("")
        window.present()

    def _nosmall_render(self, query: str) -> None:
        if self._nosmall_list is None:
            return
        child = self._nosmall_list.get_first_child()
        while child is not None:
            self._nosmall_list.remove(child)
            child = self._nosmall_list.get_first_child()

        needle = query.strip().casefold()
        importable = self._nosmall_importable()
        costs = self._nosmall_costs()
        matches = []
        for group in importable:
            if not needle:
                matches.append(group)
                continue
            if needle in group.name.casefold():
                matches.append(group)
                continue
            if any(needle in str(vnum) for vnum in group.vnums):
                matches.append(group)
                continue
            if needle == str(group.base_vnum):
                matches.append(group)
        self._nosmall_matches = matches
        shown = matches[:300]
        if self._nosmall_add_all_button is not None:
            self._nosmall_add_all_button.set_label(
                f"Add all matching ({len(matches)})"
            )
            self._nosmall_add_all_button.set_sensitive(bool(matches))
        if self._nosmall_status is not None:
            extra = (
                f" · showing first {len(shown)}"
                if len(matches) > len(shown)
                else ""
            )
            self._nosmall_status.set_text(
                f"{len(matches)} matching priced, non-pet group(s){extra}"
            )
        for group in shown:
            cost = self._nosmall_group_cost(group, costs)
            row = Gtk.ListBoxRow()
            content = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )
            name = Gtk.Label(label=group.name)
            name.set_xalign(0)
            name.set_hexpand(True)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(46)
            detail = Gtk.Label(
                label=(
                    " ".join(str(vnum) for vnum in group.vnums)
                    + (f" · {cost:g} ND" if cost is not None else "")
                )
            )
            detail.set_xalign(1)
            detail.add_css_class("dim-label")
            detail.set_ellipsize(Pango.EllipsizeMode.END)
            detail.set_max_width_chars(32)
            content.append(name)
            content.append(detail)
            row.set_child(content)
            row.connect(
                "activate", lambda _row, group=group: self._nosmall_add(group)
            )
            self._nosmall_list.append(row)

    def _nosmall_item_for(self, group, items: list) -> BazarItem | None:
        """Build a Bazar item for a catalog group, or None if not eligible."""
        from .nosmall import is_pet_group

        if is_pet_group(group):
            if self._nosmall_status is not None:
                self._nosmall_status.set_text(
                    f"{group.name} is a pet/partner item and is excluded"
                )
            return None
        cost = self._nosmall_group_cost(group, self._nosmall_costs())
        if cost is None:
            if self._nosmall_status is not None:
                self._nosmall_status.set_text(
                    f"{group.name} has no ND cost; profit cannot be "
                    "calculated, so it is excluded"
                )
            return None
        try:
            packet = group.search_packet()
        except Exception as exc:  # noqa: BLE001
            if self._nosmall_status is not None:
                self._nosmall_status.set_text(f"cannot build packet: {exc}")
            return None
        if any(item.search_packet == packet for item in items):
            if self._nosmall_status is not None:
                self._nosmall_status.set_text(
                    f"{group.name} is already in the item list"
                )
            return None
        name = group.name[:80]
        used = {item.name for item in items}
        if name in used:
            name = f"{group.name[:70]} [{group.base_vnum}]"
        counter = 2
        while name in used:
            name = f"{group.name[:66]} [{group.base_vnum}/{counter}]"
            counter += 1
        return BazarItem(
            name=name,
            search_packet=packet,
            vnum=group.vnums[0] if group.vnums else group.base_vnum,
            inv_tab=None,
            amount=5,
            min_price=0,
            nos_cost=cost,
        )

    def _nosmall_add(self, group) -> None:
        items = list(self.bazar.config.items)
        item = self._nosmall_item_for(group, items)
        if item is None:
            return
        self._bazar_set_items(items + [item])
        self._bazar_save_config(quiet=True)
        if self._nosmall_status is not None:
            cost = item.cost()[0]
            self._nosmall_status.set_text(
                f"Added {item.name}"
                + (f" ({cost:g} ND)" if cost is not None else "")
                + f" · {len(items) + 1} item(s) configured"
            )

    def _nosmall_add_all(self) -> None:
        groups = list(self._nosmall_matches)
        if not groups:
            return
        items = list(self.bazar.config.items)
        added = 0
        for group in groups:
            item = self._nosmall_item_for(group, items)
            if item is not None:
                items.append(item)
                added += 1
        if not added:
            if self._nosmall_status is not None:
                self._nosmall_status.set_text(
                    "All matching groups are already in the item list"
                )
            return
        self._bazar_set_items(items)
        self._bazar_save_config(quiet=True)
        if self._nosmall_status is not None:
            self._nosmall_status.set_text(
                f"Added {added} group(s) · {len(items)} item(s) configured"
            )

    def _nosmall_prune(self) -> None:
        """Remove unpriced and pet rows from the configured item list.

        Destructive, so it takes two clicks: the first arms the action and
        reports the count, the second within five seconds applies it. Only
        rows with an ND cost that are not pets survive.
        """
        from .nosmall import is_pet_group, split_priced_non_pet

        pet_vnums: set[int] = set()
        for group in self._nosmall_groups:
            if is_pet_group(group):
                pet_vnums.update(group.vnums)
                pet_vnums.add(group.base_vnum)
        keep, removed = split_priced_non_pet(
            list(self.bazar.config.items), pet_vnums
        )
        if not removed:
            if self._nosmall_status is not None:
                self._nosmall_status.set_text("Nothing to remove.")
            return
        now = time.monotonic()
        armed = (
            self._nosmall_prune_armed_at > 0.0
            and now - self._nosmall_prune_armed_at <= 5.0
        )
        if not armed:
            self._nosmall_prune_armed_at = now
            self._nosmall_prune_count = len(removed)
            if self._nosmall_status is not None:
                self._nosmall_status.set_text(
                    f"Click 'Remove unpriced & pets' again to delete "
                    f"{len(removed)} item(s) and keep {len(keep)}"
                )
            return
        self._nosmall_prune_armed_at = 0.0
        self._nosmall_prune_count = 0
        self._bazar_set_items(keep)
        self._bazar_save_config(quiet=True)
        if self._nosmall_status is not None:
            self._nosmall_status.set_text(
                f"Removed {len(removed)} unpriced/pet item(s) · "
                f"{len(keep)} priced item(s) kept"
            )
        # The picker list itself is unaffected by the item list, but the
        # price map now includes only the kept items' costs.
        self._nosmall_render(
            self._nosmall_search.get_text()
            if getattr(self, "_nosmall_search", None) is not None
            else ""
        )

    def _close_nosmall_picker(self, _window=None) -> bool:
        self.nosmall_window = None
        self._nosmall_list = None
        self._nosmall_status = None
        self._nosmall_matches = []
        self._nosmall_add_all_button = None
        self._nosmall_search = None
        self._nosmall_prune_armed_at = 0.0
        self._nosmall_prune_count = 0
        return False

    def _open_item_database_picker(self, mode: str = "monitor") -> None:
        """Search all Item.dat rows and add them to Monitor or Relist.

        Unlike the NosMall catalog picker (only priced groups), this lists
        every item in the local database (7,805 rows), filtered by name or
        vnum.  Adding fills the vnum/search packet and, when the item is in
        the inventory, its tab and stack; Relist mode also ticks the row and
        sets a floor of 1 so the set is immediately usable.
        """
        mode = mode if mode in ("monitor", "relist") else "monitor"
        self._item_db_mode = mode
        if self.item_db_window is not None:
            if self._item_db_search is not None:
                self._item_db_render(self._item_db_search.get_text())
            self.item_db_window.present()
            return
        if not self.item_info_loaded:
            self._ensure_item_info_loaded(
                lambda: self._open_item_database_picker(mode)
            )
            return
        window = Gtk.Window(
            title=(
                "Item database — add to monitor"
                if mode == "monitor"
                else "Item database — add to relist"
            )
        )
        window.set_application(self)
        window.set_transient_for(self.window)
        window.set_modal(True)
        window.set_default_size(760, 620)
        window.connect("close-request", self._close_item_db_picker)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        search = Gtk.SearchEntry()
        search.set_placeholder_text("search all items by name or vnum")
        search.set_hexpand(True)
        search.connect(
            "search-changed",
            lambda entry: self._item_db_render(entry.get_text()),
        )
        self._item_db_search = search
        top.append(search)
        top.append(
            self._help_button(
                "Item database",
                "Every item in the local Item.dat database, not just the "
                "NosMall catalog. Adding creates a Monitor or Relist row with "
                "the exact vnum as its search packet; a new row takes its "
                "inventory tab and stack when the item is in the bag. Relist "
                "mode also ticks the row and sets a floor of 1. Add all "
                "matching has a second-click confirmation for large lists.",
            )
        )
        box.append(top)
        self._item_db_status = Gtk.Label(label="")
        self._item_db_status.set_xalign(0)
        self._item_db_status.set_wrap(True)
        self._item_db_status.add_css_class("dim-label")
        box.append(self._item_db_status)
        self._item_db_list = Gtk.ListBox()
        self._item_db_list.set_activate_on_single_click(True)
        self._item_db_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._item_db_list.connect(
            "row-activated", self._item_db_row_activated
        )
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self._item_db_list)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        box.append(scrolled)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        add = Gtk.Button(label="Add selected")
        add.connect("clicked", lambda *_: self._item_db_add_selected())
        buttons.append(add)
        self._item_db_add_all_button = Gtk.Button(
            label="Add all matching"
        )
        self._item_db_add_all_button.connect(
            "clicked", lambda *_: self._item_db_add_all()
        )
        buttons.append(self._item_db_add_all_button)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: window.close())
        buttons.append(close)
        box.append(buttons)
        window.set_child(box)
        self.item_db_window = window
        self._item_db_render("")
        window.present()

    def _item_db_render(self, query: str) -> None:
        if self._item_db_list is None:
            return
        child = self._item_db_list.get_first_child()
        while child is not None:
            self._item_db_list.remove(child)
            child = self._item_db_list.get_first_child()
        needle = query.strip().casefold()
        matches = []
        for vnum, info in self.item_info.items():
            if vnum <= 0:
                continue
            if needle and needle not in info.name.casefold() and needle not in str(vnum):
                continue
            matches.append((vnum, info))
        matches.sort(key=lambda pair: (pair[1].name.casefold(), pair[0]))
        self._item_db_matches = matches
        shown = matches[:300]
        costs = self._nosmall_costs()
        for vnum, info in shown:
            row = Gtk.ListBoxRow()
            row.db_vnum = vnum  # type: ignore[attr-defined]
            content = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )
            name = Gtk.Label(label=info.name)
            name.set_xalign(0)
            name.set_hexpand(True)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(52)
            detail = Gtk.Label(
                label=(
                    f"vnum {vnum}"
                    + (
                        f" · NPC {info.npc_price:,} g"
                        if info.npc_price > 0
                        else ""
                    )
                    + (
                        f" · {costs[vnum]:g} ND"
                        if vnum in costs
                        else ""
                    )
                )
            )
            detail.set_xalign(1)
            detail.add_css_class("dim-label")
            detail.set_ellipsize(Pango.EllipsizeMode.END)
            detail.set_max_width_chars(40)
            content.append(name)
            content.append(detail)
            row.set_child(content)
            self._item_db_list.append(row)
        extra = (
            f" · showing first {len(shown)}"
            if len(matches) > len(shown)
            else ""
        )
        if self._item_db_status is not None:
            self._item_db_status.set_text(
                f"{len(matches)} matching item(s){extra} · adding to "
                f"{'Monitor' if self._item_db_mode == 'monitor' else 'Relist'}"
            )
        if getattr(self, "_item_db_add_all_button", None) is not None:
            self._item_db_add_all_button.set_label(
                f"Add all matching ({len(matches)})"
            )
            self._item_db_add_all_button.set_sensitive(bool(matches))

    def _item_db_row_activated(
        self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow
    ) -> None:
        vnum = getattr(row, "db_vnum", None)
        if vnum:
            self._item_db_add_selected(vnum)

    def _item_db_add_selected(self, vnum: int | None = None) -> None:
        if vnum is None:
            row = self._item_db_list.get_selected_row() if self._item_db_list else None
            vnum = getattr(row, "db_vnum", None) if row is not None else None
        if not vnum:
            if self._item_db_status is not None:
                self._item_db_status.set_text("select an item first")
            return
        added, enabled = self._bazar_add_items_from_db(
            [vnum], self._item_db_mode
        )
        if self._item_db_status is not None:
            self._item_db_status.set_text(
                f"{'added' if added else 'already configured; enabled'} "
                f"vnum {vnum}"
            )

    def _item_db_add_all(self) -> None:
        matches = list(self._item_db_matches)
        if not matches:
            return
        remaining = MAX_ITEMS - len(self.bazar.config.items)
        if remaining <= 0:
            if self._item_db_status is not None:
                self._item_db_status.set_text(
                    f"the config is full ({MAX_ITEMS} items)"
                )
            return
        vnums = [vnum for vnum, _info in matches[:remaining]]
        if len(vnums) > 50:
            now = time.monotonic()
            armed = (
                self._item_db_armed_at > 0.0
                and self._item_db_armed_count == len(vnums)
                and now - self._item_db_armed_at <= 5.0
            )
            if not armed:
                self._item_db_armed_at = now
                self._item_db_armed_count = len(vnums)
                if self._item_db_status is not None:
                    self._item_db_status.set_text(
                        f"Click 'Add all matching' again to add "
                        f"{len(vnums)} item(s)"
                    )
                return
            self._item_db_armed_at = 0.0
            self._item_db_armed_count = 0
        added, enabled = self._bazar_add_items_from_db(
            vnums, self._item_db_mode
        )
        if self._item_db_status is not None:
            self._item_db_status.set_text(
                f"Added {added} item(s) · {enabled} configured/enabled"
            )

    def _close_item_db_picker(self, _window=None) -> bool:
        self.item_db_window = None
        self._item_db_list = None
        self._item_db_search = None
        self._item_db_status = None
        self._item_db_matches = []
        self._item_db_add_all_button = None
        self._item_db_armed_at = 0.0
        self._item_db_armed_count = 0
        return False

    def _bazar_add_items_from_db(
        self, vnums: list[int], mode: str
    ) -> tuple[int, int]:
        """Add/update items from the full Item.dat database in one save.

        ``mode`` is ``monitor`` (enabled, monitor-only for new rows) or
        ``relist`` (enabled + relist, floor 1 when unset, inventory tab and
        stack when the item is in the bag).
        """
        items = list(self.bazar.config.items)
        by_vnum: dict[int, BazarItem] = {}
        for item in items:
            if item.vnum:
                by_vnum.setdefault(abs(int(item.vnum)), item)
        names = self._item_name_map()
        effective: dict[int, BazarItem] = {}
        for item in items:
            for vnum in tracked_vnums([item], names):
                effective.setdefault(abs(int(vnum)), item)
        used = {item.name for item in items}
        costs = self._nosmall_costs()
        added = 0
        enabled = 0
        touched = False
        for raw_vnum in vnums:
            vnum = abs(int(raw_vnum))
            if vnum <= 0:
                continue
            info = self.item_info.get(vnum)
            slot = None
            for bag in (1, 2, 0):
                slot = self.engine.world.find_inventory(bag, vnum)
                if slot is not None:
                    break
            item = by_vnum.get(vnum) or effective.get(vnum)
            if item is not None:
                changed = False
                if not item.enabled:
                    item.enabled = True
                    changed = True
                if mode == "relist":
                    if not item.relist:
                        item.relist = True
                        changed = True
                    if item.min_price <= 0:
                        item.min_price = 1
                        changed = True
                    if slot is not None:
                        amount = min(
                            1000,
                            max(1, int(getattr(slot, "amount", slot.quantity) or 1)),
                        )
                        for attribute, value in (
                            ("vnum", vnum),
                            ("inv_tab", slot.bag),
                            ("amount", amount),
                        ):
                            if getattr(item, attribute) != value:
                                setattr(item, attribute, value)
                                changed = True
                cost = costs.get(vnum)
                if cost is not None and item.nos_cost is None and item.npc_cost is None:
                    item.nos_cost = float(cost)
                    changed = True
                elif (
                    cost is None
                    and item.nos_cost is None
                    and item.npc_cost is None
                    and info is not None
                    and info.npc_price > 0
                ):
                    item.npc_cost = float(info.npc_price)
                    changed = True
                if changed:
                    touched = True
                enabled += 1
                continue
            name = (info.name if info is not None else None) or f"item {vnum}"
            candidate = name[:80]
            base = name[:72]
            counter = 2
            while candidate in used:
                candidate = (
                    f"{base} [{vnum}]"
                    if counter == 2
                    else f"{base[:64]} [{vnum}/{counter}]"
                )[:80]
                counter += 1
            used.add(candidate)
            index0 = info.index0 if info is not None else 0
            amount = 5
            inv_tab = None
            if slot is not None:
                inv_tab = slot.bag
                amount = min(
                    1000,
                    max(1, int(getattr(slot, "amount", slot.quantity) or 1)),
                )
            cost = costs.get(vnum)
            npc = info.npc_price if info is not None else 0
            new_item = BazarItem(
                name=candidate,
                search_packet=build_search_packet([vnum]),
                vnum=vnum,
                inv_tab=inv_tab,
                amount=amount,
                min_price=1 if mode == "relist" else 0,
                nos_cost=float(cost) if cost is not None else None,
                npc_cost=(
                    float(npc)
                    if cost is None and npc > 0
                    else None
                ),
                unk1=min(1000, 8 + index0) if index0 > 0 else 9,
                unk2=3 if index0 >= 2 else 4,
                durability=1,
                medal=2,
                enabled=True,
                relist=(mode == "relist"),
            )
            items.append(new_item)
            by_vnum[vnum] = new_item
            effective[vnum] = new_item
            added += 1
            enabled += 1
            touched = True
        if touched:
            self._bazar_set_items(items)
            self._bazar_save_config(quiet=True)
            self._bazar_render_items()
            self._bazar_render_relist_items()
            self._bazar_render_monitor_items()
        label = "monitor" if mode == "monitor" else "relist"
        self._bazar_log(
            f"item database: {added} item(s) added, {enabled} enabled "
            f"for {label}"
        )
        return added, enabled

    def _bazar_render_results(self) -> None:
        if not self.bazar_results_boxes:
            return
        self._bazar_render_version = self.bazar.version
        quotes = self.bazar.sorted_quotes()
        for source in self._bazar_monitor_sources:
            self._bazar_render_monitor_view(source, quotes)

    def _bazar_render_monitor_view(
        self, source: str, quotes: list[BazarQuote]
    ) -> None:
        box = self.bazar_results_boxes.get(source)
        info = self.bazar_results_infos.get(source)
        if box is None:
            return
        child = box.get_first_child()
        while child is not None:
            box.remove(child)
            child = box.get_first_child()

        # Only monitored items belong in a Monitor view: unticking a row
        # stops its searches, so its last quote would be stale.
        items = [
            quote
            for quote in quotes
            if monitor_source(quote.item) == source and quote.item.enabled
        ]
        needle = ""
        if self.bazar_monitor_search is not None:
            needle = self.bazar_monitor_search.get_text().strip().casefold()
        if needle:
            items = [
                quote
                for quote in items
                if needle in self._bazar_item_label(quote.item).casefold()
                or needle in quote.item.name.casefold()
                or needle in str(quote.item.vnum or "")
                or any(needle in str(vnum) for vnum in quote.item.vnums)
            ]
        if not items:
            if info is not None:
                info.set_text("No items" if not needle else "No match")
            empty = Gtk.Label(
                label=(
                    f'No item matches "{needle}".'
                    if needle
                    else (
                        "No NPC-resale items yet."
                        if source == "npc"
                        else "No NosMall items yet."
                    )
                )
            )
            empty.set_xalign(0)
            empty.add_css_class("dim-label")
            box.append(empty)
            return

        check = self.bazar_only_priced_checks.get(source)
        only_priced = check is not None and check.get_active()
        if only_priced:
            visible = [quote for quote in items if quote.price is not None]
        else:
            visible = list(items)
        hidden = len(items) - len(visible)
        if info is not None:
            text = (
                f"{len(items)} match(es)" if needle
                else "Most profitable first"
            )
            if only_priced:
                text += f" · {len(visible)} priced"
            if hidden:
                text += f" · {hidden} hidden"
            info.set_text(text)

        if not visible:
            empty = Gtk.Label(
                label=(
                    f'No priced match for "{needle}".'
                    if needle
                    else "No priced items yet."
                )
            )
            empty.set_xalign(0)
            empty.add_css_class("dim-label")
            box.append(empty)
            return

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(3)
        grid.set_margin_end(14)
        grid.set_margin_top(2)
        headers = (
            "Item",
            "Cost",
            "NB Price",
            "Markup" if source == "npc" else "Profit",
            "Seller",
            "Status",
            "",
        )
        aligns = (0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 0.0)
        for column, (title, align) in enumerate(zip(headers, aligns)):
            label = Gtk.Label()
            label.set_markup(f"<b>{title}</b>" if title else "")
            label.set_xalign(align)
            grid.attach(label, column, 0, 1, 1)

        # Thin lines between the rows: without them the eye cannot tell which
        # Cost/ND or NB Price cell belongs to which item in a long list. The
        # header gets its own line, then each data row is followed by one
        # except the last (no trailing line).
        columns = len(headers)

        def separator(row: int) -> None:
            line = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            line.add_css_class("bazar-sep")
            line.set_hexpand(True)
            line.set_size_request(-1, 1)
            line.set_margin_top(3)
            line.set_margin_bottom(3)
            grid.attach(line, 0, row, columns, 1)

        separator(1)
        for row_index, quote in enumerate(visible):
            grid_row = 2 + row_index * 2
            price = "n/a" if quote.price is None else f"{quote.price:,}"
            seller = quote.seller.replace("�", "").strip()
            cells = (
                # The real Item.dat name, not the configured shortcut.
                self._bazar_item_label(quote.item),
                quote.item.cost_text(),
                price,
                quote.profit_text(),
                seller or "-",
                quote.status_text(),
            )
            for column, (text, align) in enumerate(zip(cells, aligns)):
                label = Gtk.Label(label=text)
                label.set_xalign(align)
                if column == 0:
                    # Render the full item name; wrap long names instead of
                    # cutting them with an ellipsis. 30 chars keeps the price,
                    # seller and status columns visible in the narrow pane.
                    label.set_wrap(True)
                    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                    label.set_max_width_chars(30)
                    label.set_hexpand(True)
                    # Standard text selection + Ctrl+C, like any label; the
                    # Copy button and the right-click menu do it in one click.
                    label.set_selectable(True)
                    right = Gtk.GestureClick()
                    right.set_button(3)

                    def on_right(
                        _gesture, _n_press, _x, _y, label=label, name=text
                    ) -> None:
                        self._bazar_copy_menu(label, name)

                    right.connect("pressed", on_right)
                    label.add_controller(right)
                else:
                    label.set_ellipsize(Pango.EllipsizeMode.END)
                    if column == 4:
                        label.set_max_width_chars(18)
                    elif column == 5:
                        label.set_max_width_chars(26)
                if column == 5:
                    if quote.status in ("ok", "register-sent", "changed"):
                        label.add_css_class("bazar-ok")
                    elif quote.status in (
                        "timeout",
                        "bad",
                        "error",
                        "quantity",
                        "register-no-ack",
                        "change-no-ack",
                        "no-window",
                    ):
                        label.add_css_class("bazar-bad")
                    elif quote.status in (
                        "floor",
                        "own",
                        "no-slot",
                        "no-vnum",
                        "register-pending",
                        "register-unknown",
                        "change-pending",
                        "change-unknown",
                    ):
                        label.add_css_class("bazar-warn")
                    else:
                        label.add_css_class("dim-label")
                tooltip = []
                if column == 0:
                    # Keep the configured shortcut reachable without showing
                    # it as the item name.
                    tooltip.append(self._bazar_item_tooltip(quote.item))
                if quote.listing is not None:
                    raw = " | ".join(quote.listing.raw_fields)
                    if raw:
                        tooltip.append(raw)
                if tooltip:
                    label.set_tooltip_text("\n".join(tooltip))
                grid.attach(label, column, grid_row, 1, 1)
            # One-click copy of the real item name (the text a reseller
            # pastes into the in-game bazaar search). Selectable text and
            # the right-click menu stay available as well.
            copy = Gtk.Button(label="Copy")
            copy.add_css_class("flat")
            copy.set_valign(Gtk.Align.START)
            copy.set_tooltip_text(f"Copy the item name: {cells[0]}")
            copy.connect(
                "clicked",
                lambda _button, name=cells[0]: self._copy_text(name),
            )
            grid.attach(copy, columns - 1, grid_row, 1, 1)
            if row_index < len(visible) - 1:
                separator(grid_row + 1)
        box.append(grid)

    def _bazar_seed_from_log(self, force: bool = False) -> None:
        """Learn the Bibi id/option and inventory from this client's DLL log.

        A GUI attaches after login: the client's earlier manual bazaar opening
        and the login inventory dump are still in the log, not in the live
        packet stream.  The entity id and the inventory slots are needed
        before the first search/relist.  ``force`` re-reads the log for the
        Rescan button; a normal render never seeds twice.
        """
        if self._inventory_seeded and not force:
            return
        # Set before the work below: the function schedules an inventory
        # render, and a re-entrant seed while unseeded used to loop forever.
        self._inventory_seeded = True
        path = self.log_file
        self._inventory_log_truncated = log_was_truncated(path)
        if not path:
            self._maybe_load_inventory_cache()
            self._save_inventory_cache()
            return
        world = self.engine.world
        npc_id, option, page = load_bazaar_open(path)
        if npc_id is not None:
            # The log can hold a dialog with another NPC (entity ids are per
            # map); only seed an id this map's entity list knows as Bibi.
            known = world.entities.get(npc_id)
            if known is not None and getattr(known, "vnum", 0) != BIBI_VNUM:
                self._bazar_log(
                    f"bazaar: ignored stale NPC id {npc_id} from the log "
                    "(not Bibi Basar on this map)"
                )
                npc_id = None
        if npc_id is not None:
            state = world.bazar
            learned_option = (
                option if option is not None else state.open_option
            )
            learned_page = page if page is not None else state.open_page
            changed = (
                state.npc_id != npc_id
                or state.open_option != learned_option
                or state.open_page != learned_page
                or not state.option_learned
            )
            state.npc_id = npc_id
            state.open_option = learned_option
            state.open_page = learned_page
            state.option_learned = option is not None
            self._bazar_sync_from_world()
            if changed:
                self._bazar_log(
                    f"bazaar NPC {npc_id} learned from the client log"
                    + (
                        f" (option {option}/{page})"
                        if option is not None
                        else ""
                    )
                )
        if load_bazaar_skill(path):
            state = world.bazar
            if not state.open_skill:
                state.open_skill = True
                self._bazar_log(
                    "bazaar: this client opens NosBazar with its own "
                    "shortcut (c_skill); the monitor will use it"
                )
        bags, gold = load_log_inventory(path)
        if bags:
            full_bags = log_inventory_bags(path)
            self._inventory_full_bags.update(full_bags)
            if {0, 1, 2} <= full_bags:
                # The log contains the complete login inventory (full inv
                # lines, not just ivn updates), so this is safe to cache
                # even when the live stream missed it.
                self._inventory_core_seen = True
            count = 0
            filled = 0
            for bag, slots in bags.items():
                # A full `inv <bag>` line is authoritative. A bag seen only
                # in `ivn` updates is partial: it may fill a bag the attach
                # missed, but must never replace existing live/cached slots
                # (a truncated log can hold just one update for a bag).
                if bag in full_bags or not world.inventory.get(bag):
                    world.inventory[bag] = dict(slots)
                    count += len(slots)
                    filled += 1
            if filled:
                self._inventory_cache_dirty = True
                self._bazar_log(
                    f"inventory: {count} slot(s) in {filled} bag(s) "
                    f"seeded from the log"
                )
        if gold is not None:
            world.gold = gold
        self._inventory_seeded = True
        self._maybe_load_inventory_cache()
        self._save_inventory_cache()
        self._bazar_sync_from_world()
        self._update_bazar_inventory_info()
        if self._inventory_list is not None:
            self._inventory_schedule_render()

    def _bazar_reset_for_attach(self, winpid: int) -> None:
        """Reset the tab together with the engine when another client attaches."""
        self._bazar_stop("client changed")
        self.bazar_selected_name = None
        self._bazar_selected_slot_vnum = None
        self._bazar_monitor_selected.clear()
        self.bazar_monitor_selected_name = None
        self._bazar_config_loaded = False
        self._inventory_seeded = False
        self._inventory_cache_loaded = False
        self._inventory_cache_note = ""
        self._inventory_cache_dirty = False
        self._inventory_log_truncated = False
        self._inventory_full_bags.clear()
        self._inventory_core_seen = False
        if self._inventory_cache_source is not None:
            GLib.source_remove(self._inventory_cache_source)
            self._inventory_cache_source = None
        self._bazar_character_autofilled = False
        self._self_name_scanned = False
        self._bazar_npc_autofilled = False
        self._bazar_npc_autofill_value = ""
        self.bazar_config_path = self._bazar_path_for(winpid)
        if self.bazar_config_label is not None:
            self.bazar_config_label.set_text(str(self.bazar_config_path))
        if self.bazar_character_entry is not None:
            self.bazar_character_entry.set_text("")
        if self.bazar_npc_entry is not None:
            self.bazar_npc_entry.set_text("")
        self._bazar_load_config(fallback=True)
        self._update_bazar_inventory_info()
        if self._inventory_list is not None:
            self._inventory_checks.clear()
            self._inventory_schedule_render()

    def _build_bazar_page(self):
        """NosBazar monitor, inventory scan and relisting.

        The page is a second-level notebook so each workflow owns a full
        section instead of squeezing every control into one sidebar.
        """
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.set_margin_top(6)
        page.set_margin_start(8)
        page.set_margin_end(8)
        page.set_margin_bottom(6)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        open_button = Gtk.Button(label="Open bazaar")
        open_button.set_tooltip_text(
            "Replay the Bibi Basar dialog through the client's send path."
        )
        open_button.connect("clicked", lambda *_: self._bazar_open())
        toolbar.append(open_button)
        self.bazar_info = Gtk.Label(label="NosBazar idle")
        self.bazar_info.set_xalign(0)
        self.bazar_info.set_hexpand(True)
        toolbar.append(self.bazar_info)
        toolbar.append(
            self._help_button(
                "NosBazar",
                "Section tabs: Items is the searchable catalog of every "
                "Item.dat row, where the Monitor and Relist ticks choose "
                "what this client watches and relists, and the editor sets "
                "the amount, floor and Cost type; Relist sets the exact "
                "vnum, tab and floor and starts registrations; Monitor "
                "searches and shows prices/profit, with third-level NosMall "
                "(ND cost, gold per ND) and NPC resale (NPC gold cost, such "
                "as Pet Food or Bell of Sweet Home; gold markup) views; "
                "Configuration has the Bibi id, refresh and config file. "
                "Open bazaar replays the Bibi Basar dialog through the "
                "client's own send path; with no Bibi visible it uses the "
                "portable NosBazar medal (vnum 1955) from the inventory "
                "instead. Relist changes real listings and spends the fee.",
            )
        )
        page.append(toolbar)

        sections = Gtk.Notebook()
        sections.set_vexpand(True)
        sections.set_scrollable(True)
        self.bazar_section_notebook = sections
        pages = (
            ("items", "Items", self._build_bazar_items_section()),
            ("relist", "Relist", self._build_bazar_relist_section()),
            ("monitor", "Monitor", self._build_bazar_monitor_section()),
            ("config", "Configuration", self._build_bazar_config_section()),
        )
        for key, title, child in pages:
            index = sections.append_page(child, Gtk.Label(label=title))
            self._bazar_section_index[key] = index
        sections.connect("switch-page", self._bazar_section_switched)
        page.append(sections)
        page.connect("map", self._bazar_page_mapped)

        self.bazar_page = page
        self._bazar_render_items()
        self._bazar_render_results()
        self._bazar_render_relist_items()
        self._bazar_render_monitor_items()
        self._update_bazar_inventory_info()
        if self._inventory_list is not None:
            self._inventory_render()
        self._bazar_update_info()
        self._bazar_update_relist_info()
        self._bazar_show_section("items")
        return page

    def _bazar_show_section(self, key: str) -> None:
        if self.bazar_section_notebook is None:
            return
        index = self._bazar_section_index.get(key)
        if index is not None:
            self.bazar_section_notebook.set_current_page(index)

    def _bazar_page_mapped(self, *_args) -> None:
        """Load item names when the NosBazar page becomes visible."""
        self._ensure_item_info_loaded()
        if self._inventory_list is not None:
            self._inventory_render()

    def _bazar_section_switched(
        self, _notebook: Gtk.Notebook, _page: Gtk.Widget, index: int
    ) -> None:
        if index != self._bazar_section_index.get("items"):
            return
        # Do not parse names at startup while the page is hidden; the map
        # signal handles the first real display.
        if self.bazar_page is not None and not self.bazar_page.get_mapped():
            return
        self._ensure_item_info_loaded()
        if self._inventory_list is not None:
            self._inventory_render()

    def _bazar_monitor_switched(
        self, _notebook, _page, index: int
    ) -> None:
        if index < 0 or index >= len(self._bazar_monitor_sources):
            return
        source = self._bazar_monitor_sources[index]
        self.bazar_results_box = self.bazar_results_boxes.get(source)
        self.bazar_results_info = self.bazar_results_infos.get(source)
        self.bazar_only_priced = self.bazar_only_priced_checks.get(source)

    def _build_items_group_button(self) -> Gtk.MenuButton:
        """Bulk Monitor/Relist switches over the whole configured list."""
        return self._menu_button(
            "Group actions…",
            "Bulk switches over every configured item: monitor or stop, "
            "relist or stop.",
            (
                (
                    "Monitor all",
                    lambda: self._bazar_items_set_all("enabled", True),
                ),
                (
                    "Stop monitoring all",
                    lambda: self._bazar_items_set_all("enabled", False),
                ),
                None,
                (
                    "Relist all",
                    lambda: self._bazar_items_set_all("relist", True),
                ),
                (
                    "Stop relisting all",
                    lambda: self._bazar_items_set_all("relist", False),
                ),
            ),
        )

    def _bazar_items_set_all(self, attribute: str, active: bool) -> None:
        if attribute not in ("enabled", "relist"):
            return
        changed = 0
        floors = 0
        enabled_more = 0
        for item in self.bazar.config.items:
            if getattr(item, attribute) != active:
                setattr(item, attribute, active)
                changed += 1
            if (
                attribute == "relist"
                and active
                and item.min_price <= 0
            ):
                item.min_price = 1
                floors += 1
                changed += 1
            # The relist set only reaches enabled items; "Relist all" has to
            # switch the Monitor tick back on or the pass would skip them.
            if attribute == "relist" and active and not item.enabled:
                item.enabled = True
                enabled_more += 1
                changed += 1
        if not changed:
            return
        self._bazar_save_config(quiet=True)
        self._bazar_render_items()
        self._bazar_render_relist_items()
        self._bazar_render_monitor_items()
        self._bazar_render_results()
        self._bazar_update_relist_info()
        self._bazar_update_monitor_items_info()
        label = "monitor" if attribute == "enabled" else "relist"
        text = (
            f"{label}: {len(self.bazar.config.items)} item(s) "
            f"{'on' if active else 'off'}"
        )
        if floors:
            text += f" · floor 1 set on {floors} item(s)"
        if enabled_more:
            text += f" · monitoring switched on for {enabled_more} item(s)"
        self._bazar_log(text)

    def _build_items_import_button(self) -> Gtk.MenuButton:
        return self._menu_button(
            "Import…",
            "Add items from the NosMall catalog, the whole Item.dat "
            "database, or a saved bazar config.",
            (
                ("Import NosMall…", self._open_nosmall_picker),
                (
                    "Item database…",
                    lambda: self._open_item_database_picker("monitor"),
                ),
                None,
                (
                    "Import config (add)…",
                    lambda: self._open_config_import("add"),
                ),
                (
                    "Replace with config…",
                    lambda: self._open_config_import("replace"),
                ),
            ),
        )

    def _build_items_more_button(self) -> Gtk.MenuButton:
        return self._menu_button(
            "⋯",
            "Inventory refresh, bulk actions and selection.",
            (
                ("Rescan inventory", self._inventory_reload),
                ("Price all inventory", self._bazar_price_inventory),
                ("Add whole inventory", self._bazar_add_whole_inventory),
                None,
                (
                    "Monitor all configured",
                    lambda: self._bazar_items_set_all("enabled", True),
                ),
                (
                    "Stop monitoring all configured",
                    lambda: self._bazar_items_set_all("enabled", False),
                ),
                (
                    "Relist all configured",
                    lambda: self._bazar_items_set_all("relist", True),
                ),
                (
                    "Stop relisting all configured",
                    lambda: self._bazar_items_set_all("relist", False),
                ),
                None,
                (
                    "Select all shown",
                    lambda: self._bazar_select_all_shown(),
                ),
                ("Unselect all", self._bazar_unselect_all),
            ),
        )

    def _bazar_select_all_shown(self) -> None:
        if self.bazar_items_selection is not None:
            self.bazar_items_selection.select_all()

    def _bazar_unselect_all(self) -> None:
        if self.bazar_items_selection is not None:
            self.bazar_items_selection.unselect_all()

    def _bazar_add_whole_inventory(self) -> None:
        slots = self._inventory_slots()
        if not slots:
            self._bazar_log(
                "inventory is empty; relog or change channel with this "
                "window open"
            )
            return
        created = self._inventory_add(slots, start=False)
        self._bazar_log(
            f"inventory: {len(created)} item(s) added · "
            f"{len(self.bazar.config.items)} configured"
        )

    def _open_config_import(self, mode: str) -> None:
        """Pick a bazar JSON config: merge its items in or replace them."""
        if mode not in ("add", "replace"):
            return
        dialog = Gtk.FileDialog()
        dialog.set_title(
            "Import items from a bazar config"
            if mode == "add"
            else "Replace items with a bazar config"
        )
        json_filter = Gtk.FileFilter()
        json_filter.set_name("Bazar config (JSON)")
        json_filter.add_pattern("*.json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(json_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(json_filter)
        dialog.open(self.window, None, self._on_config_import_chosen, mode)

    def _on_config_import_chosen(
        self, dialog: Gtk.FileDialog, result, mode: str
    ) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path_text = gfile.get_path() if gfile is not None else None
        if not path_text:
            self._bazar_log("config import: no local file selected")
            return
        path = Path(path_text)
        if mode == "replace":
            try:
                imported = load_bazar_config(path)
            except (BazarError, OSError, ValueError) as exc:
                self._bazar_log(f"config import: {path.name}: {exc}")
                return
            self._confirm_config_replace(path, imported)
            return
        self._bazar_import_config_path(path, "add")

    def _confirm_config_replace(self, path: Path, imported) -> None:
        window = Gtk.Window(title="Replace items?")
        window.set_application(self)
        window.set_transient_for(self.window)
        window.set_modal(True)
        window.set_default_size(470, -1)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        text = Gtk.Label(
            label=(
                f"Replace the {len(self.bazar.config.items)} configured "
                f"item(s) with the {len(imported.items)} from "
                f"{path.name}?"
            )
        )
        text.set_xalign(0)
        text.set_hexpand(True)
        text.set_wrap(True)
        top.append(text)
        top.append(
            self._help_button(
                "Replace items",
                "The imported file's items replace every configured row, "
                "including their relist floors. The Bibi id, character and "
                "refresh interval stay this client's. An old config without "
                "the relist flag loads as monitor-only (a positive Min price "
                "still infers relisting). Save the current items first if "
                "you want to keep them.",
            )
        )
        box.append(top)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: window.close())
        replace = Gtk.Button(label="Replace")
        replace.add_css_class("destructive-action")
        replace.connect(
            "clicked",
            lambda *_: (
                window.close(),
                self._apply_config_import(imported, path, "replace"),
            ),
        )
        buttons.append(cancel)
        buttons.append(replace)
        box.append(buttons)
        window.set_child(box)
        window.present()

    def _bazar_import_config_path(
        self, path: Path | str, mode: str = "add"
    ) -> bool:
        """Load a bazar config file into this window (programmatic path)."""
        path = Path(path)
        try:
            imported = load_bazar_config(path)
        except (BazarError, OSError, ValueError) as exc:
            self._bazar_log(f"config import: {path.name}: {exc}")
            return False
        return self._apply_config_import(imported, path, mode)

    def _apply_config_import(self, imported, path: Path, mode: str) -> bool:
        incoming = list(imported.items)
        if not incoming:
            self._bazar_log(f"config import: {path.name} has no items")
            return False
        if mode == "replace":
            items = incoming[:MAX_ITEMS]
            dropped = len(incoming) - len(items)
            self._bazar_set_items(items)
            self._bazar_save_config(quiet=True)
            self._bazar_log(
                f"config import: replaced with {len(items)} item(s) from "
                f"{path.name}"
                + (
                    f" ({dropped} over the {MAX_ITEMS} limit dropped)"
                    if dropped
                    else ""
                )
            )
            return True
        items = list(self.bazar.config.items)
        names = self._item_name_map()
        covered = tracked_vnums(items, names)
        used = {item.name for item in items}
        added = 0
        skipped = 0
        for item in incoming:
            if len(items) >= MAX_ITEMS:
                skipped += 1
                continue
            if item.name in used:
                skipped += 1
                continue
            vnums = tracked_vnums([item], names)
            vnum = self._bazar_row_vnum(item)
            if vnum is not None:
                vnums.add(vnum)
            if vnums & covered:
                skipped += 1
                continue
            items.append(item)
            used.add(item.name)
            covered |= vnums
            added += 1
        if added:
            self._bazar_set_items(items)
            self._bazar_save_config(quiet=True)
        self._bazar_log(
            f"config import: {added} item(s) added, {skipped} already "
            f"present from {path.name} · {len(items)} configured"
        )
        return added > 0

    def _bazar_item_check_column(
        self, title: str, field: str, width: int = 78
    ) -> Gtk.ColumnViewColumn:
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, item) -> None:
            check = Gtk.CheckButton()
            check.set_halign(Gtk.Align.CENTER)
            check.set_valign(Gtk.Align.CENTER)
            item.set_child(check)

        def bind(_factory, item) -> None:
            row = item.get_item()
            check = item.get_child()
            old = getattr(check, "_ntpl_handler", None)
            if old is not None:
                check.disconnect(old)
                check._ntpl_handler = None
            check.set_sensitive(
                True
                if field == "monitor"
                else bool(getattr(row, "can_relist", True))
            )
            # Connect after set_active so binding never fires the handler.
            check.set_active(bool(getattr(row, field)))
            check._ntpl_handler = check.connect(
                "toggled", self._bazar_item_row_toggled, row, field
            )

        def unbind(_factory, item) -> None:
            check = item.get_child()
            old = getattr(check, "_ntpl_handler", None)
            if old is not None:
                check.disconnect(old)
                check._ntpl_handler = None

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        factory.connect("unbind", unbind)
        column = Gtk.ColumnViewColumn(title=title, factory=factory)
        column.set_fixed_width(width)
        return column

    def _bazar_item_text_column(
        self,
        title: str,
        attribute: str,
        expand: bool = False,
        width: int | None = None,
    ) -> Gtk.ColumnViewColumn:
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, item) -> None:
            label = Gtk.Label(xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            item.set_child(label)

        def bind(_factory, item) -> None:
            row = item.get_item()
            label = item.get_child()
            label.set_text(str(getattr(row, attribute)))
            if attribute != "display_name":
                return
            label.set_tooltip_text(row.tooltip)
            old = getattr(label, "_ntpl_gesture", None)
            if old is not None:
                label.remove_controller(old)
            gesture = Gtk.GestureClick()
            gesture.set_button(3)
            gesture.connect(
                "pressed",
                lambda _gesture, _n, _x, _y, row=row: (
                    self._bazar_copy_menu(label, row.display_name)
                ),
            )
            label.add_controller(gesture)
            label._ntpl_gesture = gesture

        def unbind(_factory, item) -> None:
            if attribute != "display_name":
                return
            label = item.get_child()
            gesture = getattr(label, "_ntpl_gesture", None)
            if gesture is not None:
                label.remove_controller(gesture)
                label._ntpl_gesture = None

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        factory.connect("unbind", unbind)
        column = Gtk.ColumnViewColumn(title=title, factory=factory)
        column.set_expand(expand)
        if width is not None:
            column.set_fixed_width(width)
        return column

    def _bazar_item_columns(self) -> list[Gtk.ColumnViewColumn]:
        return [
            self._bazar_item_check_column("Monitor", "monitor"),
            self._bazar_item_check_column("Relist", "relist"),
            self._bazar_item_text_column(
                "Item", "display_name", expand=True
            ),
            self._bazar_item_text_column("Vnum", "vnum", width=84),
            self._bazar_item_text_column("Where", "location", width=190),
            self._bazar_item_text_column("Cost", "cost_text", width=90),
        ]

    def _build_bazar_editor(self) -> Gtk.Widget:
        """The add/edit form for one item; values live in ``bazar_form``."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        form = self.bazar_form

        def entry(key: str, placeholder: str, width: int = 14) -> Gtk.Entry:
            widget = Gtk.Entry()
            widget.set_placeholder_text(placeholder)
            widget.set_max_length(200)
            widget.set_width_chars(width)
            form[key] = widget
            return widget

        name_entry = entry("name", "shortcut label", 18)
        name_entry.set_tooltip_text(
            "Your label for this entry; the Monitor and item lists show "
            "the real item name for its vnum."
        )
        copy_name = Gtk.Button(label="Copy")
        copy_name.set_tooltip_text(
            "Copy the configured label."
        )
        copy_name.connect(
            "clicked",
            lambda *_: self._copy_text(
                name_entry.get_text(), "configured label"
            ),
        )
        box.append(
            self._field_row(
                Gtk.Label(label="Name"), name_entry, copy_name
            )
        )
        vnums_entry = entry("vnums", "e.g. 1261 2174", 18)
        vnums_entry.set_tooltip_text(
            "Alias vnums; the Monitor searches only those whose item name "
            "matches this row's Item vnum."
        )
        box.append(
            self._field_row(Gtk.Label(label="Search vnums"), vnums_entry)
        )
        raw = entry("raw", "optional full c_blist …", 18)
        raw.set_tooltip_text(
            "A captured c_blist overrides Search vnums."
        )
        box.append(
            self._field_row(Gtk.Label(label="Raw packet"), raw)
        )
        vnum = entry("vnum", "item vnum", 8)
        vnum.set_input_purpose(Gtk.InputPurpose.DIGITS)
        tab = Gtk.DropDown.new_from_strings(
            ["Tab none", "Equip (0)", "Main (1)", "Etc (2)"]
        )
        tab.set_selected(0)
        form["tab"] = tab
        box.append(
            self._field_row(
                Gtk.Label(label="Item vnum"), vnum, tab
            )
        )
        amount = Gtk.SpinButton.new_with_range(1, 1000, 1)
        amount.set_value(5)
        amount.set_digits(0)
        form["amount"] = amount
        minimum = Gtk.SpinButton.new_with_range(0, 2_000_000_000, 1000)
        minimum.set_value(0)
        minimum.set_digits(0)
        minimum.set_width_chars(12)
        form["min"] = minimum
        box.append(
            self._field_row(
                Gtk.Label(label="Amount"), amount,
                Gtk.Label(label="Min price"), minimum,
            )
        )
        enabled = Gtk.CheckButton(label="Monitor")
        enabled.set_active(True)
        enabled.set_tooltip_text(
            "Monitor searches this item. Off keeps its saved settings but "
            "skips every pass."
        )
        form["enabled"] = enabled
        relist = Gtk.CheckButton(label="Relist")
        relist.set_active(False)
        relist.set_tooltip_text(
            "Include this item in the relist set (Relist Start searches and "
            "may change its listing; needs vnum, tab and a positive floor)."
        )
        form["relist"] = relist
        box.append(
            self._field_row(
                Gtk.Label(label="Tracking"), enabled, relist
            )
        )
        cost_type = Gtk.DropDown.new_from_strings(
            ["No cost", "NosMall ND", "NPC gold"]
        )
        cost_type.set_selected(0)
        form["cost_type"] = cost_type
        cost = Gtk.SpinButton.new_with_range(0, 1_000_000_000, 0.5)
        cost.set_digits(2)
        cost.set_value(0)
        form["cost"] = cost
        box.append(
            self._field_row(
                Gtk.Label(label="Cost"), cost_type, cost
            )
        )

        advanced = Gtk.Expander(label="Advanced c_reg fields")
        advanced_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6
        )
        adv_widgets: dict[str, Gtk.SpinButton] = {}
        for key, value in (
            ("unk1", 9),
            ("unk2", 4),
            ("durability", 1),
            ("medal", 2),
        ):
            spin = Gtk.SpinButton.new_with_range(0, 1000, 1)
            spin.set_value(value)
            spin.set_digits(0)
            form[key] = spin
            adv_widgets[key] = spin
        advanced_box.append(
            self._field_row(
                Gtk.Label(label="unk1"), adv_widgets["unk1"],
                Gtk.Label(label="unk2"), adv_widgets["unk2"],
            )
        )
        advanced_box.append(
            self._field_row(
                Gtk.Label(label="durability"), adv_widgets["durability"],
                Gtk.Label(label="medal"), adv_widgets["medal"],
            )
        )
        advanced.set_child(advanced_box)
        box.append(advanced)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        save = Gtk.Button(label="Save item")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self._bazar_save_item())
        clear = Gtk.Button(label="Clear")
        clear.connect("clicked", lambda *_: self._bazar_new_item())
        buttons.append(save)
        buttons.append(clear)
        box.append(buttons)
        return box
