"""The Pet Trainer tab: tame, Miniland training and essence extraction.

The tab owns its own 50 ms timer and state machine, independent of the
Map/Bot bot. Phases:

* ``tame``    - Whip the chosen monster down and Tame it (the card's loop)
                until the server reports the pet slots full.
* ``train``   - in the Miniland, summon the sparring monster (skill 1788)
                and send the tamed monsters at it with ``suctl``.
* ``extract`` - cast Extract Essence (skill 1789) on each tamed monster; the
                released pet frees its slot and the loop tames again.

Live packet shapes are documented in docs/taming.md.
"""

from __future__ import annotations

import time

from .client import ConnectionLost
from .guicommon import REST_RETRY_S
from .tamer import (
    EXTRACT_INTERVAL_S,
    EXTRACT_TIMEOUT_S,
    MSG_CANNOT_TAME,
    MSG_EXTRACT_NOT_MAX,
    MSG_EXTRACT_NOT_MAX2,
    MSG_EXTRACT_OK,
    MSG_PET_SLOTS_FULL,
    MSG_TAME_FAILED,
    MSG_TAME_SUCCESS,
    MSG_TRAIN_UP,
    SUMMON_COOLDOWN_S,
    SUMMON_PAD_S,
    TAME_CAST_START_TIMEOUT_S,
    TAME_COOLDOWN_PAD_S,
    TAME_COOLDOWN_S,
    TAME_MAX_TRIES,
    TAME_RANGE_CELLS,
    TAME_RESULT_TIMEOUT_S,
    TAME_SEARCH_CELLS,
    TRAIN_ROUNDS,
    build_suctl_packet,
    default_config_path,
    load_config,
    save_config,
    TamerConfig,
)
from gi.repository import GLib
from gi.repository import Gtk

#: How often the tamer state machine runs.
TAMER_INTERVAL_MS = 50
#: The client's own Tame cast pace, used when the tamed skill is unknown.
TAME_TICK_PACE_S = 1.0


class TamerPageMixin:
    # ------------------------------------------------------------- UI

    def _build_tamer_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.set_margin_top(6)
        page.set_margin_start(8)
        page.set_margin_end(8)
        page.set_margin_bottom(6)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Pet Trainer")
        title.set_xalign(0)
        title.add_css_class("title-4")
        title.set_hexpand(True)
        top.append(title)
        start = Gtk.Button(label="Start")
        start.add_css_class("suggested-action")
        start.connect("clicked", lambda *_: self._tamer_start())
        top.append(start)
        stop = Gtk.Button(label="Stop")
        stop.connect("clicked", lambda *_: self._tamer_stop())
        top.append(stop)
        page.append(top)

        self.tamer_status = Gtk.Label(label="stopped")
        self.tamer_status.set_xalign(0)
        self.tamer_status.set_wrap(True)
        self.tamer_status.add_css_class("dim-label")
        page.append(self.tamer_status)

        self.tamer_info = Gtk.Label(label="pet slots: — · tamed: 0")
        self.tamer_info.set_xalign(0)
        self.tamer_info.set_wrap(True)
        page.append(self.tamer_info)

        # -- Taming ------------------------------------------------------
        tame_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.tamer_vnum_spin = Gtk.SpinButton.new_with_range(1, 1000000000, 1)
        self.tamer_vnum_spin.set_value(333)
        self.tamer_vnum_spin.set_width_chars(8)
        self.tamer_vnum_spin.set_tooltip_text(
            "Monster template to tame. Chicken = 333."
        )
        self.tamer_vnum_spin.connect(
            "value-changed", lambda *_: self._tamer_vnum_name_update()
        )
        self.tamer_vnum_name = Gtk.Label(label="Chicken")
        self.tamer_vnum_name.add_css_class("dim-label")
        tame_box.append(
            self._field_row(
                Gtk.Label(label="Monster vnum"),
                self.tamer_vnum_spin,
                self.tamer_vnum_name,
            )
        )
        self.tamer_slot_spin = Gtk.SpinButton.new_with_range(1, 9, 1)
        self.tamer_slot_spin.set_value(1)
        self.tamer_slot_spin.set_tooltip_text(
            "Skill-bar slot with Tame. Slot 0 is the card's basic attack."
        )
        tame_box.append(
            self._field_row(
                Gtk.Label(label="Tame slot"), self.tamer_slot_spin
            )
        )
        self.tamer_hp_spin = Gtk.SpinButton.new_with_range(5, 50, 5)
        self.tamer_hp_spin.set_value(25)
        self.tamer_hp_spin.set_tooltip_text(
            "Hit the monster until its HP is at most this, then Tame."
        )
        tame_box.append(
            self._field_row(
                Gtk.Label(label="Hit until HP ≤"),
                self.tamer_hp_spin,
                Gtk.Label(label="%"),
            )
        )
        page.append(
            self._frame(
                "Taming",
                tame_box,
                help_text=(
                    "Automates the Pet Trainer specialist card: find the "
                    "monster template (Chicken = 333) within 15 cells, walk "
                    "into range, hit it with the card's basic attack (slot 0) "
                    "until its HP is at or below the limit, then use Tame from "
                    "the chosen slot. Tame needs the monster below 50% HP and "
                    "a higher character level (1-star pets)."
                ),
            )
        )

        # -- End to end --------------------------------------------------
        auto_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.tamer_e2e_toggle = Gtk.ToggleButton(
            label="End to end (tame → train → extract)"
        )
        self.tamer_e2e_toggle.set_active(False)
        self._bind_toggle_color(self.tamer_e2e_toggle)
        auto_box.append(self.tamer_e2e_toggle)

        self.tamer_miniland_spin = Gtk.SpinButton.new_with_range(
            1, 1000000000, 1
        )
        self.tamer_miniland_spin.set_value(20001)
        self.tamer_miniland_spin.set_width_chars(8)
        auto_box.append(
            self._field_row(
                Gtk.Label(label="Miniland map"), self.tamer_miniland_spin
            )
        )

        self.tamer_portal_map_spin = Gtk.SpinButton.new_with_range(
            1, 1000000000, 1
        )
        self.tamer_portal_x_spin = Gtk.SpinButton.new_with_range(0, 32767, 1)
        self.tamer_portal_y_spin = Gtk.SpinButton.new_with_range(0, 32767, 1)
        portal_row = self._field_row(
            Gtk.Label(label="Portal"),
            self.tamer_portal_map_spin,
            self.tamer_portal_x_spin,
            self.tamer_portal_y_spin,
        )
        self.tamer_portal_map_spin.set_value(1)
        self.tamer_portal_x_spin.set_value(49)
        self.tamer_portal_y_spin.set_value(133)
        for spin in (
            self.tamer_portal_map_spin,
            self.tamer_portal_x_spin,
            self.tamer_portal_y_spin,
        ):
            spin.set_width_chars(5)
        self.tamer_portal_map_spin.set_tooltip_text(
            "Map with the Miniland entrance (live: map 1)."
        )
        self.tamer_portal_x_spin.set_tooltip_text(
            "Miniland entrance x on that map (live: 49)."
        )
        self.tamer_portal_y_spin.set_tooltip_text(
            "Miniland entrance y on that map (live: 133)."
        )
        auto_box.append(portal_row)

        self.tamer_summon_spin = Gtk.SpinButton.new_with_range(1, 9, 1)
        self.tamer_summon_spin.set_value(2)
        self.tamer_summon_spin.set_tooltip_text(
            "Skill-bar slot with Summon Weak Sparring Monster (1788)."
        )
        auto_box.append(
            self._field_row(
                Gtk.Label(label="Summon slot"), self.tamer_summon_spin
            )
        )
        self.tamer_sparring_spin = Gtk.SpinButton.new_with_range(
            1, 1000000000, 1
        )
        self.tamer_sparring_spin.set_value(1497)
        self.tamer_sparring_spin.set_width_chars(8)
        self.tamer_sparring_spin.set_tooltip_text(
            "The sparring monster vnum the summon creates (live: 1497)."
        )
        auto_box.append(
            self._field_row(
                Gtk.Label(label="Sparring vnum"),
                self.tamer_sparring_spin,
            )
        )
        self.tamer_extract_spin = Gtk.SpinButton.new_with_range(1, 9, 1)
        self.tamer_extract_spin.set_value(3)
        self.tamer_extract_spin.set_tooltip_text(
            "Skill-bar slot with Extract Essence (1789)."
        )
        auto_box.append(
            self._field_row(
                Gtk.Label(label="Extract slot"), self.tamer_extract_spin
            )
        )
        self.tamer_train_spin = Gtk.SpinButton.new_with_range(1, 120, 1)
        self.tamer_train_spin.set_value(8)
        self.tamer_train_spin.set_tooltip_text(
            "Minutes to train the pets on the sparring monster before "
            "extracting (live extraction was about 8 minutes)."
        )
        auto_box.append(
            self._field_row(
                Gtk.Label(label="Train minutes"),
                self.tamer_train_spin,
                Gtk.Label(label="min"),
            )
        )
        self.tamer_extract_toggle = Gtk.CheckButton(
            label="Extract essence after training (releases the pets)"
        )
        self.tamer_extract_toggle.set_active(True)
        auto_box.append(self.tamer_extract_toggle)
        page.append(
            self._frame(
                "End to end",
                auto_box,
                help_text=(
                    "With End to end on, Start fills the pet slots with Tame, "
                    "then enters the Miniland (walking to the portal on the "
                    "portal map), summons the sparring monster and sends every "
                    "tamed monster at it with suctl. After the training time "
                    "it extracts the essence from each pet (releasing it) and "
                    "starts taming again. Extraction needs the pet at the "
                    "maximum training level; a refused extraction trains "
                    "another round."
                ),
            )
        )

        hint = Gtk.Label(
            label=(
                "The training runs only in your Miniland; the bot walks to "
                "the portal and waits for the map change."
            )
        )
        hint.set_xalign(0)
        hint.set_wrap(True)
        hint.add_css_class("dim-label")
        page.append(hint)
        return page

    # ---------------------------------------------------------- settings

    def _tamer_widget_config(self) -> TamerConfig:
        def _int(spin, default: int) -> int:
            return int(spin.get_value()) if spin is not None else default

        return TamerConfig(
            tame_vnum=_int(self.tamer_vnum_spin, 333),
            tame_slot=max(1, min(9, _int(self.tamer_slot_spin, 1))),
            tame_hp_pct=(
                float(self.tamer_hp_spin.get_value())
                if self.tamer_hp_spin is not None
                else 25.0
            ),
            e2e=bool(
                self.tamer_e2e_toggle is not None
                and self.tamer_e2e_toggle.get_active()
            ),
            miniland_map=_int(self.tamer_miniland_spin, 20001),
            portal_map=_int(self.tamer_portal_map_spin, 1),
            portal_x=_int(self.tamer_portal_x_spin, 49),
            portal_y=_int(self.tamer_portal_y_spin, 133),
            summon_slot=max(1, min(9, _int(self.tamer_summon_spin, 2))),
            sparring_vnum=_int(self.tamer_sparring_spin, 1497),
            extract_slot=max(1, min(9, _int(self.tamer_extract_spin, 3))),
            train_seconds=max(1, _int(self.tamer_train_spin, 8)) * 60,
            extract=bool(
                self.tamer_extract_toggle is not None
                and self.tamer_extract_toggle.get_active()
            ),
        )

    def _tamer_apply_config(self, config: TamerConfig) -> None:
        if self.tamer_vnum_spin is not None:
            self.tamer_vnum_spin.set_value(config.tame_vnum)
        if self.tamer_slot_spin is not None:
            self.tamer_slot_spin.set_value(config.tame_slot)
        if self.tamer_hp_spin is not None:
            self.tamer_hp_spin.set_value(config.tame_hp_pct)
        if self.tamer_e2e_toggle is not None:
            self.tamer_e2e_toggle.set_active(config.e2e)
        if self.tamer_miniland_spin is not None:
            self.tamer_miniland_spin.set_value(config.miniland_map)
        if self.tamer_portal_map_spin is not None:
            self.tamer_portal_map_spin.set_value(config.portal_map)
        if self.tamer_portal_x_spin is not None:
            self.tamer_portal_x_spin.set_value(config.portal_x)
        if self.tamer_portal_y_spin is not None:
            self.tamer_portal_y_spin.set_value(config.portal_y)
        if self.tamer_summon_spin is not None:
            self.tamer_summon_spin.set_value(config.summon_slot)
        if self.tamer_sparring_spin is not None:
            self.tamer_sparring_spin.set_value(config.sparring_vnum)
        if self.tamer_extract_spin is not None:
            self.tamer_extract_spin.set_value(config.extract_slot)
        if self.tamer_train_spin is not None:
            self.tamer_train_spin.set_value(
                max(1, min(120, int(config.train_seconds / 60) or 1))
            )
        if self.tamer_extract_toggle is not None:
            self.tamer_extract_toggle.set_active(config.extract)
        self._tamer_vnum_name_update()

    def _tamer_config_path(self):
        winpid = (
            self.attached_client.winpid
            if self.attached_client is not None
            else None
        )
        return default_config_path(winpid)

    def _tamer_load_config(self) -> None:
        self._tamer_apply_config(load_config(self._tamer_config_path()))

    def _tamer_save_config(self) -> None:
        try:
            save_config(self._tamer_config_path(), self._tamer_widget_config())
        except OSError as exc:
            self._log_line(f"tamer: could not save the settings: {exc}")

    def _tamer_vnum_name_update(self) -> None:
        if self.tamer_vnum_spin is None:
            return
        vnum = int(self.tamer_vnum_spin.get_value())
        name = self.monster_names.get(vnum) if self.monster_names else None
        if self.tamer_vnum_name is not None:
            self.tamer_vnum_name.set_text(name or "unknown vnum")

    # ------------------------------------------------------------ state

    def _tamer_reset_for_attach(self, winpid: int) -> None:
        """A new client is a new character: stop and load its own settings."""
        self._tamer_stop(quiet=True)
        self._tamer_reset_run()
        self._tamer_apply_config(load_config(default_config_path(winpid)))

    def _tamer_reset_run(self) -> None:
        self.tamer_phase = "tame"
        self.tamer_target_id = None
        self.tamer_cast_target = None
        self.tamer_cast_started = False
        self.tamer_cast_seq = 0
        self.tamer_seen_message = self.engine.world.message_seq
        self.tamer_attempts = 0
        self.tamer_hit_at = 0.0
        self.tamer_step_at = 0.0
        self.tamer_warn_at = 0.0
        self.tamer_ready_at = 0.0
        self.tamer_tamed = 0
        self.tamer_extracted = 0
        self.tamer_train_until = 0.0
        self.tamer_summon_at = 0.0
        self.tamer_suctl_at = 0.0
        self.tamer_extract_at = 0.0
        self.tamer_extract_target = None
        self.tamer_extract_cast_at = 0.0
        self.tamer_train_round = 0
        self.tamer_portal_step_at = 0.0

    def _tamer_start(self) -> None:
        if self.tamer_running:
            return
        if self.client is None or not self.client.connected:
            self._tamer_set_status("not connected to a game client")
            return
        if self._player_cell() is None:
            self._request_position()
            self._tamer_set_status("locating the character; try again")
            return
        config = self._tamer_widget_config()
        self._tamer_config = config
        self._tamer_save_config()
        self._tamer_reset_run()
        full = len(self.engine.world.pets) >= 8
        if config.e2e and full:
            self.tamer_phase = "train"
        self.tamer_running = True
        if self.tamer_timer is None:
            self.tamer_timer = GLib.timeout_add(
                TAMER_INTERVAL_MS, self._tamer_tick
            )
        self._tamer_set_status(
            "started · "
            + (
                "training in the Miniland"
                if self.tamer_phase == "train"
                else f"taming {self.monster_names.get(config.tame_vnum, config.tame_vnum)}"
            )
        )

    def _tamer_stop(self, quiet: bool = False) -> None:
        self.tamer_running = False
        if self.tamer_timer is not None:
            GLib.source_remove(self.tamer_timer)
            self.tamer_timer = None
        self.pending_target = None
        if not quiet:
            self._tamer_set_status("stopped")

    def _tamer_set_status(self, text: str) -> None:
        if self.tamer_status is not None:
            self.tamer_status.set_text(text)
        self._log_line(f"tamer: {text}")

    # ------------------------------------------------------------ helpers

    def _tamer_skill_vnum(self, slot: int) -> int:
        world = self.engine.world
        vnum = self.log_skill_slots.get(slot)
        if vnum is None:
            vnum = world.skill_learned.get(slot)
        return int(vnum) if vnum else 0

    def _tamer_skill_info(self, slot: int):
        vnum = self._tamer_skill_vnum(slot)
        return self.spells.get(vnum) if vnum else None

    def _tamer_cooldown(self, slot: int, default: float) -> float:
        info = self._tamer_skill_info(slot)
        if info is not None and info.cooldown > 0:
            return float(info.cooldown)
        return default

    def _tamer_range(self, slot: int) -> int:
        info = self._tamer_skill_info(slot)
        if info is not None and info.range > 0:
            return int(info.range)
        return TAME_RANGE_CELLS

    def _tamer_hp_percent(self, entity) -> float | None:
        if entity.hp < 0 or entity.hp_max <= 0:
            return None
        return max(0.0, min(100.0, 100.0 * entity.hp / entity.hp_max))

    def _tamer_label(self, vnum: int, entity) -> str:
        name = self.monster_names.get(vnum, f"vnum {vnum}")
        return f"{name} {entity.entity_id}"

    def _tamer_chicken_pets(self) -> list[tuple[int, int]]:
        """(slot, entityId) of the tamed monsters currently in the panel."""
        world = self.engine.world
        config = getattr(self, "_tamer_config", None) or TamerConfig()
        vnum = config.tame_vnum
        return [
            (slot, entity_id)
            for slot, entity_id in world.pet_entities.items()
            if world.pets.get(slot) == vnum
        ]

    def _tamer_update_info(self, detail: str = "") -> None:
        if self.tamer_info is None:
            return
        world = self.engine.world
        chickens = len(self._tamer_chicken_pets())
        text = (
            f"pet slots: {len(world.pets) or '—'} · chickens: {chickens} · "
            f"tamed: {self.tamer_tamed} · extracted: {self.tamer_extracted}"
        )
        if detail:
            text = f"{text} · {detail}"
        self.tamer_info.set_text(text)

    def _tamer_wait_cast(self, world, now: float, target_id: int,
                         cast_seq: int) -> bool | None:
        """Wait for a started cast: True = started, False = refused, None = wait."""
        own = next(
            (
                skill
                for seq, caster_type, caster_id, target, skill
                in world.cast_log
                if seq > cast_seq
                and caster_type == 1
                and caster_id == self._char_id()
                and target == target_id
            ),
            None,
        )
        if own is not None:
            return True
        return None

    # ------------------------------------------------------------ phases

    def _tamer_tick(self) -> bool:
        if not self.tamer_running:
            self.tamer_timer = None
            return False
        if self.client is None or not self.client.connected:
            self._tamer_set_status("not connected; stopped")
            self._tamer_stop(quiet=True)
            return False
        now = time.monotonic()
        world = self.engine.world
        if world.dead:
            self._tamer_set_status("dead; stopped (revive, then Start)")
            self._tamer_stop(quiet=True)
            return False
        char_id = self._char_id()
        if (
            world.resting is True
            and char_id is not None
            and now >= self.stand_sent_until
        ):
            self._send(f"rest 1 1 {char_id}")
            self.stand_sent_until = now + REST_RETRY_S
            self._tamer_set_status("standing up")
            return True
        if self.tamer_phase == "train":
            return self._tamer_train_tick(now, world)
        if self.tamer_phase == "extract":
            return self._tamer_extract_tick(now, world)
        return self._tamer_tame_tick(now, world)

    # -- tame -----------------------------------------------------------

    def _tamer_tame_tick(self, now: float, world) -> bool:
        config = self._tamer_config
        slot = config.tame_slot
        hp_limit = config.tame_hp_pct
        self._tamer_update_info()
        if self.tamer_cast_target is not None:
            if not self.tamer_cast_started:
                started = self._tamer_wait_cast(
                    world, now, self.tamer_cast_target, self.tamer_cast_seq
                )
                if started:
                    self.tamer_cast_started = True
                    self.tamer_attempts += 1
                elif now - self.tamer_cast_at > TAME_CAST_START_TIMEOUT_S:
                    self.tamer_cast_target = None
                    self.tamer_ready_at = now + 1.0
                    self._tamer_set_status(
                        "the client did not start the Tame cast; retrying"
                    )
                    return True
                else:
                    return True
            if (
                world.message_seq != self.tamer_seen_message
                and now - self.tamer_cast_at <= TAME_RESULT_TIMEOUT_S
            ):
                self.tamer_seen_message = world.message_seq
                if self._tamer_tame_result(world, world.last_message_id):
                    return True
            if now - self.tamer_cast_at > TAME_RESULT_TIMEOUT_S:
                self.tamer_cast_target = None
                self.tamer_cast_started = False
                self.tamer_target_id = None
                if self.tamer_attempts >= TAME_MAX_TRIES:
                    self._tamer_set_status(
                        "Tame got no answer — check the Tame slot and the "
                        "Pet Trainer card; stopped"
                    )
                    self._tamer_stop(quiet=True)
                    return False
                self._tamer_set_status("Tame got no answer; retrying")
            return True
        if self.pending_target is not None:
            position = self._player_cell()
            confirmed = world.position == self.pending_target or (
                self.last_move_time >= self.pending_since
                and self.last_move is not None
                and self.last_move[0] == 1
                and (self.last_move[2], self.last_move[3])
                == self.pending_target
            )
            if (
                confirmed
                or position == self.pending_target
                or now > self.pending_until
            ):
                self.pending_target = None
            return True
        target = self._tamer_target(world, config.tame_vnum)
        if target is None:
            self.tamer_target_id = None
            self._tamer_update_info("waiting for a target")
            if now >= self.tamer_warn_at:
                self.tamer_warn_at = now + 15.0
                name = self.monster_names.get(
                    config.tame_vnum, f"vnum {config.tame_vnum}"
                )
                self._tamer_set_status(
                    f"no {name} within {TAME_SEARCH_CELLS} cells — waiting"
                )
            return True
        if self.tamer_target_id != target.entity_id:
            self.tamer_target_id = target.entity_id
            self._tamer_set_status(
                f"taming target {self._tamer_label(config.tame_vnum, target)}"
            )
        position = self._player_cell()
        if position is None:
            self._request_position()
            return True
        distance = max(
            abs(target.x - position[0]), abs(target.y - position[1])
        )
        hp = self._tamer_hp_percent(target)
        label = self._tamer_label(config.tame_vnum, target)
        if distance > self._tamer_range(slot):
            if now >= self.tamer_step_at:
                self.tamer_step_at = now + 0.5
                self._tamer_walk(target, slot, now)
            self._tamer_update_info(f"walking to {label}")
            return True
        if self.tamer_ready_at > now:
            remaining = max(0, int(self.tamer_ready_at - now + 0.999))
            if hp is None or hp > hp_limit:
                self._tamer_hit(target, now)
                self._tamer_update_info(
                    f"hitting {label} · Tame in {remaining} s"
                )
            else:
                self._tamer_update_info(
                    f"{label} at {hp:.0f}% HP · Tame in {remaining} s"
                )
            return True
        if hp is None or hp > hp_limit:
            self._tamer_hit(target, now)
            self._tamer_update_info(
                f"hitting {label} ({hp:.0f}% HP)"
                if hp is not None
                else f"hitting {label}"
            )
            return True
        if not self._ensure_target(target.entity_id, now):
            return True
        if not self._send(
            self._skill_packet(target.entity_id, skill=slot)
        ):
            self._tamer_stop(quiet=True)
            return False
        self.tamer_cast_at = now
        self.tamer_cast_target = target.entity_id
        self.tamer_cast_started = False
        self.tamer_cast_seq = world.cast_seq
        self.tamer_seen_message = world.message_seq
        cooldown = self._tamer_cooldown(slot, TAME_COOLDOWN_S)
        self.tamer_ready_at = now + cooldown + TAME_COOLDOWN_PAD_S
        self._tamer_set_status(f"taming {label} (slot {slot})")
        return True

    def _tamer_tame_result(self, world, message_id: int) -> bool:
        if message_id not in (
            MSG_TAME_SUCCESS,
            MSG_PET_SLOTS_FULL,
            MSG_CANNOT_TAME,
            MSG_TAME_FAILED,
        ):
            return False
        target = self.tamer_cast_target
        self.tamer_cast_target = None
        self.tamer_cast_started = False
        if target is not None:
            self.target_blocked[target] = time.monotonic() + 3.0
        if message_id == MSG_TAME_SUCCESS:
            self.tamer_tamed += 1
            self.tamer_attempts = 0
            self.tamer_target_id = None
            self._tamer_update_info()
            self._tamer_set_status("tamed a pet")
            return True
        if message_id == MSG_PET_SLOTS_FULL:
            if self._tamer_config.e2e:
                self.tamer_phase = "train"
                self.tamer_train_until = 0.0
                self.tamer_train_round = 0
                self._tamer_set_status(
                    "pet slots full — starting the Miniland training"
                )
            else:
                self._tamer_stop(quiet=True)
                self._tamer_set_status(
                    "pet slots full — release or extract a pet, then Start"
                )
            return True
        if message_id == MSG_CANNOT_TAME:
            self._tamer_stop(quiet=True)
            self._tamer_set_status(
                "this skill cannot tame that monster — check the vnum and slot"
            )
            return True
        if message_id == MSG_TAME_FAILED:
            if self.tamer_attempts >= TAME_MAX_TRIES:
                self._tamer_stop(quiet=True)
                self._tamer_set_status("taming failed three times; stopped")
            else:
                self.tamer_target_id = None
                self._tamer_set_status("taming failed; trying another")
        return True

    def _tamer_target(self, world, vnum: int):
        position = self._player_cell()
        if position is None:
            return None
        px, py = position
        current = (
            world.entities.get(self.tamer_target_id)
            if self.tamer_target_id is not None
            else None
        )
        if (
            current is not None
            and current.kind == 3
            and current.vnum == vnum
            and not self._target_blocked(current.entity_id)
            and max(abs(current.x - px), abs(current.y - py))
            <= TAME_SEARCH_CELLS
        ):
            return current
        best = None
        best_distance = TAME_SEARCH_CELLS + 1
        for entity in world.entities.values():
            if entity.kind != 3 or entity.vnum != vnum:
                continue
            if self._target_blocked(entity.entity_id):
                continue
            distance = max(abs(entity.x - px), abs(entity.y - py))
            if distance < best_distance or (
                distance == best_distance
                and best is not None
                and entity.entity_id < best.entity_id
            ):
                best, best_distance = entity, distance
        return best

    def _tamer_hit(self, target, now: float) -> None:
        if now < self.tamer_hit_at:
            return
        if not self._ensure_target(target.entity_id, now):
            return
        if not self._send(self._skill_packet(target.entity_id, skill=0)):
            return
        info = self.spells.get(self._basic_skill_vnum() or 0)
        pace = 1.2
        if info is not None:
            pace = max(0.8, info.cooldown, info.cast_time)
        self.tamer_hit_at = now + pace

    def _tamer_walk(self, target, slot: int, now: float) -> bool:
        if self.map_grid is not None:
            return self._chase_step(
                target, float(self._tamer_range(slot)), now
            )
        position = self._player_cell()
        if position is None:
            return False
        dx = max(-3, min(3, target.x - position[0]))
        dy = max(-3, min(3, target.y - position[1]))
        if dx == 0 and dy == 0:
            return False
        goal = (position[0] + dx, position[1] + dy)
        if not self._ensure_target(target.entity_id, now):
            return False
        if not self._client_move(goal[0], goal[1]):
            return False
        self.pending_target = goal
        self.pending_is_path = False
        self.pending_loot_id = None
        self.pending_since = now
        self.pending_distance = max(abs(dx), abs(dy))
        self.pending_until = now + max(3.0, self.pending_distance * 1.5)
        return True

    # -- Miniland training ---------------------------------------------

    def _tamer_train_tick(self, now: float, world) -> bool:
        config = self._tamer_config
        self._tamer_update_info()
        if world.map_id != config.miniland_map:
            return self._tamer_portal_tick(now, world)
        if self.tamer_train_until == 0.0:
            self.tamer_train_until = now + config.train_seconds
            self._tamer_set_status(
                "in the Miniland — summoning the sparring monster"
            )
        monster = self._tamer_sparring(world, config.sparring_vnum)
        if monster is None:
            if now >= self.tamer_summon_at:
                self.tamer_summon_at = (
                    now + SUMMON_COOLDOWN_S + SUMMON_PAD_S
                )
                if self._send(
                    self._skill_packet(
                        self._char_id() or 0,
                        skill=config.summon_slot,
                        target_type=1,
                    )
                ):
                    self._tamer_set_status(
                        f"summoning the sparring monster (slot "
                        f"{config.summon_slot})"
                    )
            else:
                remaining = max(
                    0, int(self.tamer_summon_at - now + 0.999)
                )
                self._tamer_update_info(
                    f"waiting for the summon ({remaining} s)"
                )
            return True
        pets = self._tamer_chicken_pets()
        if not pets:
            self._tamer_stop(quiet=True)
            self._tamer_set_status(
                "no tamed monsters in the pet panel — taming first"
            )
            return False
        if now >= self.tamer_suctl_at:
            ids = [entity_id for _slot, entity_id in pets]
            packet = build_suctl_packet(
                world.map_id or 0, ids, monster.entity_id
            )
            if self._send(packet):
                self.tamer_suctl_at = now + 1.5
                self._tamer_update_info(
                    f"training {len(ids)} pet(s) on {monster.entity_id}"
                )
        remaining = max(0, int(self.tamer_train_until - now + 0.999))
        if now >= self.tamer_train_until:
            if config.extract:
                self.tamer_phase = "extract"
                self.tamer_extract_at = 0.0
                self._tamer_set_status(
                    "training done — extracting the pet essence"
                )
            else:
                self._tamer_stop(quiet=True)
                self._tamer_set_status("training done; stopped (extract is off)")
                return False
        else:
            self._tamer_update_info(
                f"training · {remaining // 60}:{remaining % 60:02d} left"
            )
        return True

    def _tamer_portal_tick(self, now: float, world) -> bool:
        """Walk to the Miniland portal and wait for the map change."""
        config = self._tamer_config
        if world.map_id != config.portal_map:
            self._tamer_set_status(
                f"waiting in the Miniland (map {config.miniland_map}) — "
                f"go to map {config.portal_map} near "
                f"({config.portal_x},{config.portal_y})"
            )
            return True
        position = self._player_cell()
        if position is None:
            self._request_position()
            return True
        distance = max(
            abs(position[0] - config.portal_x),
            abs(position[1] - config.portal_y),
        )
        if distance <= 1:
            if now >= self.tamer_portal_step_at:
                self.tamer_portal_step_at = now + 2.0
                self._client_move(config.portal_x, config.portal_y)
            self._tamer_set_status("stepping on the Miniland portal")
            return True
        if now >= self.tamer_portal_step_at:
            self.tamer_portal_step_at = now + 1.0
            dx = max(-3, min(3, config.portal_x - position[0]))
            dy = max(-3, min(3, config.portal_y - position[1]))
            goal = (position[0] + dx, position[1] + dy)
            if self._client_move(goal[0], goal[1]):
                self.pending_target = goal
                self.pending_is_path = False
                self.pending_loot_id = None
                self.pending_since = now
                self.pending_distance = max(abs(dx), abs(dy))
                self.pending_until = now + max(3.0, self.pending_distance * 1.5)
        self._tamer_set_status(
            f"walking to the Miniland portal "
            f"({config.portal_x},{config.portal_y})"
        )
        return True

    @staticmethod
    def _tamer_sparring(world, vnum: int):
        for entity in world.entities.values():
            if entity.kind == 3 and entity.vnum == vnum:
                return entity
        return None

    # -- extraction -----------------------------------------------------

    def _tamer_extract_tick(self, now: float, world) -> bool:
        config = self._tamer_config
        pets = self._tamer_chicken_pets()
        self._tamer_update_info()
        # Handle the answer of a cast first: the last pet's `p_clear` empties
        # the panel before the result would otherwise be counted.
        if self.tamer_extract_target is not None:
            if (
                world.message_seq != self.tamer_seen_message
                and now - self.tamer_extract_cast_at <= EXTRACT_TIMEOUT_S
            ):
                self.tamer_seen_message = world.message_seq
                message_id = world.last_message_id
                if message_id == MSG_EXTRACT_OK:
                    self.tamer_extract_target = None
                    self.tamer_extracted += 1
                    self._tamer_set_status("essence extracted; next pet")
                    return True
                if message_id in (MSG_EXTRACT_NOT_MAX, MSG_EXTRACT_NOT_MAX2):
                    self.tamer_extract_target = None
                    self.tamer_train_round += 1
                    if self.tamer_train_round > TRAIN_ROUNDS:
                        self._tamer_stop(quiet=True)
                        self._tamer_set_status(
                            "the pet is not at the maximum training level; "
                            "train longer (Train minutes)"
                        )
                        return False
                    self.tamer_phase = "train"
                    self.tamer_train_until = 0.0
                    self._tamer_set_status(
                        f"pet not maxed — training another round "
                        f"({self.tamer_train_round}/{TRAIN_ROUNDS})"
                    )
                    return True
            if now - self.tamer_extract_cast_at > EXTRACT_TIMEOUT_S:
                self.tamer_extract_target = None
                self._tamer_set_status(
                    "Extract got no answer — check the Extract slot; trying "
                    "the next pet"
                )
            return True
        if not pets:
            self.tamer_phase = "tame"
            self._tamer_set_status("all pets extracted — taming again")
            return True
        if now >= self.tamer_extract_at:
            pet_id = pets[0][1]
            if not self._send(
                self._skill_packet(
                    pet_id, skill=config.extract_slot, target_type=2
                )
            ):
                self._tamer_stop(quiet=True)
                return False
            self.tamer_extract_target = pet_id
            self.tamer_extract_cast_at = now
            self.tamer_seen_message = world.message_seq
            self.tamer_extract_at = now + EXTRACT_INTERVAL_S
            self._tamer_set_status(
                f"extracting pet {pet_id} (slot {config.extract_slot})"
            )
        return True
