"""GTK4 front end: packet table + map/path/auto-attack controls.

The GTK loop and the socket reader never share a thread: the reader pushes into
a ``queue.Queue`` and a 50 ms GLib timeout drains it on the main loop. The bot
runs on the same GLib main loop and only sends packets through the client; it
never touches GTK from a worker thread.
"""

from __future__ import annotations

import math
import os
import queue
import threading
import time

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from .client import ConnectionLost, PacketClient
from .engine import Engine, Entity
from .mapdata import (
    DEFAULT_NSTC,
    MapDataError,
    MapGrid,
    astar,
    find_map_for_positions,
    load_nstc,
    terrain_rgb,
)
from .protocol import Kind, Packet, ProtocolError

MAX_ROWS = 20000
TRIM_ROWS = 2000
DRAIN_INTERVAL_MS = 50
BOT_INTERVAL_MS = 200
DEFAULT_MAP_ID = 20
DEFAULT_SCALE = 3


class PacketRow(GObject.Object):
    __gtype_name__ = "NtplPacketRow"

    def __init__(self, packet: Packet) -> None:
        super().__init__()
        self.packet = packet
        self.direction = packet.direction.value
        local = time.localtime(packet.timestamp)
        millis = int(packet.timestamp * 1000) % 1000
        self.time_text = (
            f"{local.tm_hour:02d}:{local.tm_min:02d}:{local.tm_sec:02d}.{millis:03d}"
        )
        self.opcode = packet.opcode
        self.text = packet.text


def _column(title: str, attribute: str, expand: bool = False, width: int | None = None):
    factory = Gtk.SignalListItemFactory()

    def setup(_factory, item) -> None:
        label = Gtk.Label(xalign=0)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        item.set_child(label)

    def bind(_factory, item) -> None:
        row = item.get_item()
        label = item.get_child()
        label.set_text(getattr(row, attribute))
        if attribute == "direction":
            label.remove_css_class("recv")
            label.remove_css_class("send")
            label.add_css_class("recv" if row.direction == "RECV" else "send")

    factory.connect("setup", setup)
    factory.connect("bind", bind)

    column = Gtk.ColumnViewColumn(title=title, factory=factory)
    column.set_expand(expand)
    if width is not None:
        column.set_fixed_width(width)
    return column


class Application(Gtk.Application):
    def __init__(self, host: str, port: int | None, map_data: str | None = None) -> None:
        super().__init__(
            application_id="cz.tarajura.ntpl",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.host = host
        self.port = port
        self.client: PacketClient | None = None
        self.inbox: queue.Queue = queue.Queue()
        self.filter_text = ""
        self.status_received = False

        self.store = None
        self.filter = None
        self.filter_model = None
        self.scrolled = None
        self.search = None
        self.send_entry = None
        self.status = None
        self.follow = None
        self.window = None
        self.notebook = None

        self.engine = Engine()
        self.char_id: int | None = None
        self.last_move: tuple[int, int, int, int] | None = None
        self.last_move_time = 0.0

        self.map_file = (
            map_data or os.environ.get("NTPL_NSTC") or str(DEFAULT_NSTC)
        )
        self.map_grids: dict[int, MapGrid] = {}
        self.map_grid: MapGrid | None = None
        self.map_surface: cairo.ImageSurface | None = None
        self.map_scale = DEFAULT_SCALE
        self.map_area = None
        self.map_scrolled = None
        self.map_id_spin = None
        self.map_scale_spin = None
        self.map_info = None
        self.skill_entry = None
        self.skill_packet_entry = None
        self.attack_range = None
        self.attack_cooldown = None
        self.char_entry = None
        self.auto_attack_toggle = None
        self.loop_toggle = None

        self.waypoints: list[tuple[int, int]] = []
        self.path_cells: list[tuple[int, int]] = []
        self.path_index = 0
        self.pending_target: tuple[int, int] | None = None
        self.pending_since = 0.0
        self.pending_until = 0.0
        self.stand_sent_until = 0.0
        self.attack_cooldown_until = 0.0
        self.bot_running = False
        self.bot_timer = None

    # -- UI -------------------------------------------------------------

    def do_activate(self) -> None:
        active = self.get_active_window()
        if active is not None:
            active.present()
            return

        self._build_ui()
        self._install_css()
        self._start_client()

    def _build_ui(self) -> None:
        window = Gtk.ApplicationWindow(application=self, title="NosTale Packet Logger")
        window.set_default_size(1280, 820)
        self.window = window

        header = Gtk.HeaderBar()
        window.set_titlebar(header)

        clear = Gtk.Button(label="Clear")
        clear.connect("clicked", lambda *_: self.store.remove_all())
        header.pack_start(clear)

        self.follow = Gtk.ToggleButton(label="Follow")
        self.follow.set_active(True)
        self.follow.set_tooltip_text("Scroll to the newest packet automatically")
        header.pack_end(self.follow)

        packet_page = self._build_packet_page()
        map_page = self._build_map_page()

        self.notebook = Gtk.Notebook()
        self.notebook.append_page(packet_page, Gtk.Label(label="Packets"))
        self.notebook.append_page(map_page, Gtk.Label(label="Map / Bot"))
        self.notebook.set_vexpand(True)
        self.notebook.connect("switch-page", self._on_page_switch)

        self.status = Gtk.Label(label="connecting...")
        self.status.set_xalign(0)
        self.status.set_margin_start(8)
        self.status.set_margin_bottom(4)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        root.append(self.notebook)
        root.append(self.status)
        window.set_child(root)
        window.present()

        self._ensure_map_loaded()

    def _build_packet_page(self):
        self.store = Gio.ListStore.new(PacketRow)
        self.filter_model = Gtk.FilterListModel.new(self.store)
        self.filter = Gtk.CustomFilter.new(self._matches)
        self.filter_model.set_filter(self.filter)

        selection = Gtk.NoSelection(model=self.filter_model)
        self.column_view = Gtk.ColumnView(model=selection)
        self.column_view.append_column(_column("Time", "time_text", width=110))
        self.column_view.append_column(_column("Dir", "direction", width=70))
        self.column_view.append_column(_column("Opcode", "opcode", width=150))
        self.column_view.append_column(_column("Packet", "text", expand=True))
        self.column_view.set_show_row_separators(True)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_child(self.column_view)
        self.scrolled.set_vexpand(True)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_top(6)
        toolbar.set_margin_bottom(6)
        toolbar.set_margin_start(8)
        toolbar.set_margin_end(8)

        self.search = Gtk.SearchEntry()
        self.search.set_hexpand(True)
        self.search.set_placeholder_text("filter: opcode or packet text")
        self.search.connect("search-changed", self._on_search)
        toolbar.append(self.search)

        self.send_entry = Gtk.Entry()
        self.send_entry.set_hexpand(True)
        self.send_entry.set_placeholder_text("packet to send, e.g. walk 19 146 0 10")
        self.send_entry.connect("activate", self._on_send)
        toolbar.append(self.send_entry)

        send_button = Gtk.Button(label="Send")
        send_button.connect("clicked", lambda *_: self._on_send(self.send_entry))
        toolbar.append(send_button)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.append(toolbar)
        page.append(self.scrolled)
        return page

    def _build_map_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        top.set_margin_top(6)
        top.set_margin_start(8)
        top.set_margin_end(8)
        top.append(Gtk.Label(label="Map ID"))
        self.map_id_spin = Gtk.SpinButton.new_with_range(0, 100000, 1)
        self.map_id_spin.set_value(DEFAULT_MAP_ID)
        top.append(self.map_id_spin)
        load = Gtk.Button(label="Load")
        load.connect("clicked", self._on_load_map)
        top.append(load)
        auto = Gtk.Button(label="Auto-detect")
        auto.set_tooltip_text("Pick the map whose walkable cells contain the live entities")
        auto.connect("clicked", self._on_auto_map)
        top.append(auto)
        top.append(Gtk.Label(label="Scale"))
        self.map_scale_spin = Gtk.SpinButton.new_with_range(1, 10, 1)
        self.map_scale_spin.set_value(DEFAULT_SCALE)
        self.map_scale_spin.connect("value-changed", self._on_map_scale)
        top.append(self.map_scale_spin)
        self.map_info = Gtk.Label(label="map data not loaded")
        self.map_info.set_xalign(0)
        self.map_info.set_hexpand(True)
        top.append(self.map_info)
        page.append(top)

        self.map_area = Gtk.DrawingArea()
        self.map_area.set_draw_func(self._draw_map)
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_map_click)
        self.map_area.add_controller(click)
        self.map_scrolled = Gtk.ScrolledWindow()
        self.map_scrolled.set_child(self.map_area)
        self.map_scrolled.set_vexpand(True)
        page.append(self.map_scrolled)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bottom.set_margin_start(8)
        bottom.set_margin_end(8)
        bottom.set_margin_bottom(4)
        clear = Gtk.Button(label="Clear path")
        clear.connect("clicked", lambda *_: self._clear_path())
        bottom.append(clear)
        self.loop_toggle = Gtk.ToggleButton(label="Loop")
        self.loop_toggle.set_active(True)
        bottom.append(self.loop_toggle)
        start = Gtk.Button(label="Start")
        start.connect("clicked", lambda *_: self._start_bot())
        bottom.append(start)
        stop = Gtk.Button(label="Stop")
        stop.connect("clicked", lambda *_: self._stop_bot())
        bottom.append(stop)

        self.auto_attack_toggle = Gtk.ToggleButton(label="Auto-attack")
        bottom.append(self.auto_attack_toggle)
        bottom.append(Gtk.Label(label="Skill"))
        self.skill_entry = Gtk.Entry()
        self.skill_entry.set_width_chars(6)
        self.skill_entry.set_text("866")
        self.skill_entry.set_tooltip_text("skill vnum sent to the server")
        bottom.append(self.skill_entry)
        bottom.append(Gtk.Label(label="Range"))
        self.attack_range = Gtk.SpinButton.new_with_range(1, 40, 1)
        self.attack_range.set_value(10)
        bottom.append(self.attack_range)
        bottom.append(Gtk.Label(label="Cooldown"))
        self.attack_cooldown = Gtk.SpinButton.new_with_range(1, 30, 1)
        self.attack_cooldown.set_value(3)
        bottom.append(self.attack_cooldown)
        bottom.append(Gtk.Label(label="Char ID"))
        self.char_entry = Gtk.Entry()
        self.char_entry.set_width_chars(10)
        self.char_entry.set_placeholder_text("auto from rest")
        bottom.append(self.char_entry)
        page.append(bottom)

        self.skill_packet_entry = Gtk.Entry()
        self.skill_packet_entry.set_text("u_s {skill} {type} {target}")
        self.skill_packet_entry.set_tooltip_text(
            "template for auto-attack: {skill}, {type}, {target}"
        )
        self.skill_packet_entry.set_margin_start(8)
        self.skill_packet_entry.set_margin_end(8)
        page.append(self.skill_packet_entry)

        hint = Gtk.Label(
            label=(
                "Click the map to add waypoints; Start walks the A* path. "
                "Auto-attack sends ncif + the skill template when a monster is in range. "
                "The character must be standing; the rest packet toggles sit/stand."
            )
        )
        hint.set_xalign(0)
        hint.set_wrap(True)
        hint.set_margin_start(8)
        hint.set_margin_end(8)
        hint.set_margin_bottom(6)
        hint.add_css_class("dim-label")
        page.append(hint)
        return page

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b".recv { color: #2aa198; } .send { color: #b58900; }"
            b".dim-label { opacity: 0.7; }"
        )
        display = Gdk.Display.get_default()
        if display is not None and hasattr(Gtk.StyleContext, "add_provider_for_display"):
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    # -- map data -------------------------------------------------------

    def _ensure_map_loaded(self) -> None:
        if self.map_grids:
            return
        try:
            self.map_grids = load_nstc(self.map_file)
        except MapDataError as exc:
            self._set_status(f"map data: {exc}")
            if self.map_info is not None:
                self.map_info.set_text(str(exc))
            return

        map_id = None
        if self.engine.world.map_id in self.map_grids:
            map_id = self.engine.world.map_id
        elif DEFAULT_MAP_ID in self.map_grids:
            map_id = DEFAULT_MAP_ID
        else:
            map_id = next(iter(self.map_grids))
        self._select_map(map_id)

    def _select_map(self, map_id: int) -> None:
        grid = self.map_grids.get(map_id)
        if grid is None:
            self._set_status(f"map {map_id} not in {self.map_file}")
            return

        self.map_grid = grid
        self.map_id_spin.set_value(map_id)
        self.waypoints.clear()
        self.path_cells.clear()
        self.path_index = 0
        self.pending_target = None
        self._build_map_surface()
        self._update_map_size()
        total = grid.width * grid.height
        walkable = grid.walkable_count()
        self.map_info.set_text(
            f"map {grid.map_id}: {grid.width}x{grid.height}, "
            f"walkable {100 * walkable / total:.1f}%"
        )
        self._set_status(f"loaded map {grid.map_id} ({grid.width}x{grid.height})")

    def _build_map_surface(self) -> None:
        grid = self.map_grid
        if grid is None:
            self.map_surface = None
            return
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, grid.width, grid.height)
        context = cairo.Context(surface)
        for y in range(grid.height):
            for x in range(grid.width):
                red, green, blue = terrain_rgb(grid.at(x, y))
                context.set_source_rgb(red / 255, green / 255, blue / 255)
                context.rectangle(x, y, 1, 1)
                context.fill()
        self.map_surface = surface

    def _update_map_size(self) -> None:
        if self.map_grid is None or self.map_area is None:
            return
        self.map_scale = int(self.map_scale_spin.get_value())
        self.map_area.set_content_width(self.map_grid.width * self.map_scale)
        self.map_area.set_content_height(self.map_grid.height * self.map_scale)
        self.map_area.queue_draw()

    def _on_map_scale(self, _spin) -> None:
        self._update_map_size()

    def _on_load_map(self, _button) -> None:
        self._ensure_map_loaded()
        self._select_map(int(self.map_id_spin.get_value()))

    def _on_auto_map(self, _button) -> None:
        self._ensure_map_loaded()
        points = [
            (entity.x, entity.y)
            for entity in self.engine.world.entities.values()
            if entity.kind in (1, 2, 3)
        ]
        ranked = find_map_for_positions(self.map_grids, points)
        if not ranked:
            self._set_status("auto-detect: no entity positions yet")
            return
        fraction, map_id = ranked[0]
        self._select_map(map_id)
        self._set_status(f"auto-detected map {map_id} ({fraction * 100:.1f}% walkable)")

    # -- map drawing ----------------------------------------------------

    def _draw_map(self, _area, context, _width, _height) -> None:
        context.set_source_rgb(0.08, 0.08, 0.08)
        context.paint()
        if self.map_surface is None or self.map_grid is None:
            return

        scale = self.map_scale
        context.save()
        context.scale(scale, scale)
        context.set_source_surface(self.map_surface, 0, 0)
        context.paint()
        context.restore()

        grid = self.map_grid

        # A* path
        if self.path_cells:
            context.set_source_rgba(0.0, 0.9, 0.9, 0.85)
            size = max(1.0, scale * 0.45)
            for x, y in self.path_cells:
                context.rectangle(
                    x * scale + (scale - size) / 2,
                    y * scale + (scale - size) / 2,
                    size,
                    size,
                )
            context.fill()

        # waypoints
        context.set_source_rgba(1.0, 0.85, 0.1, 1.0)
        for x, y in self.waypoints:
            context.arc(
                x * scale + scale / 2,
                y * scale + scale / 2,
                max(2.0, scale * 0.6),
                0,
                2 * math.pi,
            )
            context.fill()

        # entities
        for entity in self.engine.world.entities.values():
            if not (0 <= entity.x < grid.width and 0 <= entity.y < grid.height):
                continue
            if entity.kind == 1:
                context.set_source_rgba(0.2, 0.9, 0.2, 0.9)
            elif entity.kind == 2:
                context.set_source_rgba(0.3, 0.5, 1.0, 0.9)
            else:
                context.set_source_rgba(1.0, 0.45, 0.1, 0.9)
            context.arc(
                entity.x * scale + scale / 2,
                entity.y * scale + scale / 2,
                max(2.0, scale * 0.45),
                0,
                2 * math.pi,
            )
            context.fill()

        # own character
        own = self._player_cell()
        if own is not None:
            x, y = own
            context.set_source_rgba(1.0, 0.1, 0.1, 1.0)
            context.arc(
                x * scale + scale / 2,
                y * scale + scale / 2,
                max(3.0, scale * 0.7),
                0,
                2 * math.pi,
            )
            context.fill()
            context.set_source_rgba(1.0, 1.0, 1.0, 1.0)
            context.set_line_width(1.5)
            context.arc(
                x * scale + scale / 2,
                y * scale + scale / 2,
                max(4.0, scale * 0.9),
                0,
                2 * math.pi,
            )
            context.stroke()

    def _on_map_click(self, gesture, _n_press, x, y) -> None:
        if self.map_grid is None:
            return
        scale = self.map_scale
        cell = (int(x // scale), int(y // scale))
        if not (
            0 <= cell[0] < self.map_grid.width
            and 0 <= cell[1] < self.map_grid.height
        ):
            return

        if gesture.get_current_button() == 3:
            if self.waypoints:
                self.waypoints.pop()
                self._rebuild_path()
            return

        if not self.map_grid.is_walkable(*cell):
            self._set_status(f"cell {cell} is blocked")
            return
        self.waypoints.append(cell)
        self._rebuild_path()

    def _clear_path(self) -> None:
        self.waypoints.clear()
        self.path_cells.clear()
        self.path_index = 0
        self.pending_target = None
        if self.map_area is not None:
            self.map_area.queue_draw()
        self._set_status("path cleared")

    def _player_cell(self) -> tuple[int, int] | None:
        # Prefer the protocol position (`at`/`walk`); the char entity from
        # `tp`/`mv` is the confirmed fallback. `cond` no longer creates a
        # phantom entity at (0,0).
        if self.engine.world.position is not None:
            return self.engine.world.position
        char_id = self._char_id()
        if char_id is not None:
            entity = self.engine.world.entities.get(char_id)
            if entity is not None:
                return (entity.x, entity.y)
        return None

    def _rebuild_path(self) -> None:
        self.path_cells = []
        self.path_index = 0
        if self.map_grid is None or not self.waypoints:
            if self.map_area is not None:
                self.map_area.queue_draw()
            return

        current = self._player_cell()
        if current is None or not self.map_grid.is_walkable(*current):
            current = self.waypoints[0]

        path: list[tuple[int, int]] = []
        for waypoint in self.waypoints:
            leg = astar(self.map_grid, current, waypoint)
            if leg is None:
                self._set_status(f"no path to {waypoint}")
                continue
            path.extend(leg)
            current = waypoint
        self.path_cells = path
        if self.map_area is not None:
            self.map_area.queue_draw()
        self._set_status(f"path: {len(path)} cells, {len(self.waypoints)} waypoint(s)")

    def _on_page_switch(self, _notebook, _page, _page_num) -> None:
        self._ensure_map_loaded()
        if self.map_area is not None:
            self.map_area.queue_draw()

    # -- connection -----------------------------------------------------

    def _start_client(self) -> None:
        self.client = PacketClient(self.host, self.port)

        def worker() -> None:
            try:
                self.client.connect(retries=50, delay=0.2)
            except ConnectionLost as exc:
                GLib.idle_add(self._set_status, f"not connected: {exc}")
                return

            GLib.idle_add(self._on_connected)

            try:
                while True:
                    message = self.client.recv(timeout=0.5)
                    if message is not None:
                        self.inbox.put(message)
            except (ConnectionLost, ProtocolError, OSError) as exc:
                GLib.idle_add(self._set_status, f"disconnected: {exc}")

        threading.Thread(target=worker, name="ntpl-gui-reader", daemon=True).start()
        GLib.timeout_add(DRAIN_INTERVAL_MS, self._drain)

    def _drain(self) -> bool:
        for _ in range(1000):
            try:
                message = self.inbox.get_nowait()
            except queue.Empty:
                break

            if message.kind is Kind.STATUS:
                self.status_received = True
                self._set_status(message.payload)
            elif message.kind is Kind.QUIT:
                self._set_status("disconnected")
            else:
                packet = Packet.from_message(message)
                self.engine.feed(packet)
                self._track_packet(packet)
                self._append(packet)
                if self.map_area is not None:
                    self.map_area.queue_draw()

        return True

    def _track_packet(self, packet: Packet) -> None:
        if packet.direction is not Kind.RECV:
            return
        if packet.opcode in ("tp", "mv"):
            values = packet.ints(4) if packet.opcode == "tp" else packet.ints(5)
            if values and values[0] == 1:
                self.last_move = (values[0], values[1], values[2], values[3])
                self.last_move_time = time.monotonic()
        elif packet.opcode == "rest":
            values = packet.ints(3)
            if values and values[0] == 1:
                self.char_id = values[1]

    # -- callbacks ------------------------------------------------------

    def _append(self, packet: Packet) -> None:
        self.store.append(PacketRow(packet))
        if self.store.get_n_items() > MAX_ROWS:
            self.store.splice(0, TRIM_ROWS, [])
        if self.follow.get_active():
            GLib.idle_add(self._scroll_to_end)

    def _scroll_to_end(self) -> bool:
        adjustment = self.scrolled.get_vadjustment()
        adjustment.set_value(
            max(0.0, adjustment.get_upper() - adjustment.get_page_size())
        )
        return False

    def _matches(self, row: PacketRow) -> bool:
        if not self.filter_text:
            return True
        return (
            self.filter_text in row.text.lower()
            or self.filter_text in row.opcode.lower()
        )

    def _on_search(self, _entry) -> None:
        self.filter_text = self.search.get_text().strip().lower()
        self.filter.changed(Gtk.FilterChange.DIFFERENT)

    def _on_send(self, _entry) -> None:
        text = self.send_entry.get_text().strip()
        if not text:
            return

        try:
            self.client.send_packet(text)
        except (ConnectionLost, ValueError) as exc:
            self._set_status(f"send failed: {exc}")
        else:
            self.send_entry.set_text("")

    def _on_connected(self) -> None:
        if not self.status_received:
            self._set_status(f"connected to {self.host}:{self.client.port}")

    def _set_status(self, text: str) -> None:
        if self.status is not None:
            self.status.set_text(text)

    # -- bot ------------------------------------------------------------

    def _char_id(self) -> int | None:
        if self.char_entry is not None:
            text = self.char_entry.get_text().strip()
            if text.isdigit():
                return int(text)
        return self.char_id

    def _send(self, text: str) -> bool:
        if self.client is None or not self.client.connected:
            self._set_status("not connected")
            return False
        try:
            self.client.send_packet(text)
        except (ConnectionLost, ValueError) as exc:
            self._set_status(f"send failed: {exc}")
            self._stop_bot()
            return False
        return True

    def _start_bot(self) -> None:
        if self.bot_running:
            return
        if not self.path_cells:
            self._set_status("add waypoints first (click the map)")
            return
        self.bot_running = True
        self.path_index = 0
        self.pending_target = None
        self.stand_sent_until = 0.0
        self.attack_cooldown_until = 0.0
        if self.bot_timer is None:
            self.bot_timer = GLib.timeout_add(BOT_INTERVAL_MS, self._bot_tick)
        self._set_status("bot started")

    def _stop_bot(self) -> None:
        self.bot_running = False
        self.pending_target = None
        if self.bot_timer is not None:
            GLib.source_remove(self.bot_timer)
            self.bot_timer = None
        self._set_status("bot stopped")

    def _advance_path(self) -> None:
        self.path_index += 1
        if self.path_index >= len(self.path_cells):
            if self.loop_toggle is not None and self.loop_toggle.get_active():
                self.path_index = 0
            else:
                self._set_status("path finished")
                self._stop_bot()

    def _nearest_monster(self, radius: int) -> Entity | None:
        position = self._player_cell()
        if position is None:
            return None
        px, py = position
        best: Entity | None = None
        best_distance = radius + 1
        for entity in self.engine.world.entities.values():
            if entity.kind != 3:
                continue
            distance = max(abs(entity.x - px), abs(entity.y - py))
            if distance <= radius and distance < best_distance:
                best = entity
                best_distance = distance
        return best

    def _skill_packet(self, target_id: int) -> str:
        skill = self.skill_entry.get_text().strip() or "866"
        template = (
            self.skill_packet_entry.get_text().strip()
            or "u_s {skill} {type} {target}"
        )
        try:
            return template.format(skill=skill, type=3, target=target_id)
        except (KeyError, ValueError):
            return template

    def _bot_tick(self) -> bool:
        if not self.bot_running:
            self.bot_timer = None
            return False
        if self.client is None or not self.client.connected:
            self._set_status("not connected; bot stopped")
            self._stop_bot()
            return False

        now = time.monotonic()
        world = self.engine.world

        if self.pending_target is not None:
            confirmed = (
                self.last_move_time >= self.pending_since
                and self.last_move is not None
                and self.last_move[0] == 1
                and (self.last_move[2], self.last_move[3]) == self.pending_target
            )
            if confirmed:
                self._set_status(f"arrived at {self.pending_target}")
                self.pending_target = None
                self._advance_path()
            elif now > self.pending_until:
                self._set_status(f"walk to {self.pending_target} not confirmed; skipping")
                self.pending_target = None
                self._advance_path()
            return True

        char_id = self._char_id()
        if world.resting is True and char_id is not None and now >= self.stand_sent_until:
            self._send(f"rest 1 1 {char_id}")
            self.stand_sent_until = now + 2.0
            self._set_status("standing up")
            return True

        if (
            self.auto_attack_toggle is not None
            and self.auto_attack_toggle.get_active()
            and now >= self.attack_cooldown_until
        ):
            monster = self._nearest_monster(int(self.attack_range.get_value()))
            if monster is not None:
                self._send(f"ncif 3 {monster.entity_id}")
                packet = self._skill_packet(monster.entity_id)
                self._send(packet)
                cooldown = float(self.attack_cooldown.get_value())
                self.attack_cooldown_until = now + cooldown
                self._set_status(f"attack {monster.entity_id}: {packet}")
                return True

        if not self.path_cells:
            self._set_status("path finished")
            self._stop_bot()
            return False

        if self.path_index >= len(self.path_cells):
            if self.loop_toggle.get_active():
                self.path_index = 0
            else:
                self._set_status("path finished")
                self._stop_bot()
                return False

        target = self.path_cells[self.path_index]
        if self._send(f"walk {target[0]} {target[1]} 0 10"):
            self.pending_target = target
            self.pending_since = now
            self.pending_until = now + 3.0
            self._set_status(f"walking to {target}")
        return True

    def do_shutdown(self) -> None:
        self._stop_bot()
        if self.client is not None:
            self.client.close()
        Gtk.Application.do_shutdown(self)


def run_gui(
    host: str = "127.0.0.1",
    port: int | None = None,
    map_data: str | None = None,
) -> int:
    app = Application(host, port, map_data)
    return int(app.run([]))
