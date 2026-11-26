"""The Stats page: player DPS meter.

Extracted from gui.py; every method still runs on the Application
instance, so it uses self state exactly like the core methods.
"""


from .dpsmeter import sample_from_packet
from .guicommon import DpsRowView
from .guicommon import _column
from .protocol import Packet
from gi.repository import GLib
from gi.repository import Gio
from gi.repository import Gtk
import time

class StatsPageMixin:
    def _build_stats_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.set_margin_top(6)
        page.set_margin_bottom(6)
        page.set_margin_start(6)
        page.set_margin_end(6)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        toolbar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        self.dps_info = Gtk.Label(label="No player damage yet")
        self.dps_info.set_xalign(0)
        self.dps_info.set_hexpand(True)
        self.dps_info.add_css_class("dim-label")
        toolbar.append(self.dps_info)
        toolbar.append(Gtk.Label(label="Window"))
        self.dps_window = Gtk.DropDown.new_from_strings(
            ["5 s", "10 s", "30 s", "60 s", "Session"]
        )
        self.dps_window.set_selected(1)
        self.dps_window.set_tooltip_text(
            "DPS averaging window: damage in this window divided by it"
        )
        self.dps_window.connect(
            "notify::selected", lambda *_: self._dps_render()
        )
        toolbar.append(self.dps_window)
        reset = Gtk.Button(label="Reset")
        reset.set_tooltip_text("Clear every damage total and start again")
        reset.connect("clicked", lambda *_: self._dps_reset())
        toolbar.append(reset)
        content.append(toolbar)

        self.dps_store = Gio.ListStore.new(DpsRowView)
        selection = Gtk.NoSelection(model=self.dps_store)
        view = Gtk.ColumnView(model=selection)
        view.append_column(_column("Player", "name", expand=True))
        view.append_column(_column("DPS", "dps_text", width=90))
        view.append_column(_column("Damage", "damage_text", width=110))
        view.append_column(_column("Hits", "hits_text", width=70))
        view.append_column(_column("Share", "share_text", width=70))
        view.append_column(_column("Last hit", "last_text", width=90))
        view.append_column(_column("Session", "total_text", width=110))
        view.set_show_row_separators(True)
        scroll = Gtk.ScrolledWindow()
        scroll.set_child(view)
        scroll.set_vexpand(True)
        content.append(scroll)

        frame = self._frame(
            "DPS meter",
            content,
            help_text=(
                "Every player hit broadcast as `su` adds its damage: the "
                "decoded field 13 of the live packet, with the target HP in "
                "fields 16/17. Only attacker type 1 (players) counts, and "
                "zero-damage skills (buffs) are ignored. DPS is the damage "
                "in the chosen window divided by it; Session averages since "
                "Reset. Other players appear when the server broadcasts "
                "their hits. The table refreshes twice a second while this "
                "tab is visible; the meter keeps collecting in the "
                "background."
            ),
        )
        frame.set_vexpand(True)
        page.append(frame)
        self.dps_page = page
        page.connect("map", lambda *_: self._dps_render())
        self._dps_timer = GLib.timeout_add(500, self._dps_tick)
        return page

    def _dps_window_seconds(self) -> float | None:
        selected = (
            self.dps_window.get_selected() if self.dps_window else 1
        )
        windows: tuple[float | None, ...] = (5.0, 10.0, 30.0, 60.0, None)
        if selected < 0 or selected >= len(windows):
            return 10.0
        return windows[selected]

    def _dps_player_name(self, attacker_id: int) -> str:
        cached = self.dps_names.get(attacker_id)
        world = self.engine.world
        name = ""
        if attacker_id == world.self_id and world.self_name:
            name = world.self_name
        else:
            entity = world.entities.get(attacker_id)
            name = (getattr(entity, "name", "") or "").strip()
        if name:
            self.dps_names[attacker_id] = name
            return name
        return cached or f"char {attacker_id}"

    def _dps_on_packet(self, packet: Packet) -> None:
        sample = sample_from_packet(packet)
        if sample is None:
            return
        self.dps_meter.add(sample)
        if (
            sample.attacker_id == self.engine.world.self_id
            and self.engine.world.self_name
        ):
            self.dps_names.setdefault(
                sample.attacker_id, self.engine.world.self_name
            )

    def _dps_tick(self) -> bool:
        if self._closing:
            self._dps_timer = None
            return False
        if self.dps_page is not None and self.dps_page.get_mapped():
            self._dps_render()
        return True

    def _dps_render(self) -> None:
        if self.dps_store is None:
            return
        now = time.time()
        window = self._dps_window_seconds()
        rows = self.dps_meter.snapshot(now, window)
        self.dps_last_rows = rows
        self.dps_store.remove_all()
        for row in rows:
            self.dps_store.append(
                DpsRowView(row, self._dps_player_name(row.attacker_id), now)
            )
        if self.dps_info is None:
            return
        if not rows:
            self.dps_info.set_text("No player damage in this window")
            return
        top = rows[0]
        damage = sum(row.damage for row in rows)
        label = "session" if window is None else f"{window:g} s"
        self.dps_info.set_text(
            f"{len(rows)} player(s) · top "
            f"{self._dps_player_name(top.attacker_id)} {top.dps:,.0f} DPS · "
            f"{damage:,} damage · {label}"
        )

    def _dps_reset(self) -> None:
        self.dps_meter.reset()
        self.dps_last_rows = []
        self._dps_render()
        self._log_line("stats: DPS meter reset")
