"""The Family page: manual and automatic invites.

Extracted from gui.py; every method still runs on the Application
instance, so it uses self state exactly like the core methods.
"""


from .client import ConnectionLost
from .guicommon import FAMILY_AUTO_TICK_MS
from .protocol import Kind
from .protocol import Packet
from gi.repository import GLib
from gi.repository import Gtk
from pathlib import Path
import json
import math
import os
import time

class FamilyPageMixin:
    def _build_family_page(self) -> Gtk.Widget:
        """Family tools; the first feature is inviting a member."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.set_margin_top(16)
        page.set_margin_bottom(16)
        page.set_margin_start(16)
        page.set_margin_end(16)
        page.set_vexpand(True)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        heading = Gtk.Label()
        heading.set_markup("<b>Family</b>")
        heading.set_xalign(0)
        header.append(heading)
        header.append(
            self._help_button(
                "Family",
                "Invite a player to your family. Type the exact character "
                "name and press Invite: the tab sends the client's own "
                "%Familyinvite <name> command. The player must be online and "
                "accept, and your character needs permission to invite. "
                "Manual %Familyinvite commands typed in game are picked up "
                "here too. Players on map shows everyone known on the map "
                "and their family; Invited players lists confirmed or "
                "manually added names. Remove a name to let auto-invite try "
                "it again; Add to list records a name without sending.",
            )
        )
        page.append(header)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_halign(Gtk.Align.START)
        row.append(Gtk.Label(label="Player"))
        self.family_name_entry = Gtk.Entry()
        self.family_name_entry.set_placeholder_text("player name")
        self.family_name_entry.set_max_length(25)
        self.family_name_entry.set_width_chars(24)
        self.family_name_entry.connect(
            "activate", lambda *_: self._on_family_invite()
        )
        row.append(self.family_name_entry)
        self.family_invite_button = Gtk.Button(label="Invite")
        self.family_invite_button.add_css_class("suggested-action")
        self.family_invite_button.connect(
            "clicked", lambda *_: self._on_family_invite()
        )
        row.append(self.family_invite_button)
        add_to_list = Gtk.Button(label="Add to list")
        add_to_list.set_tooltip_text(
            "Record a name as already invited without sending anything."
        )
        add_to_list.connect(
            "clicked", lambda *_: self._on_family_add_to_list()
        )
        row.append(add_to_list)
        page.append(row)

        auto_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        auto_row.set_halign(Gtk.Align.START)
        self.family_auto_toggle = Gtk.CheckButton(
            label="Auto-invite players with no family"
        )
        self.family_auto_toggle.set_active(False)
        self.family_auto_toggle.connect(
            "toggled", lambda *_: self._on_family_auto_toggled()
        )
        auto_row.append(self.family_auto_toggle)
        auto_row.append(
            self._help_button(
                "Auto-invite",
                "Scans the map for players whose family state is known and "
                "who have no family, nearest first. It sends one invite, "
                "waits for the server's confirmation, then waits out the "
                "server's invite cooldown (the countdown on the right). "
                "Every confirmed name is saved per client and never retried, "
                "even if the player declines. The Only players with a fairy "
                "option excludes fresh/alt accounts. It stops if the server "
                "stops confirming, so a client without family permission "
                "cannot spam invites.",
            )
        )
        self.family_timer_label = Gtk.Label(label="")
        self.family_timer_label.add_css_class("dim-label")
        auto_row.append(self.family_timer_label)
        page.append(auto_row)

        fairy_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        fairy_row.set_halign(Gtk.Align.START)
        self.family_only_fairy = Gtk.CheckButton(
            label="Only players with a fairy"
        )
        self.family_only_fairy.set_active(False)
        self.family_only_fairy.connect(
            "toggled", lambda *_: self._on_family_only_fairy_toggled()
        )
        fairy_row.append(self.family_only_fairy)
        fairy_row.append(
            self._help_button(
                "Fairy filter",
                "When auto-invite is on, invite only known no-family players "
                "who have a fairy. Players without a fairy are usually fresh "
                "alt accounts. The setting is saved with the invited list.",
            )
        )
        page.append(fairy_row)

        self.family_status = Gtk.Label(label="")
        self.family_status.set_xalign(0)
        self.family_status.set_wrap(True)
        self.family_status.set_max_width_chars(60)
        self.family_status.add_css_class("dim-label")
        page.append(self.family_status)

        split = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        split.set_wide_handle(True)
        split.set_hexpand(True)
        split.set_vexpand(True)
        page.append(split)

        players_panel = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        players_panel.set_vexpand(True)
        self.family_players_info = Gtk.Label(label="Players on map: 0")
        self.family_players_info.set_xalign(0)
        self.family_players_info.add_css_class("dim-label")
        players_panel.append(self.family_players_info)
        self.family_players_list = Gtk.ListBox()
        self.family_players_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.family_players_list.set_activate_on_single_click(False)
        self.family_players_scroll = Gtk.ScrolledWindow()
        self.family_players_scroll.set_child(self.family_players_list)
        self.family_players_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        self.family_players_scroll.set_hexpand(True)
        self.family_players_scroll.set_vexpand(True)
        players_panel.append(self.family_players_scroll)
        split.set_start_child(players_panel)
        split.set_resize_start_child(True)

        invited_panel = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        invited_panel.set_vexpand(True)
        invited_panel.set_size_request(340, -1)
        self.family_invited_info = Gtk.Label(label="Invited players: 0")
        self.family_invited_info.set_xalign(0)
        self.family_invited_info.add_css_class("dim-label")
        invited_panel.append(self.family_invited_info)
        self.family_invited_list = Gtk.ListBox()
        self.family_invited_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.family_invited_list.set_activate_on_single_click(False)
        self.family_invited_scroll = Gtk.ScrolledWindow()
        self.family_invited_scroll.set_child(self.family_invited_list)
        self.family_invited_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        self.family_invited_scroll.set_vexpand(True)
        self.family_invited_scroll.set_visible(False)
        invited_panel.append(self.family_invited_scroll)
        split.set_end_child(invited_panel)
        split.set_resize_end_child(False)
        split.set_position(700)

        self.family_state_path = self._family_path_for(None)
        self._family_load_state()
        self._family_render_invited(force=True)
        if self._family_timer_source is None:
            self._family_timer_source = GLib.timeout_add(
                1000, self._family_ui_tick
            )
        self._family_render_players()
        return page

    @staticmethod
    def _family_usable_name(name: str) -> bool:
        # Placeholder entities on busy maps can have names that are only
        # underscores or spacing; they are not real players to invite.
        return (
            bool(name)
            and not any(character.isspace() for character in name)
            and any(character.isalnum() for character in name)
        )

    def _family_path_for(self, winpid: int | None) -> Path:
        override = os.environ.get("NTPL_FAMILY_STATE")
        if override:
            return Path(override)
        if winpid is None and self.attached_client is not None:
            winpid = self.attached_client.winpid
        base = os.environ.get("XDG_CONFIG_HOME") or str(
            Path.home() / ".config"
        )
        name = (
            f"family-{winpid}.json" if winpid is not None else "family.json"
        )
        return Path(base) / "ntpl" / name

    def _family_load_state(self) -> None:
        self._family_invited.clear()
        path = self.family_state_path
        if path is None:
            return
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return
        only_with_fairy = None
        if isinstance(data, dict):
            only_with_fairy = data.get("only_with_fairy")
            data = data.get("invited", [])
        if not isinstance(data, list):
            return
        if isinstance(only_with_fairy, bool) and self.family_only_fairy is not None:
            self._family_only_fairy_guard = True
            self.family_only_fairy.set_active(only_with_fairy)
            self._family_only_fairy_guard = False
        for item in data:
            if isinstance(item, str) and item.strip():
                name = item.strip()
                self._family_invited[name.casefold()] = name

    def _family_save_state(self) -> None:
        path = self.family_state_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "invited": sorted(
                            self._family_invited.values(),
                            key=str.casefold,
                        ),
                        "only_with_fairy": bool(
                            self.family_only_fairy is not None
                            and self.family_only_fairy.get_active()
                        ),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError as exc:
            self._log_line(f"family list: cannot save {path}: {exc}")

    def _family_render_invited(self, force: bool = False) -> None:
        if self.family_invited_list is None:
            return
        count = len(self._family_invited)
        no_reply = len(self._family_no_reply)
        if self.family_invited_info is not None:
            text = f"Invited players: {count}"
            if no_reply:
                text += f" · {no_reply} no reply"
            self.family_invited_info.set_text(text)
        if self.family_invited_scroll is not None:
            self.family_invited_scroll.set_visible(
                count > 0 or no_reply > 0
            )
        # The count/visibility above are cheap; the row rebuild is throttled
        # so a burst of confirmations cannot rebuild dozens of rows each.
        now = time.monotonic()
        if not force and now < self._family_invited_next_at:
            self._family_invited_dirty = True
            return
        self._family_invited_next_at = now + 1.0
        self._family_invited_dirty = False
        while (
            child := self.family_invited_list.get_first_child()
        ) is not None:
            self.family_invited_list.remove(child)
        entries = dict(self._family_invited)
        for key, name in self._family_no_reply.items():
            entries.setdefault(key, name)
        for key, name in sorted(
            entries.items(), key=lambda item: item[1].casefold()
        ):
            silent = (
                key in self._family_no_reply
                and key not in self._family_invited
            )
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            row.set_selectable(False)
            box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=6
            )
            box.set_margin_top(2)
            box.set_margin_bottom(2)
            label = Gtk.Label(
                label=name + ("  ·  no reply" if silent else "")
            )
            label.set_xalign(0)
            label.set_hexpand(True)
            if silent:
                label.add_css_class("bazar-warn")
            box.append(label)
            remove = Gtk.Button(label="Remove")
            remove.add_css_class("flat")
            remove.set_valign(Gtk.Align.CENTER)
            remove.connect(
                "clicked",
                lambda _button, chosen=name: self._family_remove(chosen),
            )
            box.append(remove)
            row.set_child(box)
            self.family_invited_list.append(row)

    def _family_remove(self, name: str) -> None:
        key = name.casefold()
        if key not in self._family_invited and key not in self._family_no_reply:
            return
        self._family_invited.pop(key, None)
        self._family_no_reply.pop(key, None)
        pending = self._family_pending
        if pending is not None and pending[0].casefold() == key:
            self._family_pending = None
        self._family_save_state()
        self._family_render_invited()
        self._family_render_players()
        self._set_family_status(
            f"Removed {name} from the invited list.", "bazar-warn"
        )
        self._log_line(f"family list: removed {name}")

    def _on_family_add_to_list(self) -> None:
        if self.family_name_entry is None:
            return
        name = self.family_name_entry.get_text().strip()
        if not self._family_usable_name(name):
            self._set_family_status(
                "Enter one player name.", "bazar-bad"
            )
            return
        if name.casefold() in self._family_invited:
            self._set_family_status(
                f"{name} is already in the invited list.", "bazar-warn"
            )
            return
        self._family_remember(name)
        self._set_family_status(
            f"Added {name} to the invited list.", "bazar-ok"
        )
        self._log_line(f"family list: added {name}")

    def _on_family_only_fairy_toggled(self) -> None:
        if self._family_only_fairy_guard:
            return
        self._family_players_sig = None
        self._family_save_state()
        self._family_render_players()

    def _family_cooldown_remaining(self) -> int:
        return max(
            0, math.ceil(self._family_next_invite_at - time.monotonic())
        )

    def _family_update_timer(self) -> None:
        if self.family_timer_label is None:
            return
        remaining = self._family_cooldown_remaining()
        self.family_timer_label.set_text(
            f"Next invite in {remaining}s" if remaining > 0 else ""
        )

    def _family_ui_tick(self) -> bool:
        if self._closing:
            self._family_timer_source = None
            return False
        # Do not rebuild a 100-row list in the background: only the visible
        # Family page needs it, and twice a second is plenty for a map.
        if self.family_page is None or self.notebook is None:
            return True
        if self.notebook.get_current_page() != self.notebook.page_num(
            self.family_page
        ):
            return True
        self._family_update_timer()
        if self._family_invited_dirty:
            self._family_render_invited(force=True)
        now = time.monotonic()
        if now >= self._family_players_next_at:
            self._family_players_next_at = now + 1.0
            self._family_render_players()
        return True

    def _family_render_players(self) -> None:
        if self.family_players_list is None:
            return
        world = self.engine.world
        own_id = self._char_id()
        rows: list[tuple[str, str, bool, bool, bool, int]] = []
        total = 0
        to_invite = 0
        for entity in world.entities.values():
            if entity.kind != 1 or entity.entity_id == own_id:
                continue
            name = (
                entity.name or self.log_names.get(entity.entity_id, "")
            ).strip()
            if not self._family_usable_name(name):
                continue
            total += 1
            key = name.casefold()
            invited = key in self._family_invited
            silent = key in self._family_no_reply
            known_no_family = (
                entity.family_known
                and entity.family_id <= 0
                and entity.family_name in ("", "-")
            )
            only_fairy = bool(
                self.family_only_fairy is not None
                and self.family_only_fairy.get_active()
            )
            if (
                known_no_family
                and not invited
                and not silent
                and (not only_fairy or entity.has_fairy)
            ):
                to_invite += 1
            # Show only actionable rows: invite candidates and names already
            # in a list. A busy town's 167 "family unknown" rows made the GTK
            # list rebuild stall the whole window.
            if not (known_no_family or invited or silent):
                continue
            if entity.family_known and entity.family_id > 0:
                family = entity.family_name or "has family"
            elif entity.family_known:
                family = "no family"
            else:
                family = "family unknown"
            if entity.has_fairy:
                fairy = "fairy"
            elif entity.fairy_known:
                fairy = "no fairy"
            else:
                fairy = "fairy unknown"
            family = f"{family} · {fairy}"
            rows.append(
                (
                    name,
                    family,
                    invited,
                    silent,
                    entity.family_known,
                    entity.family_id,
                )
            )
        rows.sort(key=lambda row: row[0].casefold())
        rows = rows[:60]
        signature = tuple(
            (name, family, invited, silent)
            for name, family, invited, silent, _, _ in rows
        )
        if self.family_players_info is not None:
            self.family_players_info.set_text(
                f"Players on map: {total} · {to_invite} to invite"
            )
        if signature == self._family_players_sig:
            return
        self._family_players_sig = signature
        while (
            child := self.family_players_list.get_first_child()
        ) is not None:
            self.family_players_list.remove(child)
        for (
            name,
            family,
            invited,
            silent,
            family_known,
            family_id,
        ) in rows:
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            row.set_selectable(False)
            box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=6
            )
            box.set_margin_top(2)
            box.set_margin_bottom(2)
            label_text = f"{name}  ·  {family}"
            if silent:
                label_text += "  ·  no reply"
            label = Gtk.Label(label=label_text)
            label.set_xalign(0)
            label.set_hexpand(True)
            box.append(label)
            if invited:
                action = Gtk.Button(label="Remove")
                action.add_css_class("flat")
                action.connect(
                    "clicked",
                    lambda _button, chosen=name: self._family_remove(chosen),
                )
            elif silent:
                action = Gtk.Button(label="Retry")
                action.add_css_class("flat")
                action.connect(
                    "clicked",
                    lambda _button, chosen=name: self._family_send_invite(
                        chosen
                    ),
                )
            elif family_known and family_id <= 0:
                action = Gtk.Button(label="Invite")
                action.add_css_class("flat")
                action.connect(
                    "clicked",
                    lambda _button, chosen=name: self._family_send_invite(
                        chosen
                    ),
                )
            else:
                action = Gtk.Label(label="")
            action.set_valign(Gtk.Align.CENTER)
            box.append(action)
            row.set_child(box)
            self.family_players_list.append(row)
        if self.family_players_scroll is not None:
            self.family_players_scroll.set_visible(True)

    def _family_remember(self, name: str) -> None:
        key = name.casefold()
        if key in self._family_invited:
            return
        self._family_invited[key] = name
        self._family_no_reply.pop(key, None)
        self._family_save_state()
        self._family_render_invited()
        self._family_render_players()

    def _family_reset_for_attach(self, winpid: int) -> None:
        self._family_stop_auto(quiet=True)
        self._family_pending = None
        self._family_no_reply.clear()
        self._family_no_reply_streak = 0
        self._family_auto_last = 0.0
        self._family_status_hold_until = 0.0
        self._family_next_invite_at = 0.0
        self._family_players_sig = None
        self._family_players_next_at = 0.0
        self.family_state_path = self._family_path_for(winpid)
        self._family_load_state()
        self._family_render_invited(force=True)
        self._family_render_players()
        self._family_update_timer()
        self._set_family_status("")

    def _family_stop_auto(self, quiet: bool = False) -> None:
        if self._family_auto_timer is not None:
            GLib.source_remove(self._family_auto_timer)
            self._family_auto_timer = None
        if (
            self.family_auto_toggle is not None
            and self.family_auto_toggle.get_active()
        ):
            self._family_auto_guard = True
            self.family_auto_toggle.set_active(False)
            self._family_auto_guard = False
        if not quiet:
            self._set_family_status("Auto-invite off.", "bazar-warn")
            self._log_line("family auto-invite off")

    def _on_family_auto_toggled(self) -> None:
        if self._family_auto_guard or self.family_auto_toggle is None:
            return
        if not self.family_auto_toggle.get_active():
            if self._family_auto_timer is not None:
                GLib.source_remove(self._family_auto_timer)
                self._family_auto_timer = None
            self._set_family_status("Auto-invite off.", "bazar-warn")
            self._log_line("family auto-invite off")
            return
        if self.client is None or not self.client.connected:
            self._family_auto_guard = True
            self.family_auto_toggle.set_active(False)
            self._family_auto_guard = False
            self._set_family_status(
                "Not connected to a game client.", "bazar-bad"
            )
            return
        self._family_auto_last = 0.0
        self._family_no_reply_streak = 0
        if self._family_auto_timer is None:
            self._family_auto_timer = GLib.timeout_add(
                FAMILY_AUTO_TICK_MS, self._family_auto_tick
            )
        self._set_family_status(
            "Auto-invite on: looking for players without a family.",
            "bazar-warn",
        )
        self._log_line("family auto-invite on")

    def _family_auto_tick(self) -> bool:
        if (
            self.family_auto_toggle is None
            or not self.family_auto_toggle.get_active()
        ):
            self._family_auto_timer = None
            return False
        if self.client is None or not self.client.connected:
            self._family_stop_auto(quiet=True)
            self._set_family_status(
                "Auto-invite stopped: not connected.", "bazar-bad"
            )
            return False
        now = time.monotonic()
        pending = self._family_pending
        if pending is not None:
            if now - pending[1] > self.family_confirm_timeout:
                name = pending[0]
                key = name.casefold()
                self._family_pending = None
                # Offline, wrong-name and non-ASCII targets can be silently
                # ignored by the server. Skip that name and keep going
                # instead of stopping the whole run.
                self._family_no_reply[key] = name
                self._family_no_reply_streak += 1
                self._family_status_hold_until = now + 3.0
                self._family_render_invited()
                self._family_render_players()
                self._set_family_status(
                    f"No reply for {name}; skipped. Auto-invite continues.",
                    "bazar-warn",
                )
                self._log_line(f"family invite no reply: {name}")
                if self._family_no_reply_streak >= 5:
                    self._family_stop_auto(quiet=True)
                    self._set_family_status(
                        "Auto-invite stopped: 5 invites got no reply "
                        "(check the client's family permission).",
                        "bazar-bad",
                    )
                    self._log_line(
                        "family auto-invite stopped: repeated no reply"
                    )
                    return False
                return True
            return True
        remaining = self._family_cooldown_remaining()
        if remaining > 0:
            if now >= self._family_status_hold_until:
                self._set_family_status(
                    f"Auto-invite on: next invite in {remaining}s.",
                    "bazar-warn",
                )
            return True
        if now - self._family_auto_last < self.family_invite_interval:
            return True
        candidate = self._family_next_candidate()
        if candidate is None:
            if now >= self._family_status_hold_until:
                self._set_family_status(
                    "Auto-invite on: waiting for players without a family.",
                    "bazar-warn",
                )
            return True
        if self._family_send_invite(candidate, auto=True):
            self._family_auto_last = now
            return True
        self._family_stop_auto(quiet=True)
        return False

    def _family_next_candidate(self) -> str | None:
        world = self.engine.world
        own_id = self._char_id()
        own_cell = self._player_cell()
        candidates: list[tuple[int, str, str]] = []
        for entity in world.entities.values():
            if entity.kind != 1 or entity.entity_id == own_id:
                continue
            if not entity.family_known or entity.family_id > 0:
                continue
            if entity.family_name not in ("", "-"):
                continue
            name = (
                entity.name or self.log_names.get(entity.entity_id, "")
            ).strip()
            if not self._family_usable_name(name):
                continue
            key = name.casefold()
            if key in self._family_invited or key in self._family_no_reply:
                continue
            if (
                self.family_only_fairy is not None
                and self.family_only_fairy.get_active()
                and not entity.has_fairy
            ):
                continue
            distance = 0
            if own_cell is not None:
                distance = max(
                    abs(entity.x - own_cell[0]),
                    abs(entity.y - own_cell[1]),
                )
            candidates.append((distance, name.casefold(), name))
        if not candidates:
            return None
        return min(candidates)[2]

    def _family_send_invite(self, name: str, auto: bool = False) -> bool:
        if self.client is None or not self.client.connected:
            self._set_family_status(
                "Not connected to a game client.", "bazar-bad"
            )
            return False
        key = name.casefold()
        if key in self._family_invited:
            self._set_family_status(
                f"{name} was already invited.", "bazar-warn"
            )
            return False
        remaining = self._family_cooldown_remaining()
        if remaining > 0:
            self._set_family_status(
                f"Server cooldown: wait {remaining}s.", "bazar-warn"
            )
            return False
        try:
            self.client.send_packet(f"%Familyinvite {name}")
        except (ConnectionLost, ValueError) as exc:
            self._set_family_status(f"Invite failed: {exc}", "bazar-bad")
            return False
        now = time.monotonic()
        self._family_pending = (name, now)
        self._family_no_reply.pop(key, None)
        # The invite is added to the invited list when the server confirms it
        # with infoi2 234; a cooldown rejection (infoi 2359) must not burn the
        # name, so it can be retried after the timer.
        self._family_next_invite_at = now + self.family_cooldown_s
        self._family_update_timer()
        self._family_render_invited()
        self._family_render_players()
        prefix = "Auto-invite" if auto else "Invite"
        self._set_family_status(
            f"{prefix}: sent to {name}; waiting for the server…",
            "bazar-warn",
        )
        self._log_line(f"family invite -> {name}")
        return True

    def _set_family_status(self, text: str, css: str | None = None) -> None:
        if self.family_status is None:
            return
        self.family_status.set_text(text)
        for name in ("bazar-ok", "bazar-bad", "bazar-warn"):
            self.family_status.remove_css_class(name)
        if css is not None:
            self.family_status.add_css_class(css)

    def _on_family_invite(self) -> None:
        if self.family_name_entry is None:
            return
        name = self.family_name_entry.get_text().strip()
        if not self._family_usable_name(name):
            self._set_family_status(
                "Enter one player name.", "bazar-bad"
            )
            return
        self._family_send_invite(name)

    def _family_on_packet(self, packet: Packet) -> None:
        """Mirror manual invites and show the server's confirmation."""
        text = packet.text.strip()
        if packet.direction is Kind.SEND and text.startswith("%Familyinvite"):
            parts = text.split(maxsplit=1)
            name = parts[1].strip() if len(parts) == 2 else ""
            if name:
                now = time.monotonic()
                self._family_pending = (name, now)
                # A manual %Familyinvite typed in game also starts the
                # account-wide invite cooldown.
                self._family_next_invite_at = now + self.family_cooldown_s
                self._family_update_timer()
                self._set_family_status(
                    f"Invite sent to {name}; waiting for the server…",
                    "bazar-warn",
                )
            return
        if packet.direction is not Kind.RECV:
            return
        if packet.opcode == "infoi":
            parts = text.split()
            # infoi 2359 ... -- the server refused a too-early invite. Start
            # the cooldown and keep the name retryable.
            if len(parts) >= 2 and parts[1] == "2359":
                pending = self._family_pending
                self._family_pending = None
                self._family_next_invite_at = (
                    time.monotonic() + self.family_cooldown_s
                )
                self._family_update_timer()
                remaining = self._family_cooldown_remaining()
                suffix = f" ({pending[0]} not sent)" if pending else ""
                self._set_family_status(
                    f"Server cooldown: next invite in {remaining}s."
                    f"{suffix}",
                    "bazar-warn",
                )
                self._log_line("family invite cooldown (infoi 2359)")
            return
        if packet.opcode != "infoi2":
            return
        parts = text.split()
        # Live capture: infoi2 234 1 <name> follows %Familyinvite <name>.
        if len(parts) < 4 or parts[1] != "234":
            return
        name = " ".join(parts[3:]).strip()
        pending = self._family_pending
        if (
            pending is None
            or time.monotonic() - pending[1] > 60.0
            or name.casefold() != pending[0].casefold()
        ):
            return
        self._family_pending = None
        self._family_no_reply_streak = 0
        self._family_remember(name)
        self._family_status_hold_until = time.monotonic() + 4.0
        self._set_family_status(
            f"Server: invited {name} to the family.", "bazar-ok"
        )
        self._log_line(f"family invite confirmed: {name}")
