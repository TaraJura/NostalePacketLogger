"""GTK4 front end.

Native GTK4 rather than a re-implementation of the WinForms window: it is the
default toolkit on Linux, ships with the desktop, and PyGObject is already
installed on a normal Ubuntu system (``python3-gi`` + ``gir1.2-gtk-4.0``).

The GTK loop and the socket reader never share a thread: the reader pushes into
a ``queue.Queue`` and a 50 ms GLib timeout drains it on the main loop.
"""

from __future__ import annotations

import queue
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from .client import ConnectionLost, PacketClient
from .protocol import Kind, Packet, ProtocolError

MAX_ROWS = 20000
TRIM_ROWS = 2000
DRAIN_INTERVAL_MS = 50


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
    def __init__(self, host: str, port: int | None) -> None:
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
        window.set_default_size(1150, 720)
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

        self.store = Gio.ListStore.new(PacketRow)
        self.filter_model = Gtk.FilterListModel.new(self.store)
        self.filter = Gtk.CustomFilter.new(self._matches)
        self.filter_model.set_filter(self.filter)

        # ColumnView takes a SelectionModel, not a FilterListModel directly.
        # NoSelection keeps the table read-only but still scrollable.
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

        self.status = Gtk.Label(label="connecting...")
        self.status.set_xalign(0)
        self.status.set_margin_start(8)
        self.status.set_margin_bottom(4)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        root.append(toolbar)
        root.append(self.scrolled)
        root.append(self.status)
        window.set_child(root)
        window.present()

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b".recv { color: #2aa198; } .send { color: #b58900; }"
        )
        display = Gdk.Display.get_default()
        if display is not None and hasattr(Gtk.StyleContext, "add_provider_for_display"):
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

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
                self._append(Packet.from_message(message))

        return True

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
        # The DLL sends its STATUS line immediately on connect. If that line
        # has already been drained, do not overwrite it with a generic notice.
        if not self.status_received:
            self._set_status(f"connected to {self.host}:{self.client.port}")

    def _set_status(self, text: str) -> None:
        if self.status is not None:
            self.status.set_text(text)

    def do_shutdown(self) -> None:
        if self.client is not None:
            self.client.close()
        Gtk.Application.do_shutdown(self)


def run_gui(host: str = "127.0.0.1", port: int | None = None) -> int:
    app = Application(host, port)
    return int(app.run([]))
