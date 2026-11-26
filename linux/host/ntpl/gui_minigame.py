"""The Minigame page: Sheep Farm and Miniland production minigames.

Sheep Farm spams a key and focuses the nearest player. The Miniland
production section plays (Great Quarry first) by sending the client's own
`mg` result/claim packets for the chosen reward tier.

Extracted from gui.py; every method still runs on the Application
instance, so it uses self state exactly like the core methods.
"""


from .client import ConnectionLost
from .engine import Entity
from .guicommon import MINIGAME_FOCUS_INTERVAL_S
from .guicommon import MINIGAME_FOCUS_REFRESH_S
from .guicommon import MINIGAME_RAID_FRESH_S
from .guicommon import MINIGAME_RENDER_INTERVAL_S
from .guicommon import MINIGAME_TICK_MS
from .minigame import DEFAULT_TARGET_LEVEL
from .minigame import MAX_INTERVAL_MS
from .minigame import MAX_RADIUS
from .minigame import MIN_INTERVAL_MS
from .minigame import MIN_RADIUS
from .minigame import PRODUCTION_GAMES
from .minigame import QUARRY_VNUM
from .minigame import TARGET_LEVELS
from .minigame import MinigameConfig
from .minigame import build_production_close_packet
from .minigame import build_production_gift_packet
from .minigame import build_production_result_packet
from .minigame import build_production_start_packet
from .minigame import load_config as load_minigame_config
from .minigame import nearby_players
from .spawnlog import load_minigame_info
from .minigame import nearest_player
from .minigame import normalize_key
from .minigame import save_config as save_minigame_config
from .minigame import tier_minimum
from gi.repository import GLib
from gi.repository import Gtk
from pathlib import Path
import os
import time

class MinigamePageMixin:
    def _build_minigame_page(self) -> Gtk.Widget:
        """Sheep Farm tools plus the Miniland production minigame."""
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
                "press Start. The Miniland production section below is "
                "separate: it sends a production minigame result and claims "
                "the chosen reward box (Great Quarry first). This is "
                "automation; the account risk is yours.",
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

        page.append(self._build_production_section())
        GLib.timeout_add(1000, self._quarry_points_tick)
        return page

    # -- Miniland production (Great Quarry) -----------------------------

    def _build_production_section(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)
        box.set_margin_end(4)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_halign(Gtk.Align.START)
        row.append(Gtk.Label(label="Game"))
        self.quarry_game_drop = Gtk.DropDown.new_from_strings(
            [f"{name} ({vnum})" for vnum, name in PRODUCTION_GAMES[:1]]
        )
        self.quarry_game_drop.set_tooltip_text(
            "Production object. Great Quarry is the live-verified game; the "
            "others share the packet flow and are added after a live test."
        )
        row.append(self.quarry_game_drop)
        row.append(Gtk.Label(label="Play until"))
        self.quarry_level_drop = Gtk.DropDown.new_from_strings(
            [
                f"{level}x · {tier_minimum(level):,} pts"
                for level in TARGET_LEVELS
            ]
        )
        self.quarry_level_drop.set_selected(DEFAULT_TARGET_LEVEL - 1)
        self.quarry_level_drop.set_tooltip_text(
            "Reward box to claim; the result reports the minimum score of "
            "that box."
        )
        row.append(self.quarry_level_drop)
        box.append(row)

        self.quarry_points_label = Gtk.Label(
            label="production points: — (open the minigame window)"
        )
        self.quarry_points_label.set_xalign(0)
        self.quarry_points_label.add_css_class("dim-label")
        box.append(self.quarry_points_label)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.START)
        self.quarry_play_button = Gtk.Button(label="Play")
        self.quarry_play_button.add_css_class("suggested-action")
        self.quarry_play_button.set_tooltip_text(
            "Claim the chosen box repeatedly until the production points "
            "run out (35 s between claims)."
        )
        self.quarry_play_button.connect(
            "clicked", lambda *_: self._quarry_start()
        )
        buttons.append(self.quarry_play_button)
        self.quarry_stop_button = Gtk.Button(label="Stop")
        self.quarry_stop_button.set_tooltip_text(
            "Cancel the running start → result → claim sequence."
        )
        self.quarry_stop_button.connect(
            "clicked", lambda *_: self._quarry_stop()
        )
        buttons.append(self.quarry_stop_button)
        buttons.append(Gtk.Label(label="every"))
        self.quarry_gap_spin = Gtk.SpinButton.new_with_range(30, 600, 5)
        self.quarry_gap_spin.set_value(35)
        self.quarry_gap_spin.set_width_chars(4)
        self.quarry_gap_spin.set_tooltip_text(
            "Seconds between claims. Live testing puts the server cooldown "
            "at about 30 s, so the default is 35; raise this if a run gets "
            "no mlo_lv."
        )
        buttons.append(self.quarry_gap_spin)
        buttons.append(Gtk.Label(label="s"))
        box.append(buttons)

        self.quarry_status = Gtk.Label(label="")
        self.quarry_status.set_xalign(0)
        self.quarry_status.set_wrap(True)
        self.quarry_status.set_max_width_chars(80)
        self.quarry_status.add_css_class("dim-label")
        box.append(self.quarry_status)
        self.quarry_countdown_label = Gtk.Label(label="")
        self.quarry_countdown_label.set_xalign(0)
        self.quarry_countdown_label.add_css_class("dim-label")
        box.append(self.quarry_countdown_label)

        return self._frame(
            "Miniland production",
            box,
            help_text=(
                "Plays a Miniland production game (Great Quarry 3125) by "
                "sending the client's own packets: mg 1 starts, mg 3 carries "
                "the score, mg 4 claims the box. 'Play until' picks the "
                "reward box, and the score is that box's minimum (the "
                "default 5x is 16,000 points; the server's own mlo_info "
                "tier table wins when it sent one). Open the Great Quarry "
                "window in the game first and keep it open: that is what "
                "tells the host the object, your production points and the "
                "live tiers. Each claim spends production points (100 "
                "normally; the label shows the last known amount, refilled "
                "daily). Play is automatic: it claims, clicks the client's Stop "
                "(Storno), waits 'every' seconds (35 by default, the server's "
                "cooldown) and "
                "starts the next one until the production points fall below "
                "100, a raid team blocks it or a claim gets no answer; Stop "
                "ends the loop. This is a packet shortcut, not a visual "
                "play-through: the server can reject it, and it violates "
                "the game rules with a high ban risk. The game refuses the "
                "minigame while you are in a raid team (\"You cannot use it "
                "when you are already a member of a raid team\"); the tab "
                "checks the last raid list and tells you to leave it first. "
                "The Minigame page's Sheep Farm tools are separate and need "
                "the bot off."
            ),
        )

    def _quarry_selected_level(self) -> int:
        if self.quarry_level_drop is None:
            return DEFAULT_TARGET_LEVEL
        index = int(self.quarry_level_drop.get_selected())
        if 0 <= index < len(TARGET_LEVELS):
            return TARGET_LEVELS[index]
        return DEFAULT_TARGET_LEVEL

    def _quarry_selected_vnum(self) -> int:
        if self.quarry_game_drop is None:
            return QUARRY_VNUM
        index = int(self.quarry_game_drop.get_selected())
        games = PRODUCTION_GAMES[:1]
        if 0 <= index < len(games):
            return games[index][0]
        return QUARRY_VNUM

    def _quarry_set_status(self, text: str, css: str | None = None) -> None:
        if self.quarry_status is None:
            return
        self.quarry_status.set_text(text)
        for name in ("bazar-ok", "bazar-bad", "bazar-warn"):
            self.quarry_status.remove_css_class(name)
        if css is not None:
            self.quarry_status.add_css_class(css)

    def _quarry_in_raid(self) -> bool:
        """True while the local character is in a fresh raid/party list.

        The game refuses the production minigames inside a raid team
        ("You cannot use it when you are already a member of a raid team"),
        so the tab checks the last `rdlst` before sending anything.
        """
        world = self.engine.world
        if not world.raid_members:
            return False
        if time.monotonic() - world.raid_at > MINIGAME_RAID_FRESH_S:
            return False
        own = self._char_id()
        return own is not None and own in world.raid_members

    def _quarry_points_tick(self) -> bool:
        if self._closing:
            return False
        if self.quarry_points_label is not None:
            points = self.engine.world.minigame.points
            if points is None:
                text = "production points: — (open the minigame window)"
            else:
                text = f"production points: {points:,}"
            if self._quarry_in_raid():
                text += (
                    f" · in a raid team ({len(self.engine.world.raid_members)})"
                    " — leave it to play"
                )
            self.quarry_points_label.set_text(text)
        if self.quarry_countdown_label is not None:
            if (
                self.quarry_auto_running
                and self.quarry_auto_next_at > 0.0
            ):
                remaining = max(
                    0,
                    int(
                        self.quarry_auto_next_at
                        - time.monotonic()
                        + 0.999
                    ),
                )
                self.quarry_countdown_label.set_text(
                    f"auto: next claim in {remaining} s"
                )
            elif self.quarry_auto_running:
                self.quarry_countdown_label.set_text(
                    "auto: claim in progress…"
                )
            else:
                self.quarry_countdown_label.set_text("")
        # The 1 s tick is the authoritative clock for the wait between
        # claims. A one-shot GLib.timeout_add_seconds was not delivered
        # reliably in the live GUI (the countdown ran but the claim after
        # it never started), so the tick starts the claim itself.
        if (
            self.quarry_auto_running
            and self.quarry_auto_next_at > 0.0
            and not self.quarry_running
            and time.monotonic() >= self.quarry_auto_next_at
        ):
            self._quarry_auto_next()
        return True

    def _quarry_play(self) -> None:
        if self.quarry_running:
            return
        if self.client is None or not self.client.connected:
            self._quarry_set_status(
                "Not connected to a game client.", "bazar-bad"
            )
            return
        if self._quarry_in_raid():
            self._quarry_set_status(
                "You are in a raid team — the minigame cannot be used. "
                "Leave the raid team, then press Play.",
                "bazar-bad",
            )
            return
        world = self.engine.world
        level = self._quarry_selected_level()
        vnum = self._quarry_selected_vnum()
        target = tier_minimum(level, world.minigame.tiers)
        if target is None:
            self._quarry_set_status(
                "Cannot resolve the reward tiers.", "bazar-bad"
            )
            return
        if (
            world.minigame.points is not None
            and world.minigame.points < 100
        ):
            self._quarry_set_status(
                f"Production points {world.minigame.points:,} — the claim "
                "needs at least 100.",
                "bazar-bad",
            )
            return
        name = dict(PRODUCTION_GAMES).get(vnum, str(vnum))
        slot = int(world.minigame.slot or 1)
        self.quarry_running = True
        self.quarry_step = 0
        self.quarry_vnum = vnum
        self.quarry_slot = slot
        self.quarry_level = level
        self.quarry_target = target
        self.quarry_points_before = world.minigame.points
        self.quarry_seen = world.minigame.update_count
        self.quarry_seen_status = world.minigame.status_count
        self.quarry_reward_seen = world.minigame.reward_count
        self.quarry_deadline = time.monotonic() + 1.2
        auto_prefix = (
            f"auto {self.quarry_auto_done + 1} · "
            if self.quarry_auto_running
            else ""
        )
        self._quarry_set_status(
            f"{auto_prefix}{name}: starting · slot {slot} · target "
            f"{level}x at {target:,} points…",
            "bazar-warn",
        )
        self._log_line(
            f"minigame: {name} start (slot {slot}), target {level}x "
            f"({target:,} pts)"
        )
        if not self._send(build_production_start_packet(vnum, slot)):
            self._quarry_finish()
            self._quarry_set_status(
                "Could not send the start packet.", "bazar-bad"
            )
            return
        if self.quarry_timer is None:
            self.quarry_timer = GLib.timeout_add(
                100, self._quarry_tick
            )

    def _quarry_finish(self) -> None:
        self.quarry_running = False
        self.quarry_timer = None

    def _quarry_stop(self, quiet: bool = False) -> None:
        self.quarry_running = False
        self.quarry_auto_running = False
        self.quarry_auto_next_at = 0.0
        if self.quarry_timer is not None:
            GLib.source_remove(self.quarry_timer)
            self.quarry_timer = None
        if not quiet:
            self._quarry_set_status("stopped.")

    def _quarry_gap(self) -> int:
        if self.quarry_gap_spin is None:
            return 35
        return max(5, int(self.quarry_gap_spin.get_value()))

    def _quarry_start(self) -> None:
        """Play: claim the chosen box until the production points run out."""
        if self.quarry_running or self.quarry_auto_running:
            return
        if self.client is None or not self.client.connected:
            self._quarry_set_status(
                "Not connected to a game client.", "bazar-bad"
            )
            return
        if self._quarry_in_raid():
            self._quarry_set_status(
                "You are in a raid team — the minigame cannot be used. "
                "Leave the raid team, then press Play.",
                "bazar-bad",
            )
            return
        points = self.engine.world.minigame.points
        if points is not None and points < 100:
            self._quarry_set_status(
                f"Production points {points:,} — a claim needs at least "
                "100.",
                "bazar-bad",
            )
            return
        self.quarry_auto_done = 0
        self.quarry_auto_retries = 0
        self.quarry_auto_running = True
        self._log_line(
            f"minigame: start, claiming the {self._quarry_selected_level()}x "
            f"box until the production points run out, "
            f"{self._quarry_gap()} s apart"
        )
        self._quarry_play()
        if not self.quarry_running:
            self.quarry_auto_running = False

    def _quarry_auto_after_success(self) -> None:
        if not self.quarry_auto_running:
            return
        self.quarry_auto_done += 1
        # Dismiss the result window with the client's own Stop button
        # (mg 2 -1 -1). The window-close form (mg 2 <slot> <vnum>) makes
        # the dialog disappear and leaves the window out of sync.
        self._send(build_production_close_packet())
        points = self.engine.world.minigame.points
        if points is not None and points < 100:
            self._quarry_auto_finish(
                f"auto stopped after {self.quarry_auto_done} claim(s): "
                f"production points {points:,} (a claim needs 100)"
            )
            return
        self.quarry_auto_retries = 0
        gap = self._quarry_gap()
        self._quarry_set_status(
            f"auto {self.quarry_auto_done} claim(s) · next claim in "
            f"{gap} s…",
            "bazar-warn",
        )
        self.quarry_auto_next_at = time.monotonic() + gap

    def _quarry_auto_next(self) -> None:
        """Start the next claim once the server cooldown has passed.

        Called by the 1 s points tick when its due time is reached.
        """
        self.quarry_auto_next_at = 0.0
        if not self.quarry_auto_running or self.quarry_running:
            return
        if self.client is None or not self.client.connected:
            self._quarry_auto_abort("auto stopped: not connected")
            return
        if self._quarry_in_raid():
            self._quarry_auto_abort(
                "auto stopped: you are in a raid team — leave it and "
                "start again"
            )
            return
        points = self.engine.world.minigame.points
        if points is not None and points < 100:
            self._quarry_auto_finish(
                f"auto stopped: production points {points:,} (a claim "
                "needs 100)"
            )
            return
        self._quarry_play()
        if not self.quarry_running:
            self.quarry_auto_running = False

    def _quarry_auto_retry(self, message: str) -> None:
        """Retry an ignored run after the cooldown; stop after 3 attempts."""
        self.quarry_auto_retries += 1
        if self.quarry_auto_retries > 3:
            self._quarry_auto_abort(
                f"auto stopped: {message} "
                f"({self.quarry_auto_retries - 1} attempt(s))"
            )
            return
        # Dismiss the failed result window before the next attempt.
        self._send(build_production_close_packet())
        gap = self._quarry_gap()
        self._quarry_set_status(
            f"auto {self.quarry_auto_done + 1} · {message} · retry "
            f"{self.quarry_auto_retries}/3 in {gap} s…",
            "bazar-warn",
        )
        self._log_line(
            f"minigame: auto retry {self.quarry_auto_retries}/3 in "
            f"{gap} s: {message}"
        )
        self.quarry_auto_next_at = time.monotonic() + gap

    def _quarry_auto_finish(self, text: str) -> None:
        self.quarry_auto_running = False
        self.quarry_auto_next_at = 0.0
        self._quarry_set_status(text, "bazar-ok")
        self._log_line(f"minigame: {text}")

    def _quarry_auto_abort(self, text: str) -> None:
        self.quarry_auto_running = False
        self.quarry_auto_next_at = 0.0
        self._quarry_set_status(text, "bazar-bad")
        self._log_line(f"minigame: {text}")

    def _quarry_tick(self) -> bool:
        if not self.quarry_running:
            self.quarry_timer = None
            return False
        if self.client is None or not self.client.connected:
            self._quarry_finish()
            if self.quarry_auto_running:
                self._quarry_auto_abort("auto stopped: not connected")
            else:
                self._quarry_set_status(
                    "Minigame stopped: not connected.", "bazar-bad"
                )
            return False
        now = time.monotonic()
        world = self.engine.world
        if self.quarry_step == 2:
            # The server's answer can arrive at any moment; do not wait out
            # the whole deadline before showing a confirmed claim.
            answered = world.minigame.update_count > self.quarry_seen
            after = world.minigame.points
            reward_vnum = world.minigame.reward_vnum
            new_reward = (
                answered
                and reward_vnum is not None
                and reward_vnum != self.quarry_reward_before
            )
            if answered and (
                after != self.quarry_points_before or new_reward
            ):
                self._quarry_finish()
                before_text = (
                    f"{self.quarry_points_before:,}"
                    if self.quarry_points_before is not None
                    else "?"
                )
                after_text = f"{after:,}" if after is not None else "?"
                reward_text = ""
                if new_reward:
                    name = self._item_name_for(int(reward_vnum))
                    if not name:
                        name = f"item {reward_vnum}"
                    reward_text = (
                        f" · reward {name} ×"
                        f"{world.minigame.reward_amount:,}"
                    )
                claimed = self.quarry_claim_level + 1
                auto_prefix = (
                    f"auto {self.quarry_auto_done + 1} · "
                    if self.quarry_auto_running
                    else ""
                )
                self._quarry_set_status(
                    f"{auto_prefix}{claimed}x claimed · production points "
                    f"{before_text} → {after_text}{reward_text}",
                    "bazar-ok",
                )
                self._log_line(
                    f"minigame: {auto_prefix}{claimed}x claimed, production "
                    f"points {before_text} -> {after_text}{reward_text}"
                )
                self._quarry_auto_after_success()
                return False
            if now >= self.quarry_deadline:
                self._quarry_finish()
                if answered:
                    message = (
                        "server answered but the production points did not "
                        "change; the claim may have been refused"
                    )
                else:
                    message = (
                        "no answer from the server; the minigame window may "
                        "be closed or the result rejected"
                    )
                if self.quarry_auto_running:
                    self._quarry_auto_retry(message)
                else:
                    self._quarry_set_status(message, "bazar-bad")
                    self._log_line(f"minigame: {message}")
                return False
            return True
        # The real client's GAMESTART is answered by `mlo_st`; once that
        # arrives the result may go out. The fixed delay is the fallback for
        # a server that stays silent.
        started = world.minigame.status_count > self.quarry_seen_status
        if now < self.quarry_deadline and not (
            started and now >= self.quarry_deadline - 0.6
        ):
            return True
        if self.quarry_step == 0:
            if not self._send(
                build_production_result_packet(
                    self.quarry_vnum, self.quarry_target, self.quarry_slot
                )
            ):
                self._quarry_finish()
                if self.quarry_auto_running:
                    self._quarry_auto_abort(
                        "auto stopped: could not send the result packet"
                    )
                else:
                    self._quarry_set_status(
                        "Could not send the result packet.", "bazar-bad"
                    )
                return False
            self.quarry_step = 1
            self.quarry_reward_seen = world.minigame.reward_count
            self.quarry_deadline = now + 2.0
            self._quarry_set_status(
                f"result sent ({self.quarry_target:,} points) · waiting "
                "for the reward level…",
                "bazar-warn",
            )
            return True
        if self.quarry_step == 1:
            # The live server answers the result with `mlo_lv <level>`
            # (0-based: 4 is the 5x box) and the client claims exactly that
            # level. Use it when it arrives; fall back to the chosen level
            # minus one so a silent server still gets a plausible claim.
            if world.minigame.reward_count > self.quarry_reward_seen:
                claim_level = world.minigame.reward_level
                if claim_level is None:
                    claim_level = max(0, self.quarry_level - 1)
            elif now >= self.quarry_deadline:
                claim_level = max(0, self.quarry_level - 1)
                self._log_line(
                    "minigame: no mlo_lv from the server; claiming level "
                    f"{claim_level} ({self.quarry_level}x)"
                )
            else:
                return True
            self.quarry_claim_level = int(claim_level)
            # Only a response after the claim counts as confirmation.
            self.quarry_seen = world.minigame.update_count
            self.quarry_points_before = world.minigame.points
            self.quarry_reward_before = world.minigame.reward_vnum
            if not self._send(
                build_production_gift_packet(
                    self.quarry_vnum, self.quarry_claim_level, self.quarry_slot
                )
            ):
                self._quarry_finish()
                if self.quarry_auto_running:
                    self._quarry_auto_abort(
                        "auto stopped: could not send the claim packet"
                    )
                else:
                    self._quarry_set_status(
                        "Could not send the claim packet.", "bazar-bad"
                    )
                return False
            self.quarry_step = 2
            self.quarry_deadline = now + 5.0
            self._quarry_set_status(
                f"claiming the {self.quarry_claim_level + 1}x box "
                f"(server level {self.quarry_claim_level})…",
                "bazar-warn",
            )
            return True
        return True

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

    def _minigame_seed_from_log(self) -> None:
        """Seed the production object/points from the attached client's log.

        The Great Quarry window is often opened before the host attaches (or
        before a GUI restart), and the server does not repeat `mlo_info` when
        the window is already open. Without this seed the production-points
        label and the live tier table stay empty until the window is closed
        and reopened.
        """
        world = self.engine.world
        if world.minigame.vnum is not None or world.minigame.points is not None:
            return
        try:
            info = load_minigame_info(self.log_file)
        except Exception:  # noqa: BLE001 - the seed is optional
            info = None
        if not info:
            return
        state = world.minigame
        state.vnum = info.get("vnum")
        state.slot = info.get("slot")
        state.points = info.get("points")
        tiers = info.get("tiers")
        if isinstance(tiers, list) and tiers:
            state.tiers = list(tiers)
        elif isinstance(tiers, tuple) and tiers:
            state.tiers = list(tiers)
        state.update_count += 1
        state.updated_at = time.monotonic()
        if state.vnum is not None:
            name = dict(PRODUCTION_GAMES).get(state.vnum, str(state.vnum))
            points = (
                f" · production points {state.points:,}"
                if state.points is not None
                else ""
            )
            self._log_line(f"minigame: {name} from the client log{points}")

    def _minigame_reset_for_attach(self, winpid: int) -> None:
        """Load this client's settings; a new client never auto-starts."""
        self._minigame_stop(quiet=True)
        self._quarry_stop(quiet=True)
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
