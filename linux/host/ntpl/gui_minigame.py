"""The Minigame page: Sheep Farm key spam and focus.

Extracted from gui.py; every method still runs on the Application
instance, so it uses self state exactly like the core methods.
"""


from .client import ConnectionLost
from .engine import Entity
from .guicommon import MINIGAME_FOCUS_INTERVAL_S
from .guicommon import MINIGAME_FOCUS_REFRESH_S
from .guicommon import MINIGAME_RENDER_INTERVAL_S
from .guicommon import MINIGAME_TICK_MS
from .minigame import MAX_INTERVAL_MS
from .minigame import MAX_RADIUS
from .minigame import MIN_INTERVAL_MS
from .minigame import MIN_RADIUS
from .minigame import MinigameConfig
from .minigame import load_config as load_minigame_config
from .minigame import nearby_players
from .minigame import nearest_player
from .minigame import normalize_key
from .minigame import save_config as save_minigame_config
from gi.repository import GLib
from gi.repository import Gtk
from pathlib import Path
import os
import time

class MinigamePageMixin:
    def _build_minigame_page(self) -> Gtk.Widget:
        """Sheep Farm helper: key spam plus a players-only focus target."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_margin_top(18)
        page.set_margin_bottom(18)
        page.set_margin_start(18)
        page.set_margin_end(18)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        heading = Gtk.Label()
        heading.set_markup("<b>Minigame</b>")
        heading.set_xalign(0)
        header.append(heading)
        header.append(
            self._help_button(
                "Minigame",
                "Sheep Farm raid tools. In the raid an NPC sheep is worth 5 "
                "points and another player 20, and the spacebar only targets "
                "real sheep. Start sends one key press to the attached game "
                "window every interval, without running the bot; Focus "
                "nearby players selects the closest other character through "
                "the client's own selector, keeps it while it stays in "
                "range, and switches when it leaves. Sheep, pets, NPCs, "
                "monsters and your own character are never chosen. The "
                "default y is the secondary-weapon attack on layouts that "
                "bind it to Y; on QWERTZ the same action is Z, so type z if y "
                "does nothing. Keep the game window focused: the DLL then "
                "sends real key input, as if typed; with the GUI focused it "
                "posts the key to the game's queue instead. Use it with the "
                "bot off: both change the client's target. The key press "
                "needs DLL 0.3.0: after updating the build, restart the game "
                "client and inject again (the GUI alone is not enough), then "
                "press Start. This is automation; the account risk is yours.",
            )
        )
        page.append(header)

        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        controls.set_margin_top(8)
        controls.set_margin_bottom(8)
        controls.set_margin_start(8)
        controls.set_margin_end(8)

        first = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        first.set_halign(Gtk.Align.START)
        self.minigame_toggle = Gtk.ToggleButton(label="Start")
        self.minigame_toggle.set_tooltip_text(
            "Start or stop the key spam and player focus"
        )
        self.minigame_toggle.connect(
            "toggled", lambda *_: self._on_minigame_toggled()
        )
        self._bind_toggle_color(self.minigame_toggle)
        first.append(self.minigame_toggle)

        first.append(Gtk.Label(label="Key"))
        self.minigame_key_entry = Gtk.Entry()
        self.minigame_key_entry.set_text(self.minigame_config.key)
        self.minigame_key_entry.set_max_length(12)
        self.minigame_key_entry.set_width_chars(8)
        self.minigame_key_entry.set_tooltip_text(
            "One letter/digit (default y) or a named key: space, enter, tab, "
            "esc, F1–F12, arrows, page up/down. The secondary attack is Z on "
            "a QWERTZ layout."
        )
        self.minigame_key_entry.connect(
            "changed", lambda *_: self._on_minigame_settings_changed()
        )
        first.append(self.minigame_key_entry)

        first.append(Gtk.Label(label="every"))
        self.minigame_interval_spin = Gtk.SpinButton.new_with_range(
            MIN_INTERVAL_MS, MAX_INTERVAL_MS, 10
        )
        self.minigame_interval_spin.set_value(
            self.minigame_config.interval_ms
        )
        self.minigame_interval_spin.set_width_chars(6)
        self.minigame_interval_spin.set_tooltip_text(
            "Delay between key presses in milliseconds"
        )
        self.minigame_interval_spin.connect(
            "value-changed", lambda *_: self._on_minigame_settings_changed()
        )
        first.append(self.minigame_interval_spin)
        first.append(Gtk.Label(label="ms"))
        controls.append(first)

        second = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        second.set_halign(Gtk.Align.START)
        self.minigame_focus_switch = Gtk.CheckButton(
            label="Focus nearby players"
        )
        self.minigame_focus_switch.set_active(
            self.minigame_config.focus_players
        )
        self.minigame_focus_switch.set_tooltip_text(
            "Select the nearest other player character only — never an NPC, "
            "pet or monster"
        )
        self.minigame_focus_switch.connect(
            "toggled", lambda *_: self._on_minigame_settings_changed()
        )
        second.append(self.minigame_focus_switch)
        second.append(Gtk.Label(label="within"))
        self.minigame_radius_spin = Gtk.SpinButton.new_with_range(
            MIN_RADIUS, MAX_RADIUS, 1
        )
        self.minigame_radius_spin.set_value(self.minigame_config.radius)
        self.minigame_radius_spin.set_width_chars(4)
        self.minigame_radius_spin.set_tooltip_text(
            "Player search radius in map tiles (Chebyshev distance)"
        )
        self.minigame_radius_spin.connect(
            "value-changed", lambda *_: self._on_minigame_settings_changed()
        )
        second.append(self.minigame_radius_spin)
        second.append(Gtk.Label(label="tiles"))
        controls.append(second)

        page.append(
            self._frame(
                "Sheep Farm",
                controls,
                help_text=(
                    "In the Sheep Farm raid players are disguised among the "
                    "sheep NPCs, so the focus deliberately ignores every "
                    "entity that is not another character; a selected player "
                    "is what the spammed attack hits. Key is sent to the game "
                    "window by the DLL; the interval is the delay between "
                    "presses and the radius is in map tiles. Settings are "
                    "saved per attached client."
                ),
            )
        )

        self.minigame_status = Gtk.Label(label="")
        self.minigame_status.set_xalign(0)
        self.minigame_status.set_wrap(True)
        self.minigame_status.set_max_width_chars(80)
        self.minigame_status.add_css_class("dim-label")
        page.append(self.minigame_status)
        self._minigame_render_status()
        return page

    @staticmethod
    def _minigame_path_for(winpid: int | None) -> Path:
        override = os.environ.get("NTPL_MINIGAME_CONFIG")
        if override:
            return Path(override)
        base = os.environ.get("XDG_CONFIG_HOME") or str(
            Path.home() / ".config"
        )
        name = (
            f"minigame-{winpid}.json"
            if winpid is not None
            else "minigame.json"
        )
        return Path(base) / "ntpl" / name

    def _minigame_set_status(
        self, text: str, css: str | None = None
    ) -> None:
        if self.minigame_status is None:
            return
        self.minigame_status.set_text(text)
        for name in ("bazar-ok", "bazar-bad", "bazar-warn"):
            self.minigame_status.remove_css_class(name)
        if css is not None:
            self.minigame_status.add_css_class(css)

    def _minigame_read_config(self) -> MinigameConfig | None:
        if (
            self.minigame_key_entry is None
            or self.minigame_interval_spin is None
            or self.minigame_focus_switch is None
            or self.minigame_radius_spin is None
        ):
            return None
        key = normalize_key(self.minigame_key_entry.get_text())
        if key is None:
            self._minigame_set_status(
                "Key must be one letter/digit or a named key "
                "(space, enter, tab, esc, F1–F12, arrows).",
                "bazar-bad",
            )
            return None
        return MinigameConfig(
            key=key,
            interval_ms=int(self.minigame_interval_spin.get_value()),
            focus_players=bool(self.minigame_focus_switch.get_active()),
            radius=int(self.minigame_radius_spin.get_value()),
        ).normalized()

    def _minigame_apply_config(self) -> None:
        self.minigame_guard = True
        try:
            if self.minigame_key_entry is not None:
                self.minigame_key_entry.set_text(self.minigame_config.key)
            if self.minigame_interval_spin is not None:
                self.minigame_interval_spin.set_value(
                    self.minigame_config.interval_ms
                )
            if self.minigame_focus_switch is not None:
                self.minigame_focus_switch.set_active(
                    self.minigame_config.focus_players
                )
            if self.minigame_radius_spin is not None:
                self.minigame_radius_spin.set_value(
                    self.minigame_config.radius
                )
        finally:
            self.minigame_guard = False
        self._minigame_render_status()

    def _minigame_save_config(self) -> None:
        if self.minigame_config_path is None:
            return
        try:
            save_minigame_config(
                self.minigame_config_path, self.minigame_config
            )
        except OSError as exc:
            self._minigame_set_status(
                f"Cannot save settings: {exc}", "bazar-bad"
            )

    def _on_minigame_settings_changed(self) -> None:
        if self.minigame_guard:
            return
        config = self._minigame_read_config()
        if config is None:
            return
        self.minigame_config = config
        self._minigame_touched = True
        self._minigame_save_config()
        self._minigame_render_status()

    def _minigame_reset_for_attach(self, winpid: int) -> None:
        """Load this client's settings; a new client never auto-starts."""
        self._minigame_stop(quiet=True)
        self.dll_key_supported = True
        self.minigame_config_path = self._minigame_path_for(winpid)
        self.minigame_config = load_minigame_config(
            self.minigame_config_path
        )
        self.minigame_focus_blocked.clear()
        self.minigame_target_requests.clear()
        self._minigame_apply_config()

    def _on_minigame_toggled(self) -> None:
        if self.minigame_guard or self.minigame_toggle is None:
            return
        if self.minigame_toggle.get_active():
            self._minigame_start()
        else:
            self._minigame_stop()

    def _minigame_abort(self, text: str) -> None:
        self.minigame_running = False
        if self.minigame_timer is not None:
            GLib.source_remove(self.minigame_timer)
            self.minigame_timer = None
        self.minigame_guard = True
        try:
            if self.minigame_toggle is not None:
                self.minigame_toggle.set_active(False)
                self.minigame_toggle.set_label("Start")
        finally:
            self.minigame_guard = False
        self._minigame_set_status(text, "bazar-bad")

    def _minigame_start(self) -> None:
        if self.client is None or not self.client.connected:
            self._minigame_abort("Not connected to a game client.")
            return
        config = self._minigame_read_config()
        if config is None:
            message = (
                self.minigame_status.get_text()
                if self.minigame_status is not None
                else ""
            )
            self._minigame_abort(message or "Fix the settings first.")
            return
        if not self.dll_key_supported:
            self._minigame_abort(
                "This DLL has no KEY command. Restart the game client and "
                "inject the 0.3.0 DLL, then try again."
            )
            return
        self.minigame_config = config
        self._minigame_touched = True
        if (
            self.minigame_key_entry is not None
            and self.minigame_key_entry.get_text() != config.key
        ):
            self.minigame_guard = True
            self.minigame_key_entry.set_text(config.key)
            self.minigame_guard = False
        self._minigame_save_config()
        if self.minigame_toggle is not None:
            self.minigame_toggle.set_label("Stop")
        self.minigame_running = True
        now = time.monotonic()
        self.minigame_next_key = now
        self.minigame_next_focus = now
        self.minigame_next_render = now + MINIGAME_RENDER_INTERVAL_S
        self.minigame_focus_id = None
        self.minigame_focus_refresh_at = 0.0
        if self.minigame_timer is None:
            self.minigame_timer = GLib.timeout_add(
                MINIGAME_TICK_MS, self._minigame_tick
            )
        focus = (
            f"players within {config.radius} tiles"
            if config.focus_players
            else "player focus off"
        )
        self._minigame_set_status(
            f"Sending {config.key} every {config.interval_ms} ms · {focus}.",
            "bazar-warn",
        )
        self._log_line(
            f"minigame start: key {config.key} every "
            f"{config.interval_ms} ms, focus={focus}"
        )

    def _minigame_stop(self, quiet: bool = False) -> None:
        was_running = self.minigame_running
        self.minigame_running = False
        if self.minigame_timer is not None:
            GLib.source_remove(self.minigame_timer)
            self.minigame_timer = None
        if self.minigame_toggle is not None:
            self.minigame_guard = True
            try:
                self.minigame_toggle.set_active(False)
                self.minigame_toggle.set_label("Start")
            finally:
                self.minigame_guard = False
        self.minigame_focus_id = None
        self.minigame_focus_name = ""
        self.minigame_focus_refresh_at = 0.0
        if quiet:
            self._minigame_render_status()
            return
        if was_running:
            self._log_line("minigame stop")
        self._minigame_render_status()

    def _minigame_player_name(self, entity: Entity) -> str:
        name = (
            entity.name or self.log_names.get(entity.entity_id, "")
        ).strip()
        return name or f"char {entity.entity_id}"

    def _minigame_tick(self) -> bool:
        if not self.minigame_running:
            self.minigame_timer = None
            return False
        if self.client is None or not self.client.connected:
            self._minigame_abort("Minigame stopped: not connected.")
            return False
        now = time.monotonic()
        config = self.minigame_config
        if config.focus_players and now >= self.minigame_next_focus:
            self._minigame_focus(now)
            self.minigame_next_focus = now + MINIGAME_FOCUS_INTERVAL_S
        if now >= self.minigame_next_key:
            try:
                self.client.send_raw(f"KEY|{config.key}")
            except (ConnectionLost, ValueError) as exc:
                self._minigame_abort(f"Minigame stopped: {exc}")
                return False
            self.minigame_next_key = now + config.interval_ms / 1000.0
        if now >= self.minigame_next_render:
            self._minigame_render_status()
            self.minigame_next_render = now + MINIGAME_RENDER_INTERVAL_S
        return True

    def _minigame_focus(self, now: float) -> None:
        """Select the nearest other player, never an NPC or a monster."""
        position = self._player_cell()
        if position is None:
            return
        config = self.minigame_config
        own_id = self._char_id()
        world = self.engine.world

        for entity_id, until in list(self.minigame_focus_blocked.items()):
            if until <= now:
                self.minigame_focus_blocked.pop(entity_id, None)
        for entity_id, until in list(
            self.minigame_target_requests.items()
        ):
            if until <= now:
                self.minigame_target_requests.pop(entity_id, None)
        blocked = set(self.minigame_focus_blocked)

        target: tuple[Entity, int] | None = None
        current = (
            world.entities.get(self.minigame_focus_id)
            if self.minigame_focus_id is not None
            else None
        )
        if (
            current is not None
            and current.kind == 1
            and current.entity_id != own_id
            and current.entity_id not in blocked
        ):
            distance = max(
                abs(current.x - position[0]),
                abs(current.y - position[1]),
            )
            if distance <= config.radius:
                target = (current, distance)
        if target is None:
            target = nearest_player(
                world.entities.values(),
                own_id,
                position,
                config.radius,
                exclude=blocked,
            )
        if target is None:
            if self.minigame_focus_id is not None:
                self.minigame_focus_id = None
                self.minigame_focus_name = ""
                self._log_line("minigame focus: no player in range")
            return

        entity, distance = target
        if (
            entity.entity_id == self.minigame_focus_id
            and now < self.minigame_focus_refresh_at
            and world.target_id == self.minigame_focus_id
        ):
            # The client still has this player selected: re-selecting would
            # only add another ncif. If a key press or the target's death
            # moved the client's target, the next 0.5 s tick repairs it before
            # the following key press.
            return
        # The client's own selector sets the local target and sends its ncif.
        # Players are selected by type 1 when the DLL is too old for TARGET.
        sent = False
        if self.dll_target_supported:
            # Remember the id for 10 s: its TARGET ERR/OK may arrive after
            # the focus has already moved to another player.
            self.minigame_target_requests[entity.entity_id] = now + 10.0
            sent = self._client_target(entity.entity_id)
        if not sent:
            sent = self._send(f"ncif 1 {entity.entity_id}")
        if not sent:
            return
        name = self._minigame_player_name(entity)
        changed = entity.entity_id != self.minigame_focus_id
        self.minigame_focus_id = entity.entity_id
        self.minigame_focus_name = name
        # Re-send soon: if TARGET turns out to be missing from this DLL, the
        # "unknown command" reply lands after the send and the next tick must
        # retry through the ncif fallback, not wait for a long refresh.
        self.minigame_focus_refresh_at = now + MINIGAME_FOCUS_REFRESH_S
        if changed:
            self._log_line(
                f"minigame focus -> {name} ({entity.entity_id}) at "
                f"{distance} tiles"
            )

    def _minigame_render_status(self) -> None:
        if self.minigame_status is None:
            return
        config = self.minigame_config
        if not self.minigame_running:
            focus = (
                f"focus players within {config.radius} tiles"
                if config.focus_players
                else "focus off"
            )
            self._minigame_set_status(
                f"Off · key {config.key} every {config.interval_ms} ms · "
                f"{focus}."
            )
            return
        parts = [
            f"Sending {config.key} every {config.interval_ms} ms"
        ]
        if config.focus_players:
            position = self._player_cell()
            rows = nearby_players(
                self.engine.world.entities.values(),
                self._char_id(),
                position,
                config.radius,
                exclude=set(self.minigame_focus_blocked),
            )
            count = (
                "1 player"
                if len(rows) == 1
                else f"{len(rows)} players"
            )
            player = (
                self.engine.world.entities.get(self.minigame_focus_id)
                if self.minigame_focus_id is not None
                else None
            )
            if player is not None and position is not None:
                distance = max(
                    abs(player.x - position[0]),
                    abs(player.y - position[1]),
                )
                name = self._minigame_player_name(player)
                parts.append(
                    f"{count} in range · focusing {name} "
                    f"({distance} tiles)"
                )
            elif rows:
                parts.append(f"{count} in range · no target selected")
            else:
                parts.append(
                    f"no player within {config.radius} tiles"
                )
        else:
            parts.append("player focus off")
        self._minigame_set_status(" · ".join(parts), "bazar-warn")
