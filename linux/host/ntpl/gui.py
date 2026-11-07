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

from .botconfig import (
    COMPARATORS,
    MONSTER_MODES,
    SPELL_TARGETS,
    SUBJECTS,
    BotConfig,
    ConfigError,
    SpellRule,
    load_config,
    save_config,
)
from .client import ConnectionLost, PacketClient
from .engine import Engine, Entity, GroundItem
from .mapdata import (
    DEFAULT_NSTC,
    MapDataError,
    MapGrid,
    astar,
    choose_map_for_positions,
    load_nstc,
    terrain_rgb,
)
from .monsternames import load_monster_names, load_skill_descriptions
from .spawnlog import load_skill_list, load_skill_slots, load_spawns
from .spells import SpellInfo, load_spells
from .protocol import Kind, Packet, ProtocolError

MAX_ROWS = 20000
TRIM_ROWS = 2000
DRAIN_INTERVAL_MS = 50
BOT_INTERVAL_MS = 200
DEFAULT_MAP_ID = 20
DEFAULT_SCALE = 3
# Live positions fed to the attach-time map ranking. More points separate
# same-layout maps; ranking 779 grids costs ~16 ms at this cap.
AUTO_MAP_POINTS = 512
# The server ignores a walk sent during the skill animation. The bot attacks,
# then waits this long before it sends the next path step.
ATTACK_WALK_DELAY = 1.5
# A walk timed out this many times in a row: the server is rejecting the
# position (wrong self position, sitting, or an unsettled client). Stop instead
# of walking further away from the real character.
MAX_REJECTED_WALKS = 3
RECONNECT_DELAY = 1.0
# Movement goes through the client's own MoveTo function in the DLL:
#   MOVE|x y  -> pathfinding + local animation + the client's own walk packets
#   POS       -> local player x, y, char id
# Injecting `walk` packets moves only the server; the client then snaps when a
# correction arrives. MOVE avoids that.
MOVE_PREFIX = "MOVE|"
POS_COMMAND = "POS"


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
    def __init__(
        self,
        host: str,
        port: int | None,
        map_data: str | None = None,
        unique: bool = True,
        log_file: str | None = None,
    ) -> None:
        super().__init__(
            application_id="cz.tarajura.ntpl",
            # Tests must not attach to a GUI that is already running for the
            # same user; NON_UNIQUE gives each test process its own app.
            flags=(
                Gio.ApplicationFlags.DEFAULT_FLAGS
                if unique
                else Gio.ApplicationFlags.NON_UNIQUE
            ),
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
        self.monster_names: dict[int, str] = {}
        self.spell_descriptions: dict[int, str] = {}
        self.spells: dict[int, SpellInfo] = {}
        self.spells_loaded = False
        self.spells_loading = False
        self.monster_names_loaded = False
        self.log_file = log_file or os.environ.get("NTPL_LOG")
        self.log_vnums: dict[int, int] = {}
        self.log_names: dict[int, str] = {}
        self.log_skill_list: list[int] = []
        self.log_skill_slots: dict[int, int] = {}
        self.spawns_loaded = False
        self.target_mode = None
        self.target_search = None
        self.target_list = None
        self.target_info = None
        self.target_vnum_entry = None
        self.target_vnums: set[int] = set()
        self.spell_rules_box = None
        self.spell_rows: list[dict] = []
        self.spell_last_cast: dict[int, float] = {}
        self.config_info = None
        self.spell_picker_window = None
        self.map_surface: cairo.ImageSurface | None = None
        self.map_area = None
        self.map_scrolled = None
        self.map_page_box = None
        self.map_box = None
        self.sidebar_scroll = None
        self.narrow_layout = False
        self.map_info = None
        self.skill_entry = None
        self.skill_packet_entry = None
        self.combat_info = None
        self._combat_info_text = ""
        self.attack_range = None
        self.attack_cooldown = None
        self.mp_warn_at = 0.0
        self.slot_warn_at = 0.0
        self._target_map_vnums: frozenset[int] | None = None
        self.auto_attack_toggle = None
        self.loop_toggle = None
        self.pickup_toggle = None
        self.recover_toggle = None
        self.attack_unknown_toggle = None
        self.recover_banner = None
        self.unknown_warn_at = 0.0
        self.loot_range = None

        self.waypoints: list[tuple[int, int]] = []
        self.path_cells: list[tuple[int, int]] = []
        self.bot_goals: list[tuple[int, int]] = []
        self.path_index = 0
        self.pending_target: tuple[int, int] | None = None
        self.pending_since = 0.0
        self.pending_until = 0.0
        self.stand_sent_until = 0.0
        self.attack_cooldown_until = 0.0
        self.walk_not_before = 0.0
        self.rejected_walks = 0
        self.attack_walk_delay = ATTACK_WALK_DELAY
        self.walk_timeout = 3.0
        self.walk_timeout_per_cell = 2.5
        # The client sends one walk packet per MOVE with the destination, so
        # the packet arrives before the animation finishes. Wait roughly this
        # long per cell before sending the next leg.
        self.walk_confirm_delay = 0.45
        self.pending_distance = 0
        self.pickup_cooldown_until = 0.0
        self.pickup_attempts: dict[int, int] = {}
        self.pickup_ignored: dict[int, float] = {}
        self.recovering = False
        self.recover_rest_at = 0.0
        self.map_redraw_scheduled = False
        self.stats_redraw_scheduled = False
        self.bot_running = False
        self.start_when_located = False
        self.bot_timer = None
        self._closing = False

    # -- UI -------------------------------------------------------------

    def do_activate(self) -> None:
        active = self.get_active_window()
        if active is not None:
            active.present()
            return

        # Install the provider before building widgets: a CSS class added to a
        # toggle that was already active before the provider existed did not
        # restyle until its state changed, which left Retaliate/Move first/Loop
        # grey instead of green.
        self._install_css()
        self._build_ui()
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
        self._ensure_monster_names()
        self._ensure_spawns_loaded()
        # Skill.dat decodes 1.2 MB of nibble-coded text (~1.5 s). Do it off
        # the main loop so the window and the packet drain stay responsive.
        self._ensure_spells_loaded()
        GLib.timeout_add(250, self._layout_tick)
        GLib.timeout_add(2000, self._auto_map_tick)
        GLib.timeout_add(1000, self._live_sidebar_tick)

    def _layout_tick(self) -> bool:
        """Reflow the Map/Bot page when the window becomes narrow.

        GTK4 widgets have no width property and no size-allocate signal, so a
        cheap 250 ms poll keeps the layout responsive without a custom
        container. Wide: map | sidebar. Narrow: map over sidebar.
        """
        if self._closing:
            return False
        if self.window is None or self.map_page_box is None:
            return True
        narrow = self.window.get_width() < 1050
        if narrow == self.narrow_layout:
            return True
        self.narrow_layout = narrow
        if narrow:
            self.map_page_box.set_orientation(Gtk.Orientation.VERTICAL)
            if self.map_box is not None:
                self.map_box.set_vexpand(True)
                self.map_box.set_size_request(-1, 380)
            if self.sidebar_scroll is not None:
                self.sidebar_scroll.set_hexpand(True)
                self.sidebar_scroll.set_vexpand(True)
                self.sidebar_scroll.set_size_request(-1, 260)
        else:
            self.map_page_box.set_orientation(Gtk.Orientation.HORIZONTAL)
            if self.map_box is not None:
                self.map_box.set_vexpand(True)
                self.map_box.set_size_request(-1, -1)
            if self.sidebar_scroll is not None:
                self.sidebar_scroll.set_hexpand(False)
                self.sidebar_scroll.set_vexpand(True)
                self.sidebar_scroll.set_size_request(360, -1)
        if self.map_area is not None:
            self.map_area.queue_draw()
        return True

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

        self.follow = Gtk.ToggleButton(label="Follow")
        self.follow.set_active(True)
        self.follow.set_tooltip_text("Scroll to the newest packet automatically")
        toolbar.append(self.follow)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.append(toolbar)
        page.append(self.scrolled)
        return page

    @staticmethod
    def _frame(title: str, child: Gtk.Widget) -> Gtk.Frame:
        frame = Gtk.Frame(label=title)
        frame.set_child(child)
        return frame

    @staticmethod
    def _field_row(*widgets: Gtk.Widget) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for widget in widgets:
            row.append(widget)
        return row

    def _bind_toggle_color(self, button: Gtk.ToggleButton) -> None:
        """Paint an active toggle green and an inactive one red."""
        button.connect("toggled", self._update_toggle_color)
        self._update_toggle_color(button)

    @staticmethod
    def _update_toggle_color(button: Gtk.ToggleButton) -> None:
        button.remove_css_class("ntpl-on")
        button.remove_css_class("ntpl-off")
        button.add_css_class(
            "ntpl-on" if button.get_active() else "ntpl-off"
        )

    def _build_map_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        page.set_margin_top(6)
        page.set_margin_start(8)
        page.set_margin_end(8)
        page.set_margin_bottom(6)

        # -- left: map ----------------------------------------------------
        map_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        map_box.set_hexpand(True)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.map_info = Gtk.Label(label="map data not loaded")
        self.map_info.set_xalign(0)
        self.map_info.set_hexpand(True)
        top.append(self.map_info)
        map_box.append(top)

        self.map_area = Gtk.DrawingArea()
        self.map_area.set_draw_func(self._draw_map)
        self.map_area.set_hexpand(True)
        self.map_area.set_vexpand(True)
        self.map_area.set_has_tooltip(True)
        self.map_area.connect("query-tooltip", self._on_map_query_tooltip)
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_map_click)
        self.map_area.add_controller(click)
        self.map_scrolled = None
        map_box.append(self.map_area)

        legend = Gtk.Label()
        legend.set_markup(
            '<span foreground="#ff3333">●</span> you   '
            '<span foreground="#ff7322">●</span> monster   '
            '<span foreground="#33e633">●</span> player   '
            '<span foreground="#4d80ff">●</span> NPC/pet   '
            '<span foreground="#ffd91a">●</span> drop   '
            '<span foreground="#00e5e5">·</span> path'
        )
        legend.set_xalign(0)
        legend.add_css_class("dim-label")
        map_box.append(legend)

        # -- right: structured control sidebar ----------------------------
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        sidebar.set_margin_top(2)
        sidebar.set_margin_bottom(2)
        sidebar.set_margin_start(2)
        sidebar.set_margin_end(2)

        # Character: live HP/MP bars and position.
        char_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.stats_area = Gtk.DrawingArea()
        self.stats_area.set_content_height(92)
        self.stats_area.set_draw_func(self._draw_stats)
        char_box.append(self.stats_area)
        self.char_info = Gtk.Label(label="position unknown")
        self.char_info.set_xalign(0)
        self.char_info.set_wrap(True)
        self.char_info.set_max_width_chars(38)
        self.char_info.add_css_class("dim-label")
        char_box.append(self.char_info)
        self.recover_banner = Gtk.Label()
        self.recover_banner.add_css_class("recover-banner")
        self.recover_banner.set_xalign(0)
        self.recover_banner.set_wrap(True)
        self.recover_banner.set_max_width_chars(38)
        self.recover_banner.set_visible(False)
        char_box.append(self.recover_banner)
        sidebar.append(self._frame("Character", char_box))

        # Path.
        path_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.path_info = Gtk.Label(label="no path")
        self.path_info.set_xalign(0)
        self.path_info.set_wrap(True)
        self.path_info.set_max_width_chars(38)
        path_box.append(self.path_info)
        path_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        start = Gtk.Button(label="Start")
        start.add_css_class("suggested-action")
        start.connect("clicked", lambda *_: self._start_bot())
        path_row.append(start)
        stop = Gtk.Button(label="Stop")
        stop.connect("clicked", lambda *_: self._stop_bot())
        path_row.append(stop)
        clear = Gtk.Button(label="Clear path")
        clear.connect("clicked", lambda *_: self._clear_path())
        path_row.append(clear)
        path_box.append(path_row)
        self.loop_toggle = Gtk.ToggleButton(label="Loop")
        self.loop_toggle.set_active(True)
        self._bind_toggle_color(self.loop_toggle)
        path_box.append(self.loop_toggle)
        sidebar.append(self._frame("Path", path_box))

        # Combat.
        combat_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.auto_attack_toggle = Gtk.ToggleButton(label="Auto-attack")
        self.retaliate_toggle = Gtk.ToggleButton(label="Retaliate")
        self.retaliate_toggle.set_active(True)
        self.retaliate_toggle.set_tooltip_text(
            "Prioritise the monster that is currently hitting you"
        )
        self.move_first_toggle = Gtk.ToggleButton(label="Move first")
        self.move_first_toggle.set_active(True)
        self.move_first_toggle.set_tooltip_text(
            "Attack while following the path and keep walking between "
            "attacks; turn off to clear the area before moving on"
        )
        for toggle in (
            self.auto_attack_toggle,
            self.retaliate_toggle,
            self.move_first_toggle,
        ):
            self._bind_toggle_color(toggle)
        combat_box.append(
            self._field_row(
                self.auto_attack_toggle,
                self.retaliate_toggle,
                self.move_first_toggle,
            )
        )
        self.skill_entry = Gtk.Entry()
        self.skill_entry.set_width_chars(6)
        # Skill 0 is the equipped SP card's basic attack (`u_s 0 3 <monster>`);
        # the server reports its vnum in `su` (260 Energy Bolt on the mage).
        self.skill_entry.set_text("0")
        self.skill_entry.set_tooltip_text(
            "skill vnum; mapped to the u_s skill slot when known "
            "(0 = the SP card's basic attack)"
        )
        self.skill_entry.connect(
            "changed", lambda *_: self._update_combat_info()
        )
        self.attack_range = Gtk.SpinButton.new_with_range(1, 40, 1)
        self.attack_range.set_value(10)
        self.attack_range.set_tooltip_text(
            "target search radius in cells; the attack itself is limited by "
            "the skill's own range (shown below)"
        )
        self.attack_cooldown = Gtk.SpinButton.new_with_range(1, 30, 1)
        self.attack_cooldown.set_value(1)
        self.attack_cooldown.set_tooltip_text(
            "minimum delay between actions; the skill's own cast time and "
            "cooldown (shown below) are enforced on top"
        )
        combat_box.append(
            self._field_row(
                Gtk.Label(label="Skill"),
                self.skill_entry,
                Gtk.Label(label="Search"),
                self.attack_range,
            )
        )
        combat_box.append(
            self._field_row(
                Gtk.Label(label="Min delay"),
                self.attack_cooldown,
            )
        )
        self.combat_info = Gtk.Label(label="reading Skill.dat…")
        self.combat_info.set_xalign(0)
        self.combat_info.set_wrap(True)
        self.combat_info.add_css_class("dim-label")
        self.combat_info.set_tooltip_text(
            "range, cast time, cooldown and MP from Skill.dat for the skill "
            "the bot is using; skill 0 follows the equipped SP card"
        )
        combat_box.append(self.combat_info)
        self.skill_packet_entry = Gtk.Entry()
        self.skill_packet_entry.set_text("u_s {skill} {type} {target}")
        self.skill_packet_entry.set_tooltip_text(
            "template for auto-attack: {skill}, {type}, {target}"
        )
        combat_box.append(self.skill_packet_entry)
        sidebar.append(self._frame("Combat", combat_box))

        # Targets: restrict auto-attack to selected monster templates.
        target_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.target_mode = Gtk.DropDown.new_from_strings(
            ["All monsters", "Only selected", "All except selected"]
        )
        self.target_mode.set_selected(0)
        self.target_mode.set_tooltip_text(
            "Filter auto-attack and Retaliate by monster template (vnum)"
        )
        target_box.append(
            self._field_row(Gtk.Label(label="Attack"), self.target_mode)
        )
        self.attack_unknown_toggle = Gtk.CheckButton(
            label="Also attack monsters with unknown vnum"
        )
        self.attack_unknown_toggle.set_tooltip_text(
            "Monsters that spawned before the DLL was injected have no `in` "
            "packet in the log, so their vnum cannot be matched. Enable this "
            "to attack them anyway in 'Only selected' mode."
        )
        target_box.append(self.attack_unknown_toggle)
        self.target_search = Gtk.SearchEntry()
        self.target_search.set_placeholder_text("Search monster name or vnum")
        self.target_search.connect(
            "search-changed", lambda *_: self._refresh_target_list()
        )
        target_box.append(self.target_search)

        self.target_list = Gtk.ListBox()
        self.target_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.target_list.set_activate_on_single_click(False)
        target_scroll = Gtk.ScrolledWindow()
        target_scroll.set_child(self.target_list)
        target_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        target_scroll.set_min_content_height(150)
        target_scroll.set_max_content_height(220)
        target_box.append(target_scroll)

        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.target_vnum_entry = Gtk.Entry()
        self.target_vnum_entry.set_width_chars(8)
        self.target_vnum_entry.set_placeholder_text("vnum")
        add_vnum = Gtk.Button(label="Add vnum")
        add_vnum.connect("clicked", lambda *_: self._add_target_vnum())
        clear_targets = Gtk.Button(label="Clear")
        clear_targets.connect("clicked", lambda *_: self._clear_targets())
        add_row.append(self.target_vnum_entry)
        add_row.append(add_vnum)
        add_row.append(clear_targets)
        target_box.append(add_row)

        self.target_info = Gtk.Label(label="no targets selected")
        self.target_info.set_xalign(0)
        self.target_info.set_wrap(True)
        self.target_info.set_max_width_chars(38)
        self.target_info.add_css_class("dim-label")
        target_box.append(self.target_info)
        sidebar.append(self._frame("Targets", target_box))

        # Spells: conditional cast rules, evaluated top to bottom.
        spell_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.spell_rules_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6
        )
        spell_box.append(self.spell_rules_box)
        spell_buttons = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        add_spell = Gtk.Button(label="Add spell…")
        add_spell.set_tooltip_text("Pick a scanned spell and add a rule")
        add_spell.connect(
            "clicked",
            lambda *_: self._open_spell_picker(self._add_spell_from_info),
        )
        add_blank = Gtk.Button(label="Blank rule")
        add_blank.set_tooltip_text("Add an empty rule and enter a vnum")
        add_blank.connect("clicked", lambda *_: self._add_spell_rule())
        spell_buttons.append(add_spell)
        spell_buttons.append(add_blank)
        spell_box.append(spell_buttons)
        spell_hint = Gtk.Label(
            label=(
                "Requires Auto-attack. Rules execute top to bottom; each "
                "fires when its condition is true and its cooldown is over. "
                "Use Always for a rotation. No match falls back to Skill in "
                "Combat."
            )
        )
        spell_hint.set_xalign(0)
        spell_hint.set_wrap(True)
        spell_hint.set_max_width_chars(34)
        spell_hint.add_css_class("dim-label")
        spell_box.append(spell_hint)
        sidebar.append(self._frame("Spells", spell_box))

        # Loot.
        loot_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.pickup_toggle = Gtk.ToggleButton(label="Pickup")
        self.pickup_toggle.set_active(True)
        self.pickup_toggle.set_tooltip_text(
            "Pick up your own drops within the loot range"
        )
        self._bind_toggle_color(self.pickup_toggle)
        self.loot_range = Gtk.SpinButton.new_with_range(1, 10, 1)
        self.loot_range.set_value(3)
        self.loot_range.set_tooltip_text("pickup radius in cells")
        loot_box.append(
            self._field_row(
                self.pickup_toggle,
                Gtk.Label(label="Radius"),
                self.loot_range,
            )
        )
        sidebar.append(self._frame("Loot", loot_box))

        # Recovery.
        recover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.recover_toggle = Gtk.ToggleButton(label="Auto-recover")
        self.recover_toggle.set_active(True)
        self.recover_toggle.set_tooltip_text(
            "Below the threshold, sit and wait for full HP"
        )
        self._bind_toggle_color(self.recover_toggle)
        self.low_hp_pct = Gtk.SpinButton.new_with_range(5, 90, 5)
        self.low_hp_pct.set_value(30)
        recover_box.append(
            self._field_row(
                self.recover_toggle,
                Gtk.Label(label="Below HP %"),
                self.low_hp_pct,
            )
        )
        sidebar.append(self._frame("Recovery", recover_box))

        # Configuration: export/import every bot setting as JSON.
        config_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        config_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        save_button = Gtk.Button(label="Save config…")
        save_button.set_tooltip_text("Export every bot setting as JSON")
        save_button.connect("clicked", self._on_save_config_clicked)
        load_button = Gtk.Button(label="Load config…")
        load_button.set_tooltip_text("Import a bot configuration from JSON")
        load_button.connect("clicked", self._on_load_config_clicked)
        config_row.append(save_button)
        config_row.append(load_button)
        config_box.append(config_row)
        self.config_info = Gtk.Label(label="not saved yet")
        self.config_info.set_xalign(0)
        self.config_info.set_wrap(True)
        self.config_info.set_max_width_chars(38)
        self.config_info.add_css_class("dim-label")
        config_box.append(self.config_info)
        sidebar.append(self._frame("Configuration", config_box))

        hint = Gtk.Label(
            label=(
                "Click the map to add waypoints; Start walks the A* path. "
                "Combat picks the monster hitting you first (Retaliate), then "
                "the nearest one. Low HP sits until full."
            )
        )
        hint.set_xalign(0)
        hint.set_wrap(True)
        hint.set_max_width_chars(42)
        hint.add_css_class("dim-label")
        sidebar.append(hint)

        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_child(sidebar)
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_size_request(360, -1)
        # Explicit False (not just the default) stops the expand flag from
        # propagating up from a wide child; without it GTK gave the sidebar
        # half of the extra window width and shrank the map.
        sidebar_scroll.set_hexpand(False)
        sidebar_scroll.set_vexpand(True)

        # A plain box instead of a Paned: the map shrinks with the window and
        # the control sidebar always stays visible. Below ~1050 px the layout
        # reflows to map-over-controls (see `_layout_tick`).
        page.append(map_box)
        page.append(sidebar_scroll)
        self.map_page_box = page
        self.map_box = map_box
        self.sidebar_scroll = sidebar_scroll
        return page

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b".recv { color: #2aa198; } .send { color: #b58900; }"
            b".dim-label { opacity: 0.7; }"
            b"button.ntpl-on { background-image: none;"
            b" background-color: #2e7d32; color: #ffffff; }"
            b"button.ntpl-on:hover { background-color: #388e3c; }"
            b"button.ntpl-off { background-image: none;"
            b" background-color: #b71c1c; color: #ffffff; }"
            b"button.ntpl-off:hover { background-color: #d32f2f; }"
            b".recover-banner { background-color: #b71c1c; color: #ffffff;"
            b" padding: 4px 6px; border-radius: 4px; }"
        )
        display = Gdk.Display.get_default()
        if display is not None and hasattr(Gtk.StyleContext, "add_provider_for_display"):
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    # -- map data -------------------------------------------------------

    def _ensure_monster_names(self) -> None:
        """Load the client's vnum → monster/skill text tables once."""
        if self.monster_names_loaded:
            return
        self.monster_names_loaded = True
        self.monster_names = load_monster_names()
        self.spell_descriptions = load_skill_descriptions()
        if self.target_list is not None:
            self._refresh_target_list()
            self._update_target_info()

    def _ensure_spells_loaded(self) -> None:
        if self.spells_loaded or self.spells_loading:
            return
        self.spells_loading = True

        def work() -> None:
            spells = load_spells()
            GLib.idle_add(self._finish_spells_load, spells)

        threading.Thread(target=work, daemon=True).start()

    def _finish_spells_load(
        self, spells: dict[int, SpellInfo]
    ) -> bool:
        self.spells = spells
        self.spells_loaded = True
        self.spells_loading = False
        for info in self.spell_rows:
            self._update_spell_desc(info)
        self._update_combat_info()
        return False

    def _ensure_spawns_loaded(self) -> None:
        """Backfill entity → vnum/name from the DLL's session log.

        The live packet stream starts when the host connects; monsters and
        players that spawned before that are known only from `mv`/`tp`. Their
        `in` packets are still in the DLL log, which covers the whole session.
        """
        if self.spawns_loaded:
            return
        self.spawns_loaded = True
        self.log_vnums, self.log_names = load_spawns(self.log_file)
        self.log_skill_list = load_skill_list(self.log_file)
        self.log_skill_slots, _ = load_skill_slots(self.log_file)

    # -- target filter --------------------------------------------------

    def _current_monster_mode(self) -> str:
        if self.target_mode is None:
            return "all"
        index = self.target_mode.get_selected()
        if 0 <= index < len(MONSTER_MODES):
            return MONSTER_MODES[index]
        return "all"

    def _monster_vnum(self, entity: Entity) -> int:
        return entity.vnum or self.log_vnums.get(entity.entity_id, 0)

    def _monster_allowed(self, entity: Entity) -> bool:
        mode = self._current_monster_mode()
        if mode == "all":
            return True
        vnum = self._monster_vnum(entity)
        if mode == "only":
            if vnum == 0:
                return bool(
                    self.attack_unknown_toggle is not None
                    and self.attack_unknown_toggle.get_active()
                )
            return vnum in self.target_vnums
        return vnum not in self.target_vnums

    def _unknown_monsters_in_range(self, radius: int) -> int:
        position = self._player_cell()
        if position is None:
            return 0
        px, py = position
        count = 0
        for entity in self.engine.world.entities.values():
            if entity.kind != 3 or self._monster_vnum(entity) != 0:
                continue
            if max(abs(entity.x - px), abs(entity.y - py)) <= radius:
                count += 1
        return count

    def _update_target_info(self) -> None:
        if self.target_info is None:
            return
        on_map = len(self._on_map_monster_vnums())
        self.target_info.set_text(
            f"{len(self.target_vnums)} selected · "
            f"{on_map} monster kind(s) on this map"
        )

    def _monster_display_name(self, vnum: int) -> str:
        return self.monster_names.get(vnum, f"vnum {vnum}")

    def _refresh_target_list(self) -> None:
        """List the monsters spawned on the current map, not the whole catalog.

        The static table holds 2,795 entries; showing its first 40 was the
        confusing part. Selections that are not on this map stay visible (so
        they can be unchecked) and are marked, instead of hiding the choice.
        """
        if self.target_list is None:
            return
        while (child := self.target_list.get_first_child()) is not None:
            self.target_list.remove(child)

        query = (
            self.target_search.get_text().strip().lower()
            if self.target_search is not None
            else ""
        )
        on_map = self._on_map_monster_vnums()
        self._target_map_vnums = on_map
        rows: list[tuple[int, str]] = []
        if query:
            matches = []
            for vnum in on_map:
                name = self._monster_display_name(vnum)
                if (
                    query in name.lower()
                    or str(vnum) == query
                    or query in str(vnum)
                ):
                    matches.append((vnum, f"{name} ({vnum})"))
            matches.sort(
                key=lambda item: (
                    0 if str(item[0]) == query else 1,
                    item[1].lower(),
                    item[0],
                )
            )
            rows = matches[:60]
        else:
            for vnum in sorted(
                self.target_vnums,
                key=lambda value: (
                    self._monster_display_name(value).lower(),
                    value,
                ),
            ):
                name = self._monster_display_name(vnum)
                suffix = "" if vnum in on_map else ", not on this map"
                rows.append((vnum, f"{name} ({vnum}{suffix})"))
            for vnum in sorted(
                (v for v in on_map if v not in self.target_vnums),
                key=lambda value: (
                    self._monster_display_name(value).lower(),
                    value,
                ),
            ):
                name = self._monster_display_name(vnum)
                rows.append((vnum, f"{name} ({vnum})"))

        if not rows:
            empty = Gtk.Label(
                label=(
                    "no matching monsters on this map"
                    if query
                    else "no monsters on this map yet"
                )
            )
            empty.add_css_class("dim-label")
            empty.set_xalign(0)
            row = Gtk.ListBoxRow()
            row.set_child(empty)
            row.set_selectable(False)
            row.set_activatable(False)
            self.target_list.append(row)
            return

        for vnum, label in rows:
            check = Gtk.CheckButton(label=label)
            check.set_active(vnum in self.target_vnums)
            check.set_tooltip_text(f"monster vnum {vnum}")
            check.connect(
                "toggled",
                lambda button, value=vnum: self._on_target_toggled(
                    value, button
                ),
            )
            row = Gtk.ListBoxRow()
            row.set_child(check)
            row.set_selectable(False)
            row.set_activatable(False)
            self.target_list.append(row)

    def _on_target_toggled(self, vnum: int, button: Gtk.CheckButton) -> None:
        if button.get_active():
            self.target_vnums.add(vnum)
        else:
            self.target_vnums.discard(vnum)
        self._update_target_info()

    def _add_target_vnum(self) -> None:
        if self.target_vnum_entry is None:
            return
        text = self.target_vnum_entry.get_text().strip()
        try:
            vnum = int(text)
        except ValueError:
            self._set_status("target vnum must be a number")
            return
        if vnum <= 0:
            self._set_status("target vnum must be positive")
            return
        self.target_vnums.add(vnum)
        self.target_vnum_entry.set_text("")
        self._refresh_target_list()
        self._update_target_info()

    def _clear_targets(self) -> None:
        self.target_vnums.clear()
        if self.target_search is not None:
            self.target_search.set_text("")
        self._refresh_target_list()
        self._update_target_info()

    # -- spell rules ----------------------------------------------------

    @staticmethod
    def _combat_stats_text(info: SpellInfo) -> str:
        """Name and the Skill.dat values the bot actually enforces."""
        parts = [f"{info.name} ({info.vnum})"]
        if info.range:
            parts.append(f"range {info.range}")
        parts.append(f"cast {info.cast_time:g}s")
        parts.append(f"CD {info.cooldown:g}s")
        parts.append(f"{info.mp_cost} MP")
        return " · ".join(parts)

    def _update_combat_info(self) -> None:
        """Show the real range/cast/cooldown/MP the bot uses.

        Skill 0 is the client's basic attack, whose vnum depends on the
        equipped SP card; it is detected from `u_s 0` + `su`, not hardcoded.
        """
        if self.combat_info is None:
            return
        skill = self._entry_int(self.skill_entry, 0)
        if not self.spells_loaded:
            text = "reading Skill.dat…"
        elif skill > 0:
            info = self.spells.get(skill)
            text = (
                self._combat_stats_text(info)
                if info is not None
                else f"skill {skill}: not in Skill.dat"
            )
        else:
            vnum = self._basic_skill_vnum()
            info = self.spells.get(vnum) if vnum else None
            if info is None:
                text = (
                    "basic attack: skill vnum not detected yet — "
                    "it is learned from the first hit (u_s 0)"
                )
            else:
                text = "basic: " + self._combat_stats_text(info)
        if text != self._combat_info_text:
            self._combat_info_text = text
            self.combat_info.set_text(text)

    def _on_map_monster_vnums(self) -> frozenset[int]:
        """Monster vnums currently spawned on the loaded map."""
        vnums: set[int] = set()
        for entity in self.engine.world.entities.values():
            if entity.kind != 3:
                continue
            vnum = self._monster_vnum(entity)
            if vnum > 0:
                vnums.add(vnum)
        return frozenset(vnums)

    def _live_sidebar_tick(self) -> bool:
        """Refresh the combat line and the on-map target list once a second."""
        if self._closing:
            return False
        self._update_combat_info()
        vnums = self._on_map_monster_vnums()
        if vnums != self._target_map_vnums:
            self._target_map_vnums = vnums
            self._refresh_target_list()
            self._update_target_info()
        return True

    def _spell_desc_text(self, spell: int) -> str:
        if spell <= 0:
            vnum = self._basic_skill_vnum()
            info = self.spells.get(vnum) if vnum else None
            if info is None:
                return "basic attack (slot 0, detected from the first hit)"
            return f"basic: {self._combat_stats_text(info)}"
        info = self.spells.get(spell)
        if info is None:
            return "unknown spell (not in Skill.dat)"
        parts = [info.name]
        slot = self._skill_slot(spell)
        if slot is not None:
            parts.append(f"slot {slot}")
        elif self._known_skills():
            parts.append("NOT LEARNED")
        if info.mp_cost:
            parts.append(f"{info.mp_cost} MP")
        if info.cast_time:
            parts.append(f"cast {info.cast_time:g}s")
        if info.cooldown:
            parts.append(f"{info.cooldown:g}s CD")
        parts.append(info.target_mode)
        if info.range:
            parts.append(f"range {info.range}")
        return " · ".join(parts)

    def _update_spell_desc(self, info: dict) -> None:
        text = info["spell"].get_text().strip()
        try:
            spell = int(text) if text else 0
        except ValueError:
            info["desc"].set_text("invalid spell vnum")
            info["desc"].set_tooltip_text(None)
            return
        description = self._spell_desc_text(spell)
        info["desc"].set_text(description)
        info["desc"].set_tooltip_text(description)

    def _renumber_spell_rules(self) -> None:
        for index, info in enumerate(self.spell_rows, 1):
            info["order"].set_text(f"{index}.")

    @staticmethod
    def _prefers_self_target(spell: SpellInfo) -> bool:
        # Only Skill.dat target type 1 is self. A name-based heuristic was
        # wrong: Healing (867) is target type 0/range 10 and the server
        # cancels a self cast, while Group Healing (873) is type 1 and works.
        return spell.target_mode == "self"

    def _set_rule_spell(self, rule_info: dict, spell: SpellInfo) -> None:
        rule_info["spell"].set_text(str(spell.vnum))
        rule_info["target"].set_selected(
            1 if self._prefers_self_target(spell) else 0
        )
        if spell.cooldown > rule_info["cooldown"].get_value():
            rule_info["cooldown"].set_value(min(300.0, spell.cooldown))
        self._update_spell_desc(rule_info)

    def _add_spell_from_info(self, spell: SpellInfo) -> None:
        target = "self" if self._prefers_self_target(spell) else "monster"
        self._add_spell_rule(
            SpellRule(
                enabled=True,
                spell=spell.vnum,
                subject="always",
                comparator="below",
                threshold=50,
                # The Skill.dat cooldown; this is a per-card value, so it
                # follows the equipped SP card's spell record.
                cooldown=spell.cooldown,
                target=target,
            )
        )

    def _open_spell_picker(self, on_pick) -> None:
        """Modal searchable list over the scanned Skill.dat catalog."""
        if self.window is None:
            return
        self._ensure_spells_loaded()
        if self.spell_picker_window is not None:
            self.spell_picker_window.close()
        window = Gtk.Window(title="Choose spell")
        window.set_application(self)
        window.set_transient_for(self.window)
        window.set_modal(True)
        window.set_default_size(640, 620)
        self.spell_picker_window = window

        def clear_picker(*_args):
            self.spell_picker_window = None
            return False

        window.connect("close-request", clear_picker)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        search = Gtk.SearchEntry()
        search.set_placeholder_text("Search spell name or vnum")
        box.append(search)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.set_activate_on_single_click(True)
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(listbox)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        box.append(scroller)

        info = Gtk.Label(
            label=(
                "Scanned from NSgtdData.NOS / Skill.dat. "
                "Active spells only."
            )
        )
        info.set_xalign(0)
        info.set_wrap(True)
        info.add_css_class("dim-label")
        box.append(info)
        window.set_child(box)

        def refresh() -> None:
            while (child := listbox.get_first_child()) is not None:
                listbox.remove(child)
            if not self.spells_loaded:
                label = Gtk.Label(label="Loading spell catalog…")
                label.add_css_class("dim-label")
                label.set_xalign(0)
                row = Gtk.ListBoxRow()
                row.set_child(label)
                row.set_selectable(False)
                row.set_activatable(False)
                listbox.append(row)
                info.set_text("Decoding NSgtdData.NOS / Skill.dat…")
                return
            query = search.get_text().strip().lower()
            known = self._known_skills()
            spells = [
                spell for spell in self.spells.values() if spell.active
            ]
            spells = [
                spell
                for spell in spells
                if spell.name
                and spell.name.lower() not in ("notuse", "monster skill")
                and not spell.name.lower().startswith("notuse")
            ]
            if query:
                spells = [
                    spell
                    for spell in spells
                    if query in spell.name.lower()
                    or str(spell.vnum) == query
                    or query in str(spell.vnum)
                ]
            learned_order = {vnum: i for i, vnum in enumerate(known)}
            spells.sort(
                key=lambda spell: (
                    0 if str(spell.vnum) == query else 1,
                    learned_order.get(spell.vnum, len(learned_order)),
                    spell.name.lower(),
                    spell.vnum,
                )
            )
            for spell in spells[:120]:
                slot = self._skill_slot(spell.vnum)
                if slot is not None:
                    slot_text = f"slot {slot}"
                elif known:
                    slot_text = "not learned"
                else:
                    # `ski` is sent at login; without it every slot is
                    # unknown, and claiming "not learned" would be false.
                    slot_text = "slot unknown"
                label = Gtk.Label(
                    label=(
                        f"{spell.name}   (vnum {spell.vnum}, {slot_text})   · "
                        f"{spell.mp_cost} MP · {spell.target_mode} · "
                        f"{spell.cooldown:g}s CD · cast {spell.cast_time:g}s "
                        f"· range {spell.range}"
                    )
                )
                label.set_xalign(0)
                label.set_ellipsize(Pango.EllipsizeMode.END)
                row = Gtk.ListBoxRow()
                row.set_child(label)
                row.set_tooltip_text(
                    f"spell {spell.vnum} · range {spell.range} · "
                    f"cast {spell.cast_time:g}s · {spell.cooldown:g}s CD · "
                    f"{spell.mp_cost} MP"
                )
                row.connect(
                    "activate",
                    lambda _row, chosen=spell: (
                        on_pick(chosen),
                        window.close(),
                    ),
                )
                listbox.append(row)
            if not spells:
                label = Gtk.Label(label="no matching spell")
                label.add_css_class("dim-label")
                label.set_xalign(0)
                row = Gtk.ListBoxRow()
                row.set_child(label)
                row.set_selectable(False)
                row.set_activatable(False)
                listbox.append(row)
            if known:
                info.set_text(
                    f"{len(spells)} spell(s) matched · {len(known)} learned"
                )
            else:
                info.set_text(
                    f"{len(spells)} spell(s) matched · slots unknown — the "
                    "learned list is sent at login; use a spell once or "
                    "relog with the logger attached"
                )

        search.connect("search-changed", lambda *_: refresh())
        refresh()

        def wait_loaded() -> bool:
            if not window.get_visible():
                return False
            if not self.spells_loaded:
                return True
            refresh()
            return False

        GLib.timeout_add(250, wait_loaded)
        window.present()
        search.grab_focus()

    def _add_spell_rule(self, rule: SpellRule | None = None) -> None:
        if self.spell_rules_box is None:
            return
        rule = rule or SpellRule()
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        container.set_margin_top(4)
        container.set_margin_bottom(4)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        order = Gtk.Label(label="1.")
        order.set_width_chars(2)
        order.set_tooltip_text("execution order (top to bottom)")
        enabled = Gtk.CheckButton()
        enabled.set_active(rule.enabled)
        enabled.set_tooltip_text("enable this rule")
        spell = Gtk.Entry()
        spell.set_width_chars(5)
        spell.set_max_width_chars(6)
        spell.set_text(str(rule.spell))
        spell.set_tooltip_text(
            "spell vnum from Skill.dat; the picker maps it to the u_s slot"
        )
        pick = Gtk.Button(label="…")
        pick.set_tooltip_text("Choose a spell from the scanned Skill.dat catalog")
        desc = Gtk.Label()
        desc.set_xalign(0)
        desc.set_hexpand(True)
        desc.set_max_width_chars(38)
        desc.set_ellipsize(Pango.EllipsizeMode.END)
        desc.add_css_class("dim-label")
        up = Gtk.Button(label="↑")
        up.set_tooltip_text("higher priority")
        down = Gtk.Button(label="↓")
        down.set_tooltip_text("lower priority")
        remove = Gtk.Button(label="×")
        remove.set_tooltip_text("remove rule")
        for widget in (order, enabled, spell, pick, up, down, remove):
            header.append(widget)
        container.append(header)
        container.append(desc)

        condition = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        subject = Gtk.DropDown.new_from_strings(["Always", "My HP %", "My MP %"])
        subject_index = (
            SUBJECTS.index(rule.subject) if rule.subject in SUBJECTS else 0
        )
        subject.set_selected(subject_index)
        comparator = Gtk.DropDown.new_from_strings(["below", "above"])
        comparator_index = (
            COMPARATORS.index(rule.comparator)
            if rule.comparator in COMPARATORS
            else 0
        )
        comparator.set_selected(comparator_index)
        threshold = Gtk.SpinButton.new_with_range(0, 100, 5)
        threshold.set_value(rule.threshold)
        threshold.set_tooltip_text("percentage threshold")
        percent_label = Gtk.Label(label="%")

        def sync_condition(*_args) -> None:
            active = subject.get_selected() != 0  # 0 = Always
            comparator.set_sensitive(active)
            threshold.set_sensitive(active)
            percent_label.set_sensitive(active)

        subject.connect("notify::selected", sync_condition)
        condition.append(subject)
        condition.append(comparator)
        condition.append(threshold)
        condition.append(percent_label)
        container.append(condition)
        sync_condition()

        timing = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        cooldown = Gtk.SpinButton.new_with_range(0, 300, 0.5)
        cooldown.set_digits(1)
        cooldown.set_value(rule.cooldown)
        cooldown.set_tooltip_text(
            "seconds before this rule may fire again; the spell's own "
            "Skill.dat cooldown is enforced even when this is 0"
        )
        target = Gtk.DropDown.new_from_strings(["Monster", "Self"])
        target_index = (
            SPELL_TARGETS.index(rule.target)
            if rule.target in SPELL_TARGETS
            else 0
        )
        target.set_selected(target_index)
        timing.append(Gtk.Label(label="Cooldown"))
        timing.append(cooldown)
        timing.append(Gtk.Label(label="s"))
        timing.append(Gtk.Label(label="Target"))
        timing.append(target)
        container.append(timing)

        info = {
            "box": container,
            "order": order,
            "enabled": enabled,
            "spell": spell,
            "desc": desc,
            "subject": subject,
            "comparator": comparator,
            "threshold": threshold,
            "cooldown": cooldown,
            "target": target,
        }
        self.spell_rows.append(info)
        self.spell_rules_box.append(container)

        spell.connect("changed", lambda *_: self._update_spell_desc(info))
        spell.connect("changed", lambda *_: self._reset_spell_timers())
        enabled.connect("toggled", lambda *_: self._reset_spell_timers())
        up.connect("clicked", lambda *_: self._move_spell_rule(info, -1))
        down.connect("clicked", lambda *_: self._move_spell_rule(info, 1))
        remove.connect("clicked", lambda *_: self._remove_spell_rule(info))
        pick.connect(
            "clicked",
            lambda *_: self._open_spell_picker(
                lambda spell_info: self._set_rule_spell(info, spell_info)
            ),
        )
        self._update_spell_desc(info)
        self._renumber_spell_rules()

    def _remove_spell_rule(self, info: dict) -> None:
        if info not in self.spell_rows:
            return
        self.spell_rows.remove(info)
        parent = info["box"].get_parent()
        if parent is not None:
            parent.remove(info["box"])
        self._reset_spell_timers()
        self._renumber_spell_rules()

    def _move_spell_rule(self, info: dict, delta: int) -> None:
        try:
            index = self.spell_rows.index(info)
        except ValueError:
            return
        new_index = index + delta
        if not (0 <= new_index < len(self.spell_rows)):
            return
        self.spell_rows[index], self.spell_rows[new_index] = (
            self.spell_rows[new_index],
            self.spell_rows[index],
        )
        sibling = (
            self.spell_rows[new_index - 1]["box"]
            if new_index > 0
            else None
        )
        self.spell_rules_box.reorder_child_after(info["box"], sibling)
        self._reset_spell_timers()
        self._renumber_spell_rules()

    def _clear_spell_rules(self) -> None:
        for info in list(self.spell_rows):
            self._remove_spell_rule(info)
        self._reset_spell_timers()
        self._renumber_spell_rules()

    def _reset_spell_timers(self) -> None:
        self.spell_last_cast.clear()

    def _collect_spell_rules(self) -> list[SpellRule]:
        rules: list[SpellRule] = []
        for info in self.spell_rows:
            text = info["spell"].get_text().strip()
            try:
                spell = int(text) if text else 0
            except ValueError:
                spell = 0
            subject_index = info["subject"].get_selected()
            comparator_index = info["comparator"].get_selected()
            target_index = info["target"].get_selected()
            rules.append(
                SpellRule(
                    enabled=info["enabled"].get_active(),
                    spell=max(0, spell),
                    subject=(
                        SUBJECTS[subject_index]
                        if 0 <= subject_index < len(SUBJECTS)
                        else "my_hp"
                    ),
                    comparator=(
                        COMPARATORS[comparator_index]
                        if 0 <= comparator_index < len(COMPARATORS)
                        else "below"
                    ),
                    threshold=info["threshold"].get_value(),
                    cooldown=info["cooldown"].get_value(),
                    target=(
                        SPELL_TARGETS[target_index]
                        if 0 <= target_index < len(SPELL_TARGETS)
                        else "monster"
                    ),
                )
            )
        return rules

    def _spell_condition_met(self, rule: SpellRule) -> bool:
        if rule.subject == "always":
            return True
        stats = self.engine.world.stats
        if rule.subject == "my_mp":
            if stats.mp_max <= 0:
                return False
            percent = 100.0 * stats.mp / stats.mp_max
        else:
            if stats.hp_max <= 0:
                return False
            percent = 100.0 * stats.hp / stats.hp_max
        if rule.comparator == "above":
            return percent > rule.threshold
        return percent < rule.threshold

    def _known_skills(self) -> list[int]:
        """The authoritative learned-skill list, when the host saw ``ski``."""
        return self.engine.world.skill_list or self.log_skill_list

    def _learned_slots(self) -> dict[int, int]:
        """Slot -> vnum seen in `u_s`/`su` pairs (log history + live)."""
        slots = dict(self.log_skill_slots)
        slots.update(self.engine.world.skill_learned)
        return slots

    def _skill_slot(self, vnum: int) -> int | None:
        if vnum <= 0:
            return None
        slot = self.engine.world.skill_slot(vnum)
        if slot is None and self.log_skill_list:
            try:
                slot = self.log_skill_list.index(vnum)
            except ValueError:
                slot = None
        if slot is None:
            for learned_slot, learned_vnum in self._learned_slots().items():
                if learned_vnum == vnum:
                    slot = learned_slot
                    break
        return slot

    def _basic_skill_vnum(self) -> int | None:
        """The equipped SP card's basic attack vnum (skill slot 0).

        ``su`` answers `u_s 0` with it, so it is live and follows a card
        swap. Until the first hit, the log's paired packets and the ``ski``
        list provide it; the old hardcoded 866 was a swordsman skill and gave
        a mage the wrong range.
        """
        world = self.engine.world
        if world.basic_skill is not None:
            return world.basic_skill
        if 0 in self.log_skill_slots:
            return self.log_skill_slots[0]
        if world.skill_list:
            return world.skill_list[0]
        if self.log_skill_list:
            return self.log_skill_list[0]
        return None

    def _skill_send_id(self, vnum: int) -> int | None:
        """Map a Skill.dat vnum to the zero-based `u_s` slot.

        ``None`` means the slot is unknown (neither `ski` nor a paired
        `u_s`/`su` seen it). Sending the vnum there would be an invalid slot,
        so the caller refuses and tells the user instead.
        """
        if vnum <= 0:
            return 0
        return self._skill_slot(vnum)

    def _spell_target(self, rule: SpellRule) -> str:
        """Resolve the target from Skill.dat, with the rule as fallback.

        Skill.dat target type 1 is self; 2 is an ally/party target, which the
        bot cannot address yet, so it falls back to self. Type 0 (any/entity)
        uses the rule's Monster/Self choice.
        """
        info = self.spells.get(rule.spell)
        if info is not None and info.target_mode in ("self", "ally"):
            return "self"
        return rule.target

    def _spell_in_range(
        self, info: SpellInfo | None, monster: Entity | None
    ) -> bool:
        if info is not None and info.target_mode in ("self", "ally"):
            return True
        if info is None or info.range <= 0 or monster is None:
            return True
        position = self._player_cell()
        if position is None:
            return True
        distance = max(
            abs(monster.x - position[0]), abs(monster.y - position[1])
        )
        return distance <= info.range

    def _select_spell_rule(
        self,
        now: float,
        monster: Entity | None,
        char_id: int | None,
    ) -> tuple[int, SpellRule] | None:
        stats = self.engine.world.stats
        known = self._known_skills()
        for index, rule in enumerate(self._collect_spell_rules()):
            if not rule.enabled or rule.spell <= 0:
                continue
            if known and rule.spell not in known:
                continue
            if self._skill_slot(rule.spell) is None:
                # `u_s` carries a slot, not the vnum. `ski` is only sent at
                # login; until a `u_s`/`su` pair reveals this spell's slot,
                # sending it would be invalid. Skip the rule so basic attacks
                # still run, and say how to fix it.
                if now >= self.slot_warn_at:
                    self._set_status(
                        f"spell {rule.spell}: u_s slot unknown — use it once "
                        "in game or relog with the logger attached"
                    )
                    self.slot_warn_at = now + 15.0
                continue
            info = self.spells.get(rule.spell)
            cooldown = max(
                rule.cooldown, info.cooldown if info is not None else 0.0
            )
            if now < self.spell_last_cast.get(index, 0.0) + cooldown:
                continue
            target = self._spell_target(rule)
            if target == "self" and char_id is None:
                continue
            if target == "monster":
                if monster is None:
                    continue
                if not self._spell_in_range(info, monster):
                    continue
            if info is not None and info.mp_cost > 0 and stats.mp < info.mp_cost:
                continue
            if self._spell_condition_met(rule):
                return index, rule
        return None

    def _cast_spell(
        self,
        index: int,
        rule: SpellRule,
        monster: Entity | None,
        char_id: int | None,
        now: float,
    ) -> bool:
        info = self.spells.get(rule.spell)
        if info is not None and info.mp_cost > 0:
            stats = self.engine.world.stats
            if stats.mp < info.mp_cost:
                self._set_status(
                    f"spell {rule.spell}: not enough MP "
                    f"({stats.mp}/{info.mp_cost})"
                )
                return False
        if self._spell_target(rule) == "self":
            if char_id is None:
                return False
            target_type, target_id = 1, char_id
        else:
            if monster is None:
                return False
            target_type, target_id = 3, monster.entity_id
            self._send(f"ncif 3 {monster.entity_id}")
        send_skill = self._skill_send_id(rule.spell)
        if send_skill is None:
            return False
        packet = self._skill_packet(
            target_id, skill=send_skill, target_type=target_type
        )
        self._send(packet)
        # The per-rule cooldown (max of the rule and Skill.dat) is enforced in
        # _select_spell_rule; here only the cast animation plus the Min delay
        # floor pace the next action.
        cooldown = float(self.attack_cooldown.get_value())
        if info is not None:
            cooldown = max(cooldown, info.cast_time)
        self.attack_cooldown_until = now + cooldown
        cast_pause = info.cast_time if info is not None else 0.0
        self.walk_not_before = now + max(
            self.attack_walk_delay, cast_pause
        )
        self.spell_last_cast[index] = now
        name = info.name if info is not None else str(rule.spell)
        slot_text = f" slot {send_skill}"
        self._set_status(
            f"spell {name} (vnum {rule.spell}{slot_text}): {packet}"
        )
        return True

    # -- configuration --------------------------------------------------

    def _collect_config(self) -> BotConfig:
        return BotConfig(
            auto_attack=bool(
                self.auto_attack_toggle is not None
                and self.auto_attack_toggle.get_active()
            ),
            retaliate=bool(
                self.retaliate_toggle is not None
                and self.retaliate_toggle.get_active()
            ),
            move_first=bool(
                self.move_first_toggle is not None
                and self.move_first_toggle.get_active()
            ),
            skill=self._entry_int(self.skill_entry, 0),
            skill_template=(
                self.skill_packet_entry.get_text().strip()
                or "u_s {skill} {type} {target}"
            ),
            attack_range=int(self.attack_range.get_value()),
            attack_cooldown=float(self.attack_cooldown.get_value()),
            pickup=bool(
                self.pickup_toggle is not None
                and self.pickup_toggle.get_active()
            ),
            loot_range=int(self.loot_range.get_value()),
            recover=bool(
                self.recover_toggle is not None
                and self.recover_toggle.get_active()
            ),
            low_hp_pct=float(self.low_hp_pct.get_value()),
            loop=bool(
                self.loop_toggle is not None
                and self.loop_toggle.get_active()
            ),
            monster_mode=self._current_monster_mode(),
            monster_vnums=sorted(v for v in self.target_vnums if v > 0),
            attack_unknown=bool(
                self.attack_unknown_toggle is not None
                and self.attack_unknown_toggle.get_active()
            ),
            spell_rules=self._collect_spell_rules(),
        )

    @staticmethod
    def _entry_int(entry, default: int) -> int:
        if entry is None:
            return default
        try:
            return int(entry.get_text().strip() or default)
        except ValueError:
            return default

    def _apply_config(self, config: BotConfig) -> None:
        if self.bot_running:
            self._stop_bot()
        self.auto_attack_toggle.set_active(config.auto_attack)
        self.retaliate_toggle.set_active(config.retaliate)
        self.move_first_toggle.set_active(config.move_first)
        self.skill_entry.set_text(str(config.skill))
        self.skill_packet_entry.set_text(config.skill_template)
        self.attack_range.set_value(config.attack_range)
        self.attack_cooldown.set_value(config.attack_cooldown)
        self._update_combat_info()
        self.pickup_toggle.set_active(config.pickup)
        self.loot_range.set_value(config.loot_range)
        self.recover_toggle.set_active(config.recover)
        self.low_hp_pct.set_value(config.low_hp_pct)
        self.loop_toggle.set_active(config.loop)

        mode_index = (
            MONSTER_MODES.index(config.monster_mode)
            if config.monster_mode in MONSTER_MODES
            else 0
        )
        self.target_mode.set_selected(mode_index)
        self.target_vnums = {v for v in config.monster_vnums if v > 0}
        if self.attack_unknown_toggle is not None:
            self.attack_unknown_toggle.set_active(config.attack_unknown)
        if self.target_search is not None:
            self.target_search.set_text("")
        self._refresh_target_list()
        self._update_target_info()

        self._clear_spell_rules()
        for rule in config.spell_rules:
            self._add_spell_rule(rule)
        self._renumber_spell_rules()
        self._reset_spell_timers()
        self.attack_cooldown_until = 0.0
        self.walk_not_before = 0.0
        self.pending_target = None

    def _save_config_to(self, path: str) -> bool:
        try:
            save_config(path, self._collect_config())
        except ConfigError as exc:
            self._set_status(str(exc))
            if self.config_info is not None:
                self.config_info.set_text(str(exc))
            return False
        name = os.path.basename(path)
        self._set_status(f"config saved: {path}")
        if self.config_info is not None:
            self.config_info.set_text(f"saved {name}")
        return True

    def _load_config_from(self, path: str) -> bool:
        try:
            config = load_config(path)
        except ConfigError as exc:
            self._set_status(str(exc))
            if self.config_info is not None:
                self.config_info.set_text(str(exc))
            return False
        self._apply_config(config)
        name = os.path.basename(path)
        self._set_status(f"config loaded: {path}")
        if self.config_info is not None:
            self.config_info.set_text(f"loaded {name}")
        return True

    def _on_save_config_clicked(self, _button) -> None:
        if self.window is None:
            return
        dialog = Gtk.FileDialog(title="Save bot configuration")
        dialog.set_initial_name("ntpl-bot.json")
        dialog.save(self.window, None, self._on_save_config_result)

    def _on_save_config_result(self, dialog, result) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return
        path = file.get_path()
        if path:
            self._save_config_to(path)

    def _on_load_config_clicked(self, _button) -> None:
        if self.window is None:
            return
        dialog = Gtk.FileDialog(title="Load bot configuration")
        dialog.open(self.window, None, self._on_load_config_result)

    def _on_load_config_result(self, dialog, result) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return
        path = file.get_path()
        if path:
            self._load_config_from(path)

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
        # A new map invalidates the old coordinates: do not keep walking a
        # path from the previous map.
        if self.bot_running:
            self._stop_bot()
        self.bot_goals = []
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
        self._schedule_map_redraw()
        self._schedule_stats_redraw()

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
        if self.map_area is not None:
            self.map_area.queue_draw()

    def _map_geometry(self, width: int, height: int) -> tuple[float, float, float]:
        """Return scale and top-left offset that show the **whole** map.

        The map keeps its aspect ratio and is scaled to the largest size that
        fits both dimensions (contain fit). Nothing is cropped, so this is the
        maximum view of the map for the current window. The surrounding canvas
        is filled with a dimmed copy of the map, so there is no black.
        """
        if self.map_grid is None or width <= 1 or height <= 1:
            return 1.0, 0.0, 0.0
        scale = min(width / self.map_grid.width, height / self.map_grid.height)
        scale = max(0.15, scale)
        offset_x = (width - self.map_grid.width * scale) / 2
        offset_y = (height - self.map_grid.height * scale) / 2
        return scale, offset_x, offset_y

    # -- redraw scheduling ---------------------------------------------

    def _schedule_map_redraw(self) -> None:
        if self.map_area is None or self.map_redraw_scheduled:
            return
        self.map_redraw_scheduled = True
        GLib.timeout_add(80, self._redraw_map_once)

    def _redraw_map_once(self) -> bool:
        self.map_redraw_scheduled = False
        if self.map_area is not None:
            self.map_area.queue_draw()
        self._update_char_info()
        return False

    def _schedule_stats_redraw(self) -> None:
        if self.stats_area is None or self.stats_redraw_scheduled:
            return
        self.stats_redraw_scheduled = True
        GLib.timeout_add(100, self._redraw_stats_once)

    def _redraw_stats_once(self) -> bool:
        self.stats_redraw_scheduled = False
        if self.stats_area is not None:
            self.stats_area.queue_draw()
        self._update_char_info()
        return False

    def _update_char_info(self) -> None:
        if self.char_info is None:
            return
        world = self.engine.world
        parts = []
        if world.map_id is not None:
            parts.append(f"map {world.map_id}")
        elif self.map_grid is not None:
            parts.append(f"map {self.map_grid.map_id}")
        if world.position is not None:
            parts.append(f"pos {world.position[0]},{world.position[1]}")
        else:
            parts.append("position unknown")
        char_id = self._char_id()
        if char_id is not None:
            parts.append(f"char {char_id}")
        if self.recovering:
            parts.append("RECOVERING")
        self.char_info.set_text(" · ".join(parts))

        if self.recover_banner is not None:
            if self.recovering:
                stats = world.stats
                threshold = (
                    self.low_hp_pct.get_value()
                    if self.low_hp_pct is not None
                    else 0
                )
                if stats.hp_max > 0:
                    percent = 100.0 * stats.hp / stats.hp_max
                    text = (
                        f"RECOVERING — HP {percent:.0f}% is below "
                        f"{threshold:.0f}%. Sitting until full; "
                        "attacks and movement are paused."
                    )
                else:
                    text = (
                        "RECOVERING — sitting until HP is full; "
                        "attacks and movement are paused."
                    )
                self.recover_banner.set_text(text)
                self.recover_banner.set_visible(True)
            else:
                self.recover_banner.set_visible(False)

    def _draw_stats(self, _area, context, width: int, height: int) -> None:
        context.set_source_rgb(0.08, 0.08, 0.08)
        context.paint()
        stats = self.engine.world.stats
        rows = (
            (
                "HP",
                stats.hp,
                stats.hp_max,
                (0.18, 0.72, 0.26),
                (0.42, 0.08, 0.08),
            ),
            (
                "MP",
                stats.mp,
                stats.mp_max,
                (0.22, 0.52, 0.95),
                (0.08, 0.16, 0.40),
            ),
        )
        context.select_font_face(
            "Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD
        )
        context.set_font_size(11)
        y = 6.0
        for label, value, maximum, fill, missing in rows:
            percent = (100.0 * value / maximum) if maximum > 0 else 0.0
            if maximum > 0:
                text = (
                    f"{label}  {value} / {maximum}  ({percent:.0f}%)"
                    f"   missing {max(0, maximum - value)}"
                )
            else:
                text = f"{label}  —"
            context.set_source_rgb(0.85, 0.85, 0.85)
            context.move_to(8, y + 12)
            context.show_text(text)
            bar_y = y + 18
            bar_w = max(10.0, width - 16)
            bar_h = 16.0
            context.set_source_rgb(*missing)
            context.rectangle(8, bar_y, bar_w, bar_h)
            context.fill()
            if maximum > 0:
                context.set_source_rgb(*fill)
                context.rectangle(8, bar_y, bar_w * min(1.0, value / maximum), bar_h)
                context.fill()
            context.set_source_rgb(0.0, 0.0, 0.0)
            context.rectangle(8, bar_y, bar_w, bar_h)
            context.set_line_width(1.0)
            context.stroke()
            y += 44.0

    def _retaliation_target(self, radius: int) -> Entity | None:
        world = self.engine.world
        if world.last_attacker_id is None:
            return None
        # Only recent hits count; a stale attacker should not lock targeting.
        if time.time() - world.last_attacker_at > 5.0:
            return None
        entity = world.entities.get(world.last_attacker_id)
        if entity is None or entity.kind != 3:
            return None
        if not self._monster_allowed(entity):
            return None
        position = self._player_cell()
        if position is None:
            return None
        distance = max(abs(entity.x - position[0]), abs(entity.y - position[1]))
        return entity if distance <= radius else None

    def _live_points(self) -> list[tuple[int, int]]:
        world = self.engine.world
        # Every tracked entity is on the current map, so monsters and NPCs are
        # as valid as players. Using them all matters: with only the own
        # character and a couple of players, several large or same-layout maps
        # tie at 100 % walkable and the ranking is arbitrary. The own position
        # comes first so a solo character still contributes a point.
        points: list[tuple[int, int]] = []
        if world.position is not None:
            points.append(world.position)
        points.extend(
            (entity.x, entity.y) for entity in world.entities.values()
        )
        # Stable order, bounded work for the ranking.
        return list(dict.fromkeys(points))[:AUTO_MAP_POINTS]

    def _auto_map_tick(self) -> bool:
        """Pick the map from ``c_map``/``at`` or from live entity positions.

        There is no Map ID / Load / Auto-detect control any more: the map
        follows the character. A map id from the server is authoritative; when
        the host attached after login and never saw one, rank the grids by the
        walkable positions of every live entity and pick the tightest fit
        (``choose_map_for_positions``).
        """
        if self._closing:
            return False
        if not self.map_grids:
            self._ensure_map_loaded()
            return True
        world = self.engine.world
        if world.map_id is not None:
            if world.map_id in self.map_grids:
                if (
                    self.map_grid is None
                    or self.map_grid.map_id != world.map_id
                ):
                    self._select_map(world.map_id)
                    self._set_status(f"auto-selected map {world.map_id}")
                return True
            # The server named a map this archive does not hold (an instance
            # map such as 20651). Guessing a different grid from positions
            # would show a wrong map under the right name; say so instead.
            if self.map_grid is None or self.map_grid.map_id != world.map_id:
                self._set_status(
                    f"map {world.map_id} has no grid in {self.map_file}"
                )
            return True
        if self.map_grid is None:
            fallback = (
                DEFAULT_MAP_ID
                if DEFAULT_MAP_ID in self.map_grids
                else next(iter(self.map_grids))
            )
            self._select_map(fallback)
            return True

        points = self._live_points()
        if len(points) < 2:
            return True
        choice = choose_map_for_positions(
            self.map_grids, points, current_id=self.map_grid.map_id
        )
        if choice is None:
            return True
        fraction, map_id = choice
        if map_id != self.map_grid.map_id:
            self._select_map(map_id)
            self._set_status(
                f"auto-detected map {map_id} "
                f"({fraction * 100:.1f}% live positions)"
            )
        return True

    # -- map drawing ----------------------------------------------------

    def _draw_map(self, _area, context, width: int, height: int) -> None:
        # Fill the whole widget. The old ScrolledWindow left the unused part
        # of the viewport black when the window was widened. The surrounding
        # canvas is a dimmed, zoomed-out copy of the same map, so it reads as
        # part of the map rather than a black hole.
        context.set_source_rgb(0.13, 0.16, 0.20)
        context.paint()

        if self.map_surface is None or self.map_grid is None:
            return

        grid_for_bg = self.map_grid
        bg_scale = max(
            width / grid_for_bg.width,
            height / grid_for_bg.height,
        )
        bg_x = (width - grid_for_bg.width * bg_scale) / 2
        bg_y = (height - grid_for_bg.height * bg_scale) / 2
        context.save()
        context.translate(bg_x, bg_y)
        context.scale(bg_scale, bg_scale)
        context.set_source_surface(self.map_surface, 0, 0)
        context.paint_with_alpha(0.26)
        context.restore()
        context.set_source_rgba(0.06, 0.08, 0.11, 0.55)
        context.paint()
        context.set_source_rgba(1.0, 1.0, 1.0, 0.045)
        context.set_line_width(1.0)
        for gx in range(0, width, 64):
            context.move_to(gx + 0.5, 0)
            context.line_to(gx + 0.5, height)
        for gy in range(0, height, 64):
            context.move_to(0, gy + 0.5)
            context.line_to(width, gy + 0.5)
        context.stroke()

        scale, ox, oy = self._map_geometry(width, height)
        grid = self.map_grid
        map_w = grid.width * scale
        map_h = grid.height * scale

        # Shadow + frame around the map.
        context.set_source_rgba(0.0, 0.0, 0.0, 0.55)
        context.rectangle(ox + 3, oy + 3, map_w, map_h)
        context.fill()
        context.set_source_rgb(0.25, 0.30, 0.36)
        context.set_line_width(2.0)
        context.rectangle(ox - 1, oy - 1, map_w + 2, map_h + 2)
        context.stroke()

        context.save()
        context.translate(ox, oy)
        context.scale(scale, scale)
        context.set_source_surface(self.map_surface, 0, 0)
        context.paint()
        context.restore()

        # All overlays below use map pixels; translate once.
        context.save()
        context.translate(ox, oy)

        # A* path: one connected polyline through cell centres, so diagonal
        # 8-direction steps read as a smooth line instead of a staircase of
        # separate squares.
        if self.path_cells:
            points = list(self.path_cells)
            own = self._player_cell()
            if own is not None:
                points.insert(0, own)
            context.set_source_rgba(0.0, 0.9, 0.9, 0.9)
            context.set_line_cap(cairo.LINE_CAP_ROUND)
            context.set_line_join(cairo.LINE_JOIN_ROUND)
            context.set_line_width(max(1.5, scale * 0.3))
            if len(points) >= 2:
                context.move_to(
                    points[0][0] * scale + scale / 2,
                    points[0][1] * scale + scale / 2,
                )
                for x, y in points[1:]:
                    context.line_to(
                        x * scale + scale / 2,
                        y * scale + scale / 2,
                    )
                context.stroke()
            else:
                x, y = points[0]
                context.arc(
                    x * scale + scale / 2,
                    y * scale + scale / 2,
                    max(2.0, scale * 0.5),
                    0,
                    2 * math.pi,
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

        # ground items (drops) as small gold diamonds
        for drop in self.engine.world.drops.values():
            if not (0 <= drop.x < grid.width and 0 <= drop.y < grid.height):
                continue
            cx = drop.x * scale + scale / 2
            cy = drop.y * scale + scale / 2
            size = max(2.5, scale * 0.55)
            context.move_to(cx, cy - size)
            context.line_to(cx + size, cy)
            context.line_to(cx, cy + size)
            context.line_to(cx - size, cy)
            context.close_path()
            context.set_source_rgba(1.0, 0.83, 0.1, 1.0)
            context.fill_preserve()
            context.set_source_rgba(0.15, 0.1, 0.0, 1.0)
            context.set_line_width(1.0)
            context.stroke()

        # own character: a bigger dot plus a facing arrow. The arrow is drawn
        # first so the dot covers its base.
        own = self._player_cell()
        if own is not None:
            x, y = own
            cx = x * scale + scale / 2
            cy = y * scale + scale / 2

            facing = self.engine.world.facing
            if facing is not None and facing != (0, 0):
                fx, fy = facing
                length = math.hypot(fx, fy)
                ux, uy = fx / length, fy / length
                arrow_len = max(12.0, scale * 2.8)
                half_width = max(4.0, scale * 0.7)
                tip = (cx + ux * arrow_len, cy + uy * arrow_len)
                base = (cx + ux * arrow_len * 0.35, cy + uy * arrow_len * 0.35)
                left = (base[0] - uy * half_width, base[1] + ux * half_width)
                right = (base[0] + uy * half_width, base[1] - ux * half_width)

                context.move_to(*tip)
                context.line_to(*left)
                context.line_to(*right)
                context.close_path()
                context.set_source_rgba(1.0, 0.1, 0.1, 1.0)
                context.fill_preserve()
                context.set_source_rgba(1.0, 1.0, 1.0, 1.0)
                context.set_line_width(max(1.5, scale * 0.28))
                context.stroke()

            radius = max(5.0, scale * 1.05)
            context.set_source_rgba(1.0, 0.1, 0.1, 1.0)
            context.arc(cx, cy, radius, 0, 2 * math.pi)
            context.fill()
            context.set_source_rgba(1.0, 1.0, 1.0, 1.0)
            context.set_line_width(max(1.5, scale * 0.25))
            context.arc(cx, cy, radius + max(1.5, scale * 0.3), 0, 2 * math.pi)
            context.stroke()

        context.restore()

    def _on_map_click(self, gesture, _n_press, x, y) -> None:
        if self.map_grid is None or self.map_area is None:
            return
        scale, ox, oy = self._map_geometry(
            self.map_area.get_width(), self.map_area.get_height()
        )
        cell = (int((x - ox) // scale), int((y - oy) // scale))
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

    # -- map tooltips ---------------------------------------------------

    def _map_cell_screen(self, x: int, y: int) -> tuple[float, float]:
        scale, ox, oy = self._map_geometry(
            self.map_area.get_width(), self.map_area.get_height()
        )
        return ox + (x + 0.5) * scale, oy + (y + 0.5) * scale

    def _own_tooltip_text(self, own: tuple[int, int]) -> str:
        world = self.engine.world
        lines = ["You"]
        char_id = self._char_id()
        if char_id is not None:
            lines.append(f"char id {char_id}")
        lines.append(f"position {own[0]}, {own[1]}")
        if world.map_id is not None:
            lines.append(f"map {world.map_id}")
        return "\n".join(lines)

    def _entity_tooltip_text(self, entity: Entity) -> str:
        labels = {1: "Player", 2: "NPC/pet", 3: "Monster"}
        label = labels.get(entity.kind, "Entity")
        # The live `in` packet is authoritative. If the host attached after
        # this entity spawned, recover the vnum/name from the session log.
        vnum = entity.vnum or self.log_vnums.get(entity.entity_id, 0)
        name = entity.name or self.log_names.get(entity.entity_id, "")
        if not name and entity.kind == 3:
            name = self.monster_names.get(vnum, "")
        if name:
            title = f"{label} {name}"
        else:
            title = f"{label} #{entity.entity_id}"
        lines = [title]
        if entity.kind == 1:
            lines.append(f"char id {entity.entity_id}")
        else:
            lines.append(f"entity id {entity.entity_id}")
            if vnum:
                lines.append(f"vnum {vnum}")
        lines.append(f"position {entity.x}, {entity.y}")
        own = self._player_cell()
        if own is not None:
            distance = max(abs(entity.x - own[0]), abs(entity.y - own[1]))
            lines.append(f"distance {distance}")
        if self.engine.world.last_attacker_id == entity.entity_id:
            lines.append("attacking you")
        return "\n".join(lines)

    def _drop_tooltip_text(self, drop: GroundItem) -> str:
        title = f"Drop item {drop.item_vnum}"
        if drop.amount > 1:
            title += f" x{drop.amount}"
        lines = [title, f"drop id {drop.drop_id}"]
        own = self._player_cell()
        lines.append(f"position {drop.x}, {drop.y}")
        if own is not None:
            distance = max(abs(drop.x - own[0]), abs(drop.y - own[1]))
            lines.append(f"distance {distance}")
        if drop.owner_id:
            owner = "you" if drop.owner_id == self._char_id() else str(drop.owner_id)
            lines.append(f"owner {owner}")
        else:
            lines.append("unowned")
        return "\n".join(lines)

    def _map_tooltip_text(self, x: float, y: float) -> str | None:
        """Describe the own marker, entity or drop under a widget point."""
        if self.map_grid is None or self.map_area is None:
            return None
        width = self.map_area.get_width()
        height = self.map_area.get_height()
        if width <= 1 or height <= 1:
            return None
        scale, _ox, _oy = self._map_geometry(width, height)
        radius = max(9.0, scale * 1.2)
        best: tuple[float, str] | None = None

        own = self._player_cell()
        if own is not None:
            cx, cy = self._map_cell_screen(*own)
            distance = math.hypot(x - cx, y - cy)
            if distance <= radius:
                best = (distance, self._own_tooltip_text(own))

        world = self.engine.world
        for entity in world.entities.values():
            if not (
                0 <= entity.x < self.map_grid.width
                and 0 <= entity.y < self.map_grid.height
            ):
                continue
            # The own marker above is already covered; do not add its raw
            # entity a second time.
            if entity.entity_id == world.self_id:
                continue
            cx, cy = self._map_cell_screen(entity.x, entity.y)
            distance = math.hypot(x - cx, y - cy)
            if distance <= radius and (best is None or distance < best[0]):
                best = (distance, self._entity_tooltip_text(entity))

        for drop in world.drops.values():
            if not (
                0 <= drop.x < self.map_grid.width
                and 0 <= drop.y < self.map_grid.height
            ):
                continue
            cx, cy = self._map_cell_screen(drop.x, drop.y)
            distance = math.hypot(x - cx, y - cy)
            if distance <= radius and (best is None or distance < best[0]):
                best = (distance, self._drop_tooltip_text(drop))

        return best[1] if best is not None else None

    def _on_map_query_tooltip(self, _widget, x, y, _keyboard_mode, tooltip):
        text = self._map_tooltip_text(x, y)
        if not text:
            return False
        tooltip.set_text(text)
        return True

    def _clear_path(self) -> None:
        self.waypoints.clear()
        self.path_cells.clear()
        self.path_index = 0
        self.pending_target = None
        if self.map_area is not None:
            self.map_area.queue_draw()
        self._set_status("path cleared")
        self._set_path_info("no path")

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
        if current is None:
            self.path_cells = []
            if self.map_area is not None:
                self.map_area.queue_draw()
            self._set_status("own position unknown; wait for at/tp or move once")
            self._set_path_info("position unknown")
            return
        if not self.map_grid.is_walkable(*current):
            self.path_cells = []
            if self.map_area is not None:
                self.map_area.queue_draw()
            self._set_status(f"own position {current} is not walkable")
            return

        path: list[tuple[int, int]] = []
        for waypoint in self.waypoints:
            leg = astar(self.map_grid, current, waypoint)
            if leg is None:
                self._set_status(f"no path to {waypoint}")
                self._set_path_info(f"no path to {waypoint}")
                continue
            path.extend(leg)
            current = waypoint
        self.path_cells = path
        if self.map_area is not None:
            self.map_area.queue_draw()
        self._set_status(f"path: {len(path)} cells, {len(self.waypoints)} waypoint(s)")
        self._set_path_info(
            f"path: {len(path)} cells · {len(self.waypoints)} waypoint(s)"
        )

    def _on_page_switch(self, _notebook, _page, _page_num) -> None:
        self._ensure_map_loaded()
        if self.map_area is not None:
            self.map_area.queue_draw()

    # -- connection -----------------------------------------------------

    def _start_client(self) -> None:
        self.client = PacketClient(self.host, self.port)

        def worker() -> None:
            while not self._closing:
                try:
                    self.client.connect(retries=10, delay=0.2)
                except ConnectionLost as exc:
                    GLib.idle_add(self._set_status, f"not connected: {exc}")
                    time.sleep(RECONNECT_DELAY)
                    continue

                GLib.idle_add(self._on_connected)

                try:
                    while not self._closing:
                        message = self.client.recv(timeout=0.5)
                        if message is not None:
                            self.inbox.put(message)
                except (ConnectionLost, ProtocolError, OSError) as exc:
                    GLib.idle_add(self._set_status, f"disconnected: {exc}")

                if not self._closing:
                    # The DLL accepts one host at a time and replaces the old
                    # one, so another CLI/monitor can kick the GUI off. Reconnect
                    # instead of leaving the window dead.
                    time.sleep(RECONNECT_DELAY)

        threading.Thread(target=worker, name="ntpl-gui-reader", daemon=True).start()
        GLib.timeout_add(DRAIN_INTERVAL_MS, self._drain)

    def _drain(self) -> bool:
        dirty = False
        for _ in range(1000):
            try:
                message = self.inbox.get_nowait()
            except queue.Empty:
                break

            if message.kind is Kind.STATUS:
                self.status_received = True
                if message.payload.startswith("POS "):
                    parts = message.payload.split()
                    if len(parts) >= 4:
                        try:
                            x, y, char_id = (
                                int(parts[1]),
                                int(parts[2]),
                                int(parts[3]),
                            )
                        except ValueError:
                            self._set_status(message.payload)
                        else:
                            self.engine.world.x = x
                            self.engine.world.y = y
                            self.engine.world.self_id = char_id
                            self._learn_char_id(char_id)
                            self._set_status(f"position {x},{y} (char {char_id})")
                            dirty = True
                else:
                    self._set_status(message.payload)
            elif message.kind is Kind.QUIT:
                self._set_status("disconnected")
            else:
                packet = Packet.from_message(message)
                self.engine.feed(packet)
                self._track_packet(packet)
                self._learn_char_id(self.engine.world.self_id)
                # Switch the map as soon as the server announces it instead of
                # waiting for the next 2 s auto-map tick.
                if packet.opcode == "ski":
                    for info in self.spell_rows:
                        self._update_spell_desc(info)
                if packet.opcode in ("c_map", "mapout", "at"):
                    map_id = self.engine.world.map_id
                    if (
                        map_id is not None
                        and self.map_grid is not None
                        and map_id != self.map_grid.map_id
                    ):
                        self._auto_map_tick()
                self._append(packet)
                dirty = True

        if (
            self.start_when_located
            and not self.bot_running
            and self._player_cell() is not None
        ):
            self.start_when_located = False
            self._start_bot()

        if dirty:
            # The game can send hundreds of packets per tick. Coalesce the
            # drawing instead of queueing a redraw for every packet.
            self._schedule_map_redraw()
            self._schedule_stats_redraw()
        return True

    def _track_packet(self, packet: Packet) -> None:
        if packet.direction is not Kind.RECV:
            return
        if packet.opcode in ("tp", "mv"):
            values = packet.ints(4) if packet.opcode == "tp" else packet.ints(5)
            if values and values[0] == 1:
                self.last_move = (values[0], values[1], values[2], values[3])
                self.last_move_time = time.monotonic()
                # The server answers an injected walk with
                # `tp 1 <charId> <x> <y>`; match it to the pending target to
                # learn our own char id even before the first `rest`.
                if (
                    packet.opcode == "tp"
                    and self.pending_target is not None
                    and (values[2], values[3]) == self.pending_target
                ):
                    self._learn_char_id(values[1])
        elif packet.opcode == "at":
            values = packet.ints(1)
            if values:
                self._learn_char_id(values[0])

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
        # The GUI can attach after login, so there may be no `at` packet.
        # Ask the DLL for the client's own local position instead of sending a
        # probe walk (which would make the server teleport the character).
        self._request_position()

    def _request_position(self) -> bool:
        if self.client is None or not self.client.connected:
            return False
        try:
            self.client.send_raw(POS_COMMAND)
        except (ConnectionLost, ValueError):
            return False
        return True

    def _set_status(self, text: str) -> None:
        if self.status is not None:
            self.status.set_text(text)

    def _set_path_info(self, text: str) -> None:
        if getattr(self, "path_info", None) is not None:
            self.path_info.set_text(text)

    # -- bot ------------------------------------------------------------

    def _char_id(self) -> int | None:
        return self.char_id or self.engine.world.self_id

    def _learn_char_id(self, char_id: int | None) -> None:
        if char_id is None or char_id <= 0:
            return
        self.char_id = char_id
        self._update_char_info()

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

    def _client_move(self, x: int, y: int) -> bool:
        """Walk through the game's own MoveTo function (not packet injection)."""
        if self.client is None or not self.client.connected:
            self._set_status("not connected")
            return False
        try:
            self.client.send_raw(f"{MOVE_PREFIX}{x} {y}")
        except (ConnectionLost, ValueError) as exc:
            self._set_status(f"move failed: {exc}")
            self._stop_bot()
            return False
        return True

    def _start_bot(self) -> None:
        if self.bot_running:
            return
        if not self.path_cells:
            # A path is optional: without one the bot can stand and fight /
            # pick up / recover (stationary combat mode).
            if not (
                (self.auto_attack_toggle is not None
                 and self.auto_attack_toggle.get_active())
                or (self.pickup_toggle is not None
                    and self.pickup_toggle.get_active())
                or (self.recover_toggle is not None
                    and self.recover_toggle.get_active())
            ):
                self._set_status("add waypoints or enable combat/pickup")
                return
        if self._player_cell() is None:
            self.start_when_located = True
            self._request_position()
            self._set_status(
                "locating character; starting when the position arrives"
            )
            return
        # The client's MoveTo already does the pathfinding and animation. Give
        # it a multi-cell leg per call instead of one cell: a per-cell MOVE
        # starts and stops the walk every few hundred ms, which looks robotic.
        # Three cells per MOVE is short enough that the client animates it and
        # long enough that the movement is continuous.
        if self.path_cells:
            self.bot_goals = [
                self.path_cells[i] for i in range(2, len(self.path_cells), 3)
            ]
            if (
                not self.bot_goals
                or self.bot_goals[-1] != self.path_cells[-1]
            ):
                self.bot_goals.append(self.path_cells[-1])
        elif self.waypoints:
            self.bot_goals = list(self.waypoints)
        else:
            self.bot_goals = []
        self.bot_running = True
        self.path_index = 0
        self.pending_target = None
        self.stand_sent_until = 0.0
        self.attack_cooldown_until = 0.0
        self.walk_not_before = 0.0
        self.rejected_walks = 0
        self.pickup_cooldown_until = 0.0
        self.pickup_attempts.clear()
        self.pickup_ignored.clear()
        self.recovering = False
        self.recover_rest_at = 0.0
        self.unknown_warn_at = 0.0
        self.out_of_range_warn_at = 0.0
        if self.bot_timer is None:
            self.bot_timer = GLib.timeout_add(BOT_INTERVAL_MS, self._bot_tick)
        self._set_status("bot started")
        self._set_path_info(
            f"running · {len(self.bot_goals)} leg(s) · loop "
            f"{'on' if self.loop_toggle.get_active() else 'off'}"
        )

    def _stop_bot(self) -> None:
        self.bot_running = False
        self.start_when_located = False
        self.pending_target = None
        if self.bot_timer is not None:
            GLib.source_remove(self.bot_timer)
            self.bot_timer = None
        self.recovering = False
        self._set_status("bot stopped")
        self._set_path_info("stopped")

    def _advance_path(self) -> None:
        self.path_index += 1
        if self.path_index >= len(self.bot_goals):
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
            if not self._monster_allowed(entity):
                continue
            distance = max(abs(entity.x - px), abs(entity.y - py))
            if distance <= radius and distance < best_distance:
                best = entity
                best_distance = distance
        return best

    def _nearest_drop(self, radius: int) -> GroundItem | None:
        position = self._player_cell()
        char_id = self._char_id()
        if position is None or char_id is None:
            return None
        px, py = position
        now = time.monotonic()
        best: GroundItem | None = None
        best_distance = radius + 1
        for drop in self.engine.world.drops.values():
            if drop.owner_id not in (0, char_id):
                continue
            if self.pickup_attempts.get(drop.drop_id, 0) >= 3:
                continue
            if self.pickup_ignored.get(drop.drop_id, 0.0) > now:
                continue
            distance = max(abs(drop.x - px), abs(drop.y - py))
            if distance <= radius and distance < best_distance:
                best = drop
                best_distance = distance
        return best

    def _skill_packet(
        self,
        target_id: int,
        skill: int | str | None = None,
        target_type: int = 3,
    ) -> str:
        if skill is None:
            skill = self.skill_entry.get_text().strip() or "0"
        else:
            skill = str(skill)
        template = (
            self.skill_packet_entry.get_text().strip()
            or "u_s {skill} {type} {target}"
        )
        try:
            return template.format(
                skill=skill, type=target_type, target=target_id
            )
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
        char_id = self._char_id()

        # -- recovery: sit below the configured HP %, wait for full HP -----
        recover_enabled = (
            self.recover_toggle is not None
            and self.recover_toggle.get_active()
        )
        if (
            recover_enabled
            and char_id is not None
            and world.stats.hp_max > 0
        ):
            percent = 100.0 * world.stats.hp / world.stats.hp_max
            threshold = float(self.low_hp_pct.get_value())
            if not self.recovering and percent < threshold:
                self.recovering = True
                self.pending_target = None
                self.attack_cooldown_until = 0.0
                self._set_status(
                    f"HP {percent:.0f}% below {threshold:.0f}%; recovering"
                )
            if self.recovering:
                if world.stats.hp >= world.stats.hp_max:
                    self.recovering = False
                    if world.resting is True:
                        self._send(f"rest 1 1 {char_id}")
                    self._set_status("HP full; resuming")
                    self._schedule_stats_redraw()
                    return True
                if world.resting is not True and now >= self.recover_rest_at:
                    self._send(f"rest 1 1 {char_id}")
                    self.recover_rest_at = now + 2.5
                    self._set_status("sitting to recover HP")
                self._schedule_stats_redraw()
                return True
        elif self.recovering:
            self.recovering = False

        if self.pending_target is not None:
            # The client's own MoveTo sends a `walk` SEND packet; the engine
            # updates world.position from it. A server `tp`/`mv` is accepted
            # too, in case the server corrects the client.
            confirmed = world.position == self.pending_target or (
                self.last_move_time >= self.pending_since
                and self.last_move is not None
                and self.last_move[0] == 1
                and (self.last_move[2], self.last_move[3]) == self.pending_target
            )
            ready_at = self.pending_since + max(
                0.35, self.pending_distance * self.walk_confirm_delay
            )
            if confirmed and now >= ready_at:
                self._set_status(f"arrived at {self.pending_target}")
                self.pending_target = None
                self.rejected_walks = 0
                self._advance_path()
            elif now > self.pending_until:
                self.rejected_walks += 1
                if self.rejected_walks >= MAX_REJECTED_WALKS:
                    self._stop_bot()
                    self._set_status(
                        f"{self.rejected_walks} walks rejected; stopped "
                        "(check standing and own position)"
                    )
                    return False
                self._set_status(
                    f"walk to {self.pending_target} not confirmed; skipping"
                )
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
            self.pickup_toggle is not None
            and self.pickup_toggle.get_active()
            and char_id is not None
            and now >= self.pickup_cooldown_until
        ):
            drop = self._nearest_drop(int(self.loot_range.get_value()))
            if drop is not None:
                if self._send(f"get 1 {char_id} {drop.drop_id}"):
                    attempts = self.pickup_attempts.get(drop.drop_id, 0) + 1
                    self.pickup_attempts[drop.drop_id] = attempts
                    if attempts >= 3:
                        self.pickup_ignored[drop.drop_id] = now + 15.0
                    self.pickup_cooldown_until = now + 0.6
                    self._set_status(
                        f"pickup {drop.item_vnum} x{drop.amount} "
                        f"({drop.drop_id})"
                    )
                return True

        move_first = (
            self.move_first_toggle is not None
            and self.move_first_toggle.get_active()
            and bool(self.bot_goals)
        )
        auto_attack = (
            self.auto_attack_toggle is not None
            and self.auto_attack_toggle.get_active()
        )
        if auto_attack:
            radius = int(self.attack_range.get_value())
            monster = None
            if (
                self.retaliate_toggle is not None
                and self.retaliate_toggle.get_active()
            ):
                monster = self._retaliation_target(radius)
            if monster is None:
                monster = self._nearest_monster(radius)

            if (
                monster is None
                and self._current_monster_mode() == "only"
                and not (
                    self.attack_unknown_toggle is not None
                    and self.attack_unknown_toggle.get_active()
                )
            ):
                unknown = self._unknown_monsters_in_range(radius)
                if unknown and now >= self.unknown_warn_at:
                    self._set_status(
                        f"{unknown} monster(s) in range have unknown vnum; "
                        "enable 'Also attack monsters with unknown vnum'"
                    )
                    self.unknown_warn_at = now + 15.0

            selected = self._select_spell_rule(now, monster, char_id)
            rule_index, rule = (
                selected if selected is not None else (None, None)
            )
            if rule is not None and now >= self.attack_cooldown_until:
                if self._cast_spell(
                    rule_index, rule, monster, char_id, now
                ):
                    return True

            basic = self._entry_int(self.skill_entry, 0)
            basic_vnum = basic if basic > 0 else self._basic_skill_vnum()
            basic_info = (
                self.spells.get(basic_vnum) if basic_vnum else None
            )
            basic_range = float(radius)
            if basic_info is not None and basic_info.range > 0:
                # The search radius may be larger than the skill; the server
                # rejects an out-of-range attack, so the skill's range wins.
                basic_range = min(basic_range, float(basic_info.range))
            basic_distance = None
            standing_at = self._player_cell()
            if monster is not None and standing_at is not None:
                basic_distance = max(
                    abs(monster.x - standing_at[0]),
                    abs(monster.y - standing_at[1]),
                )
            basic_in_range = (
                monster is not None
                and basic_distance is not None
                and basic_distance <= basic_range
            )
            if (
                rule is None
                and monster is not None
                and basic_in_range
                and now >= self.attack_cooldown_until
            ):
                stats = self.engine.world.stats
                if (
                    basic_info is not None
                    and basic_info.mp_cost > 0
                    and stats.mp < basic_info.mp_cost
                ):
                    if now >= self.mp_warn_at:
                        self._set_status(
                            f"{basic_info.name}: not enough MP "
                            f"({stats.mp}/{basic_info.mp_cost})"
                        )
                        self.mp_warn_at = now + 10.0
                else:
                    send_skill = (
                        self._skill_send_id(basic) if basic > 0 else 0
                    )
                    if send_skill is None:
                        if now >= self.slot_warn_at:
                            self._set_status(
                                f"skill {basic}: u_s slot unknown — use it "
                                "once in game or relog with the logger attached"
                            )
                            self.slot_warn_at = now + 10.0
                    else:
                        self._send(f"ncif 3 {monster.entity_id}")
                        packet = self._skill_packet(
                            monster.entity_id, skill=send_skill
                        )
                        self._send(packet)
                        # The skill's own cast time and cooldown pace the next
                        # action; the Min delay spin is only a floor on top.
                        cooldown = float(self.attack_cooldown.get_value())
                        if basic_info is not None:
                            cooldown = max(
                                cooldown,
                                basic_info.cooldown,
                                basic_info.cast_time,
                            )
                        else:
                            cooldown = max(cooldown, 1.0)
                        self.attack_cooldown_until = now + cooldown
                        # Do not walk inside the skill animation: the server
                        # drops the walk and the bot would skip the path.
                        cast_pause = (
                            basic_info.cast_time
                            if basic_info is not None
                            else 0.0
                        )
                        self.walk_not_before = now + max(
                            self.attack_walk_delay, cast_pause
                        )
                        self._set_status(
                            f"attack {monster.entity_id}: {packet}"
                        )
                        return True

            if (
                rule is None
                and monster is not None
                and not basic_in_range
                and now >= self.out_of_range_warn_at
            ):
                self._set_status(
                    f"monster {monster.entity_id} is {basic_distance} cells "
                    f"away; basic range {basic_range:g} — not attacking"
                )
                self.out_of_range_warn_at = now + 10.0

            if (
                monster is not None
                and not move_first
                and (rule is not None or basic_in_range)
            ):
                # Clear-the-area mode: stay on this position until the
                # nearby monsters are dead. Move first keeps walking
                # between attacks instead.
                return True

        if not self.bot_goals:
            # Stationary mode: combat/pickup/recovery continue until Stop.
            if (
                (self.auto_attack_toggle is not None
                 and self.auto_attack_toggle.get_active())
                or (self.pickup_toggle is not None
                    and self.pickup_toggle.get_active())
                or (self.recover_toggle is not None
                    and self.recover_toggle.get_active())
            ):
                return True
            self._set_status("path finished")
            self._stop_bot()
            return False

        if self.path_index >= len(self.bot_goals):
            if self.loop_toggle.get_active():
                self.path_index = 0
            else:
                self._set_status("path finished")
                self._stop_bot()
                return False

        target = self.bot_goals[self.path_index]
        current = self._player_cell()
        if current is not None and current == target:
            self._advance_path()
            return True

        if now < self.walk_not_before:
            return True

        if self._client_move(target[0], target[1]):
            self.pending_target = target
            self.pending_since = now
            # A whole leg takes ~0.45 s per cell. Diagonal legs cover
            # sqrt(2) cells of travel, so pace and time out by Euclidean
            # distance, not by the Chebyshev cell count.
            cells = 0
            travel = 0.0
            if current is not None:
                dx = target[0] - current[0]
                dy = target[1] - current[1]
                cells = max(abs(dx), abs(dy))
                travel = math.hypot(dx, dy)
            timeout = min(
                60.0,
                max(self.walk_timeout, travel * self.walk_timeout_per_cell),
            )
            self.pending_distance = travel
            self.pending_until = now + timeout
            self._set_status(f"walking to {target} ({cells} cells)")
        return True

    def do_shutdown(self) -> None:
        self._closing = True
        self._stop_bot()
        if self.client is not None:
            self.client.close()
        Gtk.Application.do_shutdown(self)


def run_gui(
    host: str = "127.0.0.1",
    port: int | None = None,
    map_data: str | None = None,
    log_file: str | None = None,
) -> int:
    app = Application(host, port, map_data, log_file=log_file)
    return int(app.run([]))
