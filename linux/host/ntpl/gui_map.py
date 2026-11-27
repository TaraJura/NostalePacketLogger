"""The Map/Bot page, targets, spells, map drawing, travel, speed and cast controls.

Extracted from gui.py; every method still runs on the Application
instance, so it uses self state exactly like the core methods.
"""


from .botconfig import BotConfig
from .botconfig import COMPARATORS
from .botconfig import ConfigError
from .botconfig import MONSTER_MODES
from .botconfig import SPELL_TARGETS
from .botconfig import SUBJECTS
from .botconfig import SpellRule
from .botconfig import load_config
from .botconfig import save_config
from .client import ConnectionLost
from .engine import Entity
from .engine import GroundItem
from .guicommon import ATTACK_SEARCH_CELLS
from .guicommon import AUTO_MAP_POINTS
from .guicommon import CAST_ASSIST_MAX_S
from .guicommon import CAST_ASSIST_TTL_S
from .guicommon import CAST_PENDING_MAX
from .guicommon import DEFAULT_SKILL_TEMPLATE
from .guicommon import LOOT_APPROACH_CELLS
from .guicommon import POS_COMMAND
from .guicommon import REST_RETRY_S
from .guicommon import RETALIATE_WINDOW_S
from .guicommon import ROUTE_ARRIVE_CELLS
from .guicommon import SPEED_BOOST_MAX
from .guicommon import TIMESPACE_ICON
from .guicommon import TRAVEL_HANDOFF_CELLS
from .guicommon import TRAVEL_INTERVAL_MS
from .guicommon import TRAVEL_LEG_TIMEOUT_S
from .guicommon import TRAVEL_MIN_LEG_S
from .guicommon import TRAVEL_POS_POLL_S
from .mapdata import MapDataError
from .mapdata import add_portal
from .mapdata import astar
from .mapdata import choose_map_for_positions
from .mapdata import is_time_space_map
from .mapdata import load_nstc
from .mapdata import normalize_portals
from .mapdata import save_portals
from .mapdata import set_gate
from .mapdata import terrain_rgb
from .monsternames import load_monster_names
from .monsternames import load_skill_descriptions
from .protocol import Kind
from .protocol import Packet
from .spawnlog import load_gates
from .spawnlog import load_last_position
from .spawnlog import load_player_metadata
from .spawnlog import load_player_spawns
from .spawnlog import load_portals as load_log_portals
from .spawnlog import load_skill_list
from .spawnlog import load_skill_slots
from .spawnlog import load_spawns
from .spawnlog import load_timespaces
from .spells import SpellInfo
from .spells import load_spells
from gi.repository import GLib
from gi.repository import Gtk
from gi.repository import Pango
import cairo
import math
import os
import threading
import time

class MapPageMixin:
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
        top.append(
            self._help_button(
                "Map",
                "The client's own map with live players, monsters, NPCs, "
                "drops, portals and time-space stones. Hover for names. A "
                "click walks there; with Draw path on, clicks build a route "
                "instead. The legend below the map is the colour key.",
            )
        )
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
            '<span foreground="#d933f2">◉</span> portal   '
            '<span foreground="#ffb300">◉</span> time-space   '
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

        # Character: the name above live HP/MP bars and the position.
        char_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.char_name = Gtk.Label()
        self.char_name.set_xalign(0)
        self.char_name.set_ellipsize(Pango.EllipsizeMode.END)
        self.char_name.set_tooltip_text(
            "The attached character's name, from its spawn or party list."
        )
        char_box.append(self.char_name)
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
        sidebar.append(
            self._frame(
                "Character",
                char_box,
                help_text=(
                    "Live HP/MP, position and char id. The red banner appears "
                    "while the bot is recovering and says why combat is "
                    "paused."
                ),
            )
        )

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
        self.draw_path_toggle = Gtk.ToggleButton(label="Draw path")
        self.draw_path_toggle.set_active(False)
        self.draw_path_toggle.set_tooltip_text(
            "First click sets the route start; later clicks add waypoints."
        )
        self._bind_toggle_color(self.draw_path_toggle)
        path_box.append(self.draw_path_toggle)
        self.block_portal_toggle = Gtk.ToggleButton(label="Block portals")
        # Off by default: the character must be able to use portals normally
        # unless the user opts in for farming.
        self.block_portal_toggle.set_active(False)
        self.block_portal_toggle.set_tooltip_text(
            "Keep paths and MOVE off portal tiles while farming."
        )
        self._bind_toggle_color(self.block_portal_toggle)
        self.block_portal_toggle.connect(
            "toggled", lambda *_: self._on_block_portals_toggled()
        )
        path_box.append(self.block_portal_toggle)
        sidebar.append(
            self._frame(
                "Path",
                path_box,
                help_text=(
                    "Start follows the drawn route from the nearest leg and "
                    "repeats it while Loop is on; Stop pauses, Clear path "
                    "removes it. Draw path: the first map click sets the "
                    "route start, later clicks add waypoints. Off, a map "
                    "click walks there once. Block portals keeps A* and MOVE "
                    "off portal tiles so farming cannot change maps."
                ),
            )
        )

        # Follow a named player (a mule that trails the character you play).
        follow_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.follow_player_toggle = Gtk.ToggleButton(label="Follow player")
        self.follow_player_toggle.set_active(False)
        self.follow_player_toggle.set_tooltip_text(
            "Trail a player by name or char id."
        )
        self._bind_toggle_color(self.follow_player_toggle)
        self.follow_player_entry = Gtk.Entry()
        self.follow_player_entry.set_placeholder_text("player name or char id")
        self.follow_player_entry.set_max_length(40)
        self.follow_attack_check = Gtk.CheckButton(
            label="Attack the same target as the player"
        )
        self.follow_attack_check.set_active(False)
        self.follow_attack_check.set_tooltip_text(
            "Also attack the monster that player is fighting."
        )
        self.follow_mirror_check = Gtk.CheckButton(
            label="Mirror movement & spells"
        )
        self.follow_mirror_check.set_active(False)
        self.follow_mirror_check.set_tooltip_text(
            "Replay the followed player's walk and spells."
        )
        follow_box.append(
            self._field_row(self.follow_player_toggle, self.follow_player_entry)
        )
        follow_box.append(self.follow_attack_check)
        follow_box.append(self.follow_mirror_check)
        sidebar.append(
            self._frame(
                "Follow",
                follow_box,
                help_text=(
                    "Type a player name or char id and press Start: the "
                    "character stands and walks toward them, ignoring normal "
                    "combat, recovery and the drawn path. Pickup still "
                    "collects drops near the player. Attack the same target "
                    "also attacks the monster that player is fighting. Mirror "
                    "movement & spells replays their walk and casts on the "
                    "shared mirror bus, across separate GUI processes."
                ),
            )
        )

        # Combat.
        combat_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.auto_attack_toggle = Gtk.ToggleButton(label="Auto-attack")
        self.retaliate_toggle = Gtk.ToggleButton(label="Retaliate")
        self.retaliate_toggle.set_active(True)
        self.retaliate_toggle.set_tooltip_text(
            "Prioritise the monster that is currently hitting you"
        )
        for toggle in (
            self.auto_attack_toggle,
            self.retaliate_toggle,
        ):
            self._bind_toggle_color(toggle)
        combat_box.append(
            self._field_row(
                self.auto_attack_toggle,
                self.retaliate_toggle,
            )
        )
        self.combat_info = Gtk.Label(label="reading Skill.dat…")
        self.combat_info.set_xalign(0)
        self.combat_info.set_wrap(True)
        self.combat_info.add_css_class("dim-label")
        self.combat_info.set_tooltip_text(
            "Range, cast, cooldown and MP from Skill.dat."
        )
        combat_box.append(self.combat_info)
        sidebar.append(
            self._frame(
                "Combat",
                combat_box,
                help_text=(
                    "Auto-attack fights monsters in range with the equipped "
                    "SP card's basic attack (slot 0). Retaliate puts the "
                    "monster that is hitting you first. The line below is the "
                    "skill's range, cast time, cooldown and MP from Skill.dat."
                ),
            )
        )

        # Pet tamer: the Pet Trainer card's Whip + Tame loop.
        tame_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.tame_toggle = Gtk.ToggleButton(label="Tame monsters")
        self.tame_toggle.set_tooltip_text(
            "Bot mode: hit the monster down to the HP limit, then Tame it."
        )
        self._bind_toggle_color(self.tame_toggle)
        tame_box.append(self.tame_toggle)
        self.tame_vnum_spin = Gtk.SpinButton.new_with_range(1, 1000000000, 1)
        self.tame_vnum_spin.set_value(333)
        self.tame_vnum_spin.set_width_chars(8)
        self.tame_vnum_spin.set_tooltip_text(
            "Monster template to tame. Chicken = 333."
        )
        tame_box.append(
            self._field_row(
                Gtk.Label(label="Monster vnum"), self.tame_vnum_spin
            )
        )
        self.tame_slot_spin = Gtk.SpinButton.new_with_range(1, 9, 1)
        self.tame_slot_spin.set_value(1)
        self.tame_slot_spin.set_tooltip_text(
            "Skill-bar slot with Tame. Slot 0 is the card's basic attack."
        )
        tame_box.append(
            self._field_row(
                Gtk.Label(label="Tame slot"), self.tame_slot_spin
            )
        )
        self.tame_hp_spin = Gtk.SpinButton.new_with_range(5, 50, 5)
        self.tame_hp_spin.set_value(25)
        self.tame_hp_spin.set_tooltip_text(
            "Hit the monster until its HP is at most this, then Tame."
        )
        tame_box.append(
            self._field_row(
                Gtk.Label(label="Hit until HP ≤"),
                self.tame_hp_spin,
                Gtk.Label(label="%"),
            )
        )
        self.tame_info = Gtk.Label(label="pet slots: — · tamed: 0")
        self.tame_info.set_xalign(0)
        self.tame_info.set_wrap(True)
        self.tame_info.set_max_width_chars(38)
        self.tame_info.add_css_class("dim-label")
        tame_box.append(self.tame_info)
        sidebar.append(
            self._frame(
                "Pet tamer",
                tame_box,
                help_text=(
                    "Automates the Pet Trainer specialist card: the bot finds "
                    "the monster template (Chicken = 333) within 15 cells, "
                    "walks into range, hits it with the card's basic attack "
                    "(slot 0) until its HP is at or below the limit, then uses "
                    "Tame from the chosen skill-bar slot. It keeps going until "
                    "Stop, the server's max pet slots message, or three failed "
                    "tames. Equip the Pet Trainer card first. Taming is normal "
                    "gameplay, but automation violates the game rules."
                ),
            )
        )

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
            "Also attack monsters whose vnum is unknown."
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
        sidebar.append(
            self._frame(
                "Targets",
                target_box,
                help_text=(
                    "Restrict auto-attack and Retaliate to monster templates "
                    "(vnums). Only selected and All except selected use the "
                    "list below. Unknown vnum covers monsters that spawned "
                    "before the logger attached, so their vnum is unknown."
                ),
            )
        )

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
        sidebar.append(
            self._frame(
                "Spells",
                spell_box,
                help_text=(
                    "Requires Auto-attack. Rules execute top to bottom; each "
                    "fires when its condition is true and its cooldown is "
                    "over. Use Always for a rotation. When no rule fires, the "
                    "basic attack is used."
                ),
            )
        )

        # Loot.
        loot_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.pickup_toggle = Gtk.ToggleButton(label="Pickup")
        self.pickup_toggle.set_active(True)
        self.pickup_toggle.set_tooltip_text(
            "Pick up your own drops within 10 cells"
        )
        self._bind_toggle_color(self.pickup_toggle)
        loot_box.append(self._field_row(self.pickup_toggle))
        sidebar.append(
            self._frame(
                "Loot",
                loot_box,
                help_text=(
                    "Pick up your own and free drops within 10 cells, also "
                    "during combat. Another player's protected drop is "
                    "skipped."
                ),
            )
        )

        # Recovery.
        recover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.recover_toggle = Gtk.ToggleButton(label="Auto-recover HP")
        self.recover_toggle.set_active(True)
        self.recover_toggle.set_tooltip_text(
            "Below the HP threshold, sit and wait for full HP"
        )
        self._bind_toggle_color(self.recover_toggle)
        self.low_hp_pct = Gtk.SpinButton.new_with_range(5, 90, 5)
        self.low_hp_pct.set_value(70)
        recover_box.append(
            self._field_row(
                self.recover_toggle,
                Gtk.Label(label="Below HP %"),
                self.low_hp_pct,
            )
        )
        self.recover_mp_toggle = Gtk.ToggleButton(label="Auto-recover MP")
        self.recover_mp_toggle.set_active(True)
        self.recover_mp_toggle.set_tooltip_text(
            "Below the MP threshold, sit and wait for full MP"
        )
        self._bind_toggle_color(self.recover_mp_toggle)
        self.low_mp_pct = Gtk.SpinButton.new_with_range(5, 90, 5)
        self.low_mp_pct.set_value(70)
        recover_box.append(
            self._field_row(
                self.recover_mp_toggle,
                Gtk.Label(label="Below MP %"),
                self.low_mp_pct,
            )
        )
        self.revive_toggle = Gtk.ToggleButton(label="Revive on death")
        self.revive_toggle.set_active(False)
        self.revive_toggle.set_tooltip_text(
            "Accept the revival dialog (10 Seeds of Power) and resume."
        )
        self._bind_toggle_color(self.revive_toggle)
        recover_box.append(self.revive_toggle)
        sidebar.append(
            self._frame(
                "Recovery",
                recover_box,
                help_text=(
                    "Sit and regenerate when HP or MP falls below the "
                    "threshold; the current kill is finished first. Revive on "
                    "death accepts the in-place revival dialog (Continue "
                    "here? You'll need 10 Seeds of Power) and resumes. Off "
                    "stops the bot at death."
                ),
            )
        )

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
        sidebar.append(
            self._frame(
                "Configuration",
                config_box,
                help_text=(
                    "Save or load the whole bot setup as JSON: route, "
                    "targets, spells, follow, recovery and toggles. The line "
                    "below is the last save or load result."
                ),
            )
        )

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

    def _build_malicious_page(self) -> Gtk.Widget:
        """Client-side tricks that apply immediately and need no bot run."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_margin_top(18)
        page.set_margin_bottom(18)
        page.set_margin_start(18)
        page.set_margin_end(18)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        heading = Gtk.Label()
        heading.set_markup("<b>Malicious</b>")
        heading.set_xalign(0)
        header.append(heading)
        header.append(
            self._help_button(
                "Malicious",
                "Client-side tweaks that need no bot. Speed + raises the "
                "local player's walking speed by that many flat points "
                "(12 + 3 = 15) and applies immediately, in manual play too. "
                "Scroll the spin or type any value: "
                "the DLL raises the client's own 59 cap, extends its speed "
                "table and scales the top entry past the speed byte, so "
                "there is no small cap (up to 2,000,000,000). The DLL keeps "
                "outgoing walk packets at the server's normal speed, keeps "
                "the client faster, and turns the value down or off if the "
                "server corrects the player. Saved as speed_boost. "
                "Cast speed works without the bot: press a skill once and it "
                "repeats automatically every time the server says that "
                "skill's cooldown ended (`sr`), so your character keeps "
                "casting at the server's fastest rate; press another skill "
                "to switch, or turn the switch off. With the bot running its "
                "spell rules send the next eligible spell immediately too, "
                "and casts overlap — this server accepts casts sent during "
                "each other. The client's skill-bar cooldowns stay visible. "
                "Each spell still lands after the server's own cast time, "
                "which no client hack can shorten. Saved as cast_boost. "
                "These tweaks violate the game rules and are high ban risk.",
            )
        )
        page.append(header)

        # Speed +: the DLL adds this flat value to the local player's incoming
        # `cond` speed, the way a NosTale speed buff works (+1, +2, +3...).
        speed_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        speed_row.append(Gtk.Label(label="Speed +"))
        # No small host-side cap: the spin moves the value up as far as the
        # DLL can emulate it. The client's own 59 limit is raised by the DLL.
        speed_adjustment = Gtk.Adjustment(
            value=0,
            lower=0,
            upper=SPEED_BOOST_MAX,
            step_increment=1,
            page_increment=1000000,
        )
        self.speed_boost_spin = Gtk.SpinButton(
            adjustment=speed_adjustment, climb_rate=1.0, digits=0
        )
        self.speed_boost_spin.set_value(0)
        self.speed_boost_spin.set_width_chars(10)
        self.speed_boost_spin.set_tooltip_text(
            "Flat walking-speed bonus; 0 = normal. Scroll or type any value."
        )
        self.speed_boost_spin.connect(
            "value-changed", lambda *_: self._on_speed_boost_changed()
        )
        speed_row.append(self.speed_boost_spin)
        page.append(speed_row)

        # Cast speed: a plain switch. On = every client-side cooldown is
        # zeroed and the bot casts the moment the server is ready; off =
        # normal. There is no value to tune: the server's cooldown and cast
        # time are the hard floor.
        cast_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cast_row.append(Gtk.Label(label="Cast speed"))
        self.cast_boost_switch = Gtk.Switch()
        self.cast_boost_switch.set_active(False)
        self.cast_boost_switch.set_valign(Gtk.Align.CENTER)
        self.cast_boost_switch.set_tooltip_text(
            "On: press a skill once — it repeats at the server's fastest "
            "rate; press another skill to switch. With the bot running its "
            "spells cast immediately too. Client cooldowns stay visible."
        )
        self.cast_boost_switch.connect(
            "notify::active", lambda *_: self._on_cast_speed_changed()
        )
        cast_row.append(self.cast_boost_switch)
        page.append(cast_row)
        return page

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
        # A GUI attached after login has no map id yet. The log's last `at`
        # and the client's own walks already say where the character is, so
        # seed the world instead of guessing from monster positions.
        map_id, x, y, char_id = load_last_position(self.log_file)
        world = self.engine.world
        if world.map_id is None and map_id is not None:
            world.map_id = map_id
            if x is not None and y is not None:
                world.x, world.y = x, y
            if char_id is not None:
                world.self_id = char_id
                self._learn_char_id(char_id)
        # A player who spawned before this GUI attached has no live `in` in
        # memory; the log still has their spawn and movements. Seed them so
        # Follow can find them by name instead of reporting "not in sight".
        # Family fields are seeded too: otherwise every pre-attach player
        # looked "family unknown" and auto-invite could never select them.
        metadata = load_player_metadata(self.log_file)
        for player_id, (name, px, py) in load_player_spawns(
            self.log_file
        ).items():
            if player_id == world.self_id:
                # The bazar relist needs the owner name to recognise its own
                # listing; the log's `in 1 <name> - <charId>` has it even when
                # the GUI attached after login.
                if name:
                    world.player_names[player_id] = name
                self._bazar_sync_from_world()
                continue
            entity = world.entities.get(player_id)
            if entity is None:
                entity = Entity(player_id, 1, name=name, x=px, y=py)
                world.entities[player_id] = entity
            else:
                entity.x, entity.y = px, py
                if not entity.name:
                    entity.name = name
            meta = metadata.get(player_id)
            if meta is not None:
                (
                    entity.family_id,
                    entity.family_name,
                    entity.family_known,
                    entity.fairy_vnum,
                    entity.fairy_element,
                    entity.fairy_known,
                ) = meta
        merged = False
        # `gp` is the server's own portal list: exact and complete per map.
        for (map_id, x, y), destination in load_gates(self.log_file).items():
            if set_gate(self.portals, map_id, x, y, destination):
                merged = True
        # Crossings fill in maps whose `gp` was missed.
        for (map_id, x, y), destination in load_log_portals(
            self.log_file
        ).items():
            if add_portal(self.portals, map_id, x, y, destination):
                merged = True
        normalized = normalize_portals(self.portals)
        if normalized != self.portals:
            self.portals = normalized
            merged = True
        if merged:
            save_portals(self.portals)
        for (map_id, x, y), entry in load_timespaces(
            self.log_file
        ).items():
            ts_id, _flag, min_level, max_level = entry
            self.timespaces[(map_id, x, y)] = (
                ts_id,
                min_level,
                max_level,
            )

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

        The static table holds thousands of entries; showing its first 40
        was the confusing part. Selections that are not on this map stay visible (so
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
        if not self.spells_loaded:
            text = "reading Skill.dat…"
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
        merged = False
        for (map_id, x, y), destination in self.engine.world.gates.items():
            if set_gate(self.portals, map_id, x, y, destination):
                merged = True
        # A `gp` burst can arrive after a route was drawn: replan it with the
        # new portal cells instead of leaving a path that crosses one.
        gate_cells = frozenset(
            (x, y)
            for (map_id, x, y) in self.engine.world.gates
            if self.map_grid is not None and map_id == self.map_grid.map_id
        )
        if gate_cells != self._gate_cells_seen:
            self._gate_cells_seen = gate_cells
            if self.waypoints:
                self._rebuild_path()
        for (map_id, x, y), destination in self.engine.world.portals.items():
            if add_portal(self.portals, map_id, x, y, destination):
                merged = True
        normalized = normalize_portals(self.portals)
        if normalized != self.portals:
            self.portals = normalized
            merged = True
        for (map_id, x, y), entry in self.engine.world.timespaces.items():
            value = (entry.ts_id, entry.min_level, entry.max_level)
            if self.timespaces.get((map_id, x, y)) != value:
                self.timespaces[(map_id, x, y)] = value
                merged = True
        if merged:
            save_portals(self.portals)
            if self.map_area is not None:
                self.map_area.queue_draw()
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
        search.set_hexpand(True)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        top.append(search)
        top.append(
            self._help_button(
                "spell picker",
                "Active spells scanned from the client's NSgtdData.NOS / "
                "Skill.dat, with slot status when the learned skill list is "
                "known. Pick one to add it as a rule.",
            )
        )
        box.append(top)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.set_activate_on_single_click(True)
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(listbox)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        box.append(scroller)

        info = Gtk.Label(label="")
        info.set_xalign(0)
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
                info.set_text("Decoding Skill.dat…")
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
                    f"{len(spells)} matched · slots unknown (relog to learn)"
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
            "Seconds before this rule may fire again; Skill.dat cooldown still applies."
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
            last_cast = self.spell_last_cast.get(index, 0.0)
            slot = self._skill_slot(rule.spell)
            if slot is not None and self._server_skill_ready(
                slot, last_cast
            ):
                # The server said this skill's cooldown ended (`sr`). Cast
                # speed means the minimum delay: drop the local Skill.dat
                # and per-rule gates, the server's readiness is the floor.
                cooldown = 0.0
            if now < last_cast + cooldown:
                continue
            target = self._spell_target(rule)
            if target == "self" and char_id is None:
                continue
            if target == "monster":
                if monster is None:
                    continue
                if not self._has_line_of_sight(monster):
                    continue
                if not self._spell_in_range(info, monster):
                    continue
            if info is not None and info.mp_cost > 0 and stats.mp < info.mp_cost:
                continue
            if self._spell_condition_met(rule):
                return index, rule
        return None

    def _note_attack(self, target_id: int | None, now: float) -> None:
        """Queue an attack so the server's matching `cancel` is classified.

        `cancel 0 0 -1` ends one cast, not necessarily the newest: with a
        short skill cooldown the next attack is already sent when the
        previous cast's `su`/`cancel` pair arrives. Entries are kept in send
        order and paired with the server's replies in the same order.
        """
        self.last_attack_sent_at = now
        casts = self.pending_casts
        casts.append([now, False, target_id])
        while len(casts) > CAST_PENDING_MAX:
            casts.pop(0)

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
            # Select in the client first (the DLL applies it before the u_s
            # on the same connection).
            if not self._ensure_target(monster.entity_id, now):
                return False
        send_skill = self._skill_send_id(rule.spell)
        if send_skill is None:
            return False
        packet = self._skill_packet(
            target_id, skill=send_skill, target_type=target_type
        )
        self._send(packet)
        self._note_attack(target_id, now)
        if target_type == 3:
            self.fight_target_id = target_id
        # The per-rule cooldown (max of the rule and Skill.dat) is enforced in
        # _select_spell_rule; here only the cast animation plus the Min delay
        # floor pace the next action. Cast speed above 100% shortens that
        # client-side cast pause; the server's cooldown is still checked by
        # the sr readiness gate.
        scale = self._cast_speed_scale()
        cooldown = (info.cast_time if info is not None else 0.0) * scale
        self.attack_cooldown_until = now + cooldown
        cast_pause = (
            info.cast_time if info is not None else 0.0
        ) * scale
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
            skill=0,
            skill_template=DEFAULT_SKILL_TEMPLATE,
            attack_range=ATTACK_SEARCH_CELLS,
            attack_cooldown=0.0,
            pickup=bool(
                self.pickup_toggle is not None
                and self.pickup_toggle.get_active()
            ),
            loot_range=LOOT_APPROACH_CELLS,
            recover=bool(
                self.recover_toggle is not None
                and self.recover_toggle.get_active()
            ),
            low_hp_pct=float(self.low_hp_pct.get_value()),
            recover_mp=bool(
                self.recover_mp_toggle is not None
                and self.recover_mp_toggle.get_active()
            ),
            low_mp_pct=float(self.low_mp_pct.get_value()),
            revive_on_death=bool(
                self.revive_toggle is not None
                and self.revive_toggle.get_active()
            ),
            loop=bool(
                self.loop_toggle is not None
                and self.loop_toggle.get_active()
            ),
            block_portals=bool(
                self.block_portal_toggle is not None
                and self.block_portal_toggle.get_active()
            ),
            speed_boost=(
                int(self.speed_boost_spin.get_value())
                if self.speed_boost_spin is not None
                else 0
            ),
            cast_boost=bool(
                self.cast_boost_switch is not None
                and self.cast_boost_switch.get_active()
            ),
            follow=bool(
                self.follow_player_toggle is not None
                and self.follow_player_toggle.get_active()
            ),
            follow_player=(
                self.follow_player_entry.get_text().strip()
                if self.follow_player_entry is not None
                else ""
            ),
            follow_attack=bool(
                self.follow_attack_check is not None
                and self.follow_attack_check.get_active()
            ),
            follow_mirror=bool(
                self.follow_mirror_check is not None
                and self.follow_mirror_check.get_active()
            ),
            tame=bool(
                self.tame_toggle is not None
                and self.tame_toggle.get_active()
            ),
            tame_vnum=(
                int(self.tame_vnum_spin.get_value())
                if self.tame_vnum_spin is not None
                else 333
            ),
            tame_slot=(
                int(self.tame_slot_spin.get_value())
                if self.tame_slot_spin is not None
                else 1
            ),
            tame_hp_pct=(
                float(self.tame_hp_spin.get_value())
                if self.tame_hp_spin is not None
                else 25.0
            ),
            draw_path=bool(
                self.draw_path_toggle is not None
                and self.draw_path_toggle.get_active()
            ),
            map_id=(
                self.map_grid.map_id
                if self.map_grid is not None
                else self.engine.world.map_id
            ),
            waypoints=list(self.waypoints),
            path_start=self.path_start,
            monster_mode=self._current_monster_mode(),
            monster_vnums=sorted(v for v in self.target_vnums if v > 0),
            attack_unknown=bool(
                self.attack_unknown_toggle is not None
                and self.attack_unknown_toggle.get_active()
            ),
            spell_rules=self._collect_spell_rules(),
        )

    def _apply_config(self, config: BotConfig) -> None:
        if self.bot_running:
            self._stop_bot()
        self.auto_attack_toggle.set_active(config.auto_attack)
        self.retaliate_toggle.set_active(config.retaliate)
        self._update_combat_info()
        self.pickup_toggle.set_active(config.pickup)
        self.recover_toggle.set_active(config.recover)
        self.low_hp_pct.set_value(config.low_hp_pct)
        self.recover_mp_toggle.set_active(config.recover_mp)
        self.low_mp_pct.set_value(config.low_mp_pct)
        if self.revive_toggle is not None:
            self.revive_toggle.set_active(config.revive_on_death)
        self.loop_toggle.set_active(config.loop)
        if self.block_portal_toggle is not None:
            self.block_portal_toggle.set_active(config.block_portals)
        if self.speed_boost_spin is not None:
            self.speed_boost_spin.set_value(config.speed_boost)
        if self.cast_boost_switch is not None:
            self.cast_boost_switch.set_active(config.cast_boost)
        if self.follow_player_toggle is not None:
            self.follow_player_toggle.set_active(config.follow)
        if self.follow_player_entry is not None:
            self.follow_player_entry.set_text(config.follow_player)
        if self.follow_attack_check is not None:
            self.follow_attack_check.set_active(config.follow_attack)
        if self.follow_mirror_check is not None:
            self.follow_mirror_check.set_active(config.follow_mirror)
        if self.tame_toggle is not None:
            self.tame_toggle.set_active(config.tame)
        if self.tame_vnum_spin is not None:
            self.tame_vnum_spin.set_value(config.tame_vnum)
        if self.tame_slot_spin is not None:
            self.tame_slot_spin.set_value(config.tame_slot)
        if self.tame_hp_spin is not None:
            self.tame_hp_spin.set_value(config.tame_hp_pct)
        if self.draw_path_toggle is not None:
            self.draw_path_toggle.set_active(config.draw_path)

        # The drawn route is part of the configuration: restore the waypoints
        # and rebuild the A* path so the map shows the same line as when it
        # was saved. The route belongs to a map, so a mismatch is reported
        # instead of drawing it over unrelated terrain.
        self.path_start = config.path_start
        self.waypoints = list(config.waypoints)
        current_map = (
            self.map_grid.map_id
            if self.map_grid is not None
            else self.engine.world.map_id
        )
        if (
            config.map_id is not None
            and current_map is not None
            and config.map_id != current_map
        ):
            self.path_cells = []
            self._set_path_info(
                f"route is for map {config.map_id} "
                f"(current map {current_map})"
            )
        else:
            self._rebuild_path()
        if self.map_area is not None:
            self.map_area.queue_draw()

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

        if self.engine.world.map_id in self.map_grids:
            self._select_map(self.engine.world.map_id)
        elif self.map_info is not None:
            # No server map id yet (the window opened before the DLL/injection
            # or before login). Do not invent map 20: the auto-map tick picks
            # a map as soon as `c_map`/`at` or live positions arrive.
            self.map_info.set_text("waiting for the game…")

    def _select_map(self, map_id: int, source: str = "server") -> None:
        grid = self.map_grids.get(map_id)
        if grid is None:
            self._set_status(f"map {map_id} not in {self.map_file}")
            return

        self.map_grid = grid
        self.map_source = source
        # A new map invalidates the old coordinates: do not keep walking a
        # path from the previous map.
        if self.bot_running:
            self._stop_bot()
        self._stop_travel(quiet=True)
        self.bot_goals = []
        self.waypoints.clear()
        self.path_cells.clear()
        self.path_start = None
        self.path_index = 0
        self.pending_target = None
        self._build_map_surface()
        self._update_map_size()
        total = grid.width * grid.height
        walkable = grid.walkable_count()
        suffix = " · auto" if source == "auto" else ""
        self.map_info.set_text(
            f"map {grid.map_id}: {grid.width}x{grid.height}, "
            f"walkable {100 * walkable / total:.1f}%{suffix}"
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
        name = (
            world.self_name or self.bazar.config.character or ""
        ).strip()
        if self.char_name is not None:
            if name:
                self.char_name.set_markup(
                    f"<b>{GLib.markup_escape_text(name)}</b>"
                )
            else:
                self.char_name.set_text("")
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
                bits = []
                if self.recover_need_hp and stats.hp_max > 0:
                    percent = 100.0 * stats.hp / stats.hp_max
                    threshold = (
                        self.low_hp_pct.get_value()
                        if self.low_hp_pct is not None
                        else 0
                    )
                    bits.append(
                        f"HP {percent:.0f}% (below {threshold:.0f}%)"
                    )
                if self.recover_need_mp and stats.mp_max > 0:
                    percent = 100.0 * stats.mp / stats.mp_max
                    threshold = (
                        self.low_mp_pct.get_value()
                        if self.low_mp_pct is not None
                        else 0
                    )
                    bits.append(
                        f"MP {percent:.0f}% (below {threshold:.0f}%)"
                    )
                detail = " · ".join(bits) if bits else "HP/MP"
                text = (
                    f"RECOVERING — {detail}. Sitting until full; "
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

    def _has_line_of_sight(self, entity: Entity) -> bool:
        """True when the straight line to the entity crosses no wall.

        The server refuses attacks through blocked terrain (a town wall, a
        rock ledge) and in safe zones. Walkable cells are the best terrain
        proxy the map archive provides; the endpoints are not checked, so a
        monster standing on a ledge can still be hit from next to it.
        """
        grid = self.map_grid
        if grid is None:
            return True
        position = self._player_cell()
        if position is None:
            return True
        for cell in self._segment_cells(
            position, (entity.x, entity.y)
        )[1:-1]:
            if not grid.is_walkable(*cell):
                return False
        return True

    def _combat_paused(self, now: float) -> bool:
        """True while combat is paused after repeated refused attacks.

        The pause also ends early once the character has walked away from the
        place where the refusals started, so leaving the town/safe zone
        resumes farming without waiting for the timer. Being attacked ends it
        too when the attacker can be fought back: standing still while a
        monster hits the character looked like a frozen bot.
        """
        if now >= self.combat_paused_until:
            return False
        position = self._player_cell()
        if (
            self.combat_pause_pos is not None
            and position is not None
            and max(
                abs(position[0] - self.combat_pause_pos[0]),
                abs(position[1] - self.combat_pause_pos[1]),
            )
            >= 5
        ):
            self.combat_paused_until = 0.0
            self.combat_pause_pos = None
            self._set_status("moved away; combat resumed")
            return False
        if self._under_attack():
            attacker = self._retaliation_target(int(self._attack_reach()))
            if attacker is not None:
                self.combat_paused_until = 0.0
                self.combat_pause_pos = None
                self.cancel_events.clear()
                self.target_fail_events.clear()
                self._set_status("attacked while paused; fighting back")
                return False
        return True

    def _attack_reach(self) -> float:
        """The equipped card's basic attack range (15 cells if unknown)."""
        info = self.spells.get(self._basic_skill_vnum() or 0)
        if info is not None and info.range > 0:
            return float(info.range)
        return float(ATTACK_SEARCH_CELLS)

    def _retaliation_target(self, radius: int) -> Entity | None:
        world = self.engine.world
        if world.last_attacker_id is None:
            return None
        # Only recent hits count; a stale attacker should not lock targeting.
        if time.time() - world.last_attacker_at > RETALIATE_WINDOW_S:
            return None
        entity = world.entities.get(world.last_attacker_id)
        if entity is None or entity.kind != 3:
            return None
        # Self-defence ignores the target filter: a bee that hits you is
        # fought even in "Only selected" mode when its vnum is not selected.
        # Farming the map may be whitelisted; being killed is not.
        if self._target_blocked(entity.entity_id):
            return None
        position = self._player_cell()
        if position is None:
            return None
        distance = max(abs(entity.x - position[0]), abs(entity.y - position[1]))
        if distance > radius or not self._has_line_of_sight(entity):
            return None
        return entity

    def _recent_attacker(self, entity_id: int) -> bool:
        """True while the entity has hit the character recently."""
        when = self.engine.world.self_attackers.get(entity_id)
        return (
            when is not None
            and time.time() - when <= RETALIATE_WINDOW_S
        )

    def _under_attack(self) -> bool:
        """True while anything has hit the character recently.

        Retaliate needs a resolvable entity to attack back; recovery must not
        wait for one. Being hit is enough to forbid resting even when the
        attacker is missing from the world state (dropped as a ghost, out of
        sight, or an unseen ranged hit).
        """
        world = self.engine.world
        if world.last_attacker_id is None:
            return False
        return time.time() - world.last_attacker_at <= RETALIATE_WINDOW_S

    def _defend_before_loot(self, under_attack: bool) -> bool:
        """True when a pickup has to wait until the fight is over.

        Drops are collected right away, also while monsters hit the
        character: on a busy map the fight never ends, and drops left for
        "after the fight" were never picked up. Only a character whose HP is
        below the Recovery HP threshold defends first.
        """
        if not under_attack:
            return False
        stats = self.engine.world.stats
        if stats.hp_max <= 0:
            return False
        hp_pct = 100.0 * stats.hp / stats.hp_max
        return hp_pct < float(self.low_hp_pct.get_value())

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
        points = self._live_points()
        if self.map_grid is None:
            if len(points) < 2:
                if self.map_info is not None:
                    self.map_info.set_text("waiting for the game…")
                return True
            choice = choose_map_for_positions(self.map_grids, points)
            if choice is None:
                return True
            fraction, map_id = choice
            self._select_map(map_id, source="auto")
            self._set_status(
                f"auto-detected map {map_id} "
                f"({fraction * 100:.1f}% live positions)"
            )
            return True

        if len(points) < 2:
            return True
        choice = choose_map_for_positions(
            self.map_grids, points, current_id=self.map_grid.map_id
        )
        if choice is None:
            return True
        fraction, map_id = choice
        if map_id != self.map_grid.map_id:
            self._select_map(map_id, source="auto")
            self._set_status(
                f"auto-detected map {map_id} "
                f"({fraction * 100:.1f}% live positions)"
            )
        return True

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
            if self.path_start is None:
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

        # planned route start (Draw path): a cyan ring, not tied to the
        # character; Start walks there first.
        if self.path_start is not None:
            x, y = self.path_start
            cx = x * scale + scale / 2
            cy = y * scale + scale / 2
            radius = max(4.0, scale * 0.8)
            context.set_source_rgba(0.0, 0.9, 0.9, 0.95)
            context.set_line_width(max(1.8, scale * 0.3))
            context.arc(cx, cy, radius, 0, 2 * math.pi)
            context.stroke()
            context.set_source_rgba(1.0, 1.0, 1.0, 0.95)
            context.arc(cx, cy, radius * 0.45, 0, 2 * math.pi)
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

        # Time-Space entrances from `wp`; used below to avoid drawing a
        # duplicate generic instance marker at the same gate.
        timespace_cells = {
            (x, y)
            for (map_id, x, y) in self.timespaces
            if map_id == grid.map_id
        }
        # Portals: a ring with a diamond centre, drawn after the own marker so
        # a portal under or next to the character stays visible (a filled dot
        # would be hidden by the own dot). Magenta is a normal map portal;
        # amber marks a Time-Space/instance entrance (destination 2xxx), which
        # also carries the nosapki Time-Space stone icon.
        for (portal_map, px, py), destination in self.portals.items():
            if portal_map != grid.map_id:
                continue
            if not (0 <= px < grid.width and 0 <= py < grid.height):
                continue
            cx = px * scale + scale / 2
            cy = py * scale + scale / 2
            if is_time_space_map(destination):
                if any(
                    abs(px - tx) <= 2 and abs(py - ty) <= 2
                    for tx, ty in timespace_cells
                ):
                    continue
                self._draw_timespace_icon(
                    context, cx, cy, scale, own == (px, py)
                )
                continue

            radius = max(6.0, scale * 1.2)
            context.set_source_rgba(0.85, 0.2, 0.95, 1.0)
            context.set_line_width(max(2.0, scale * 0.4))
            context.arc(cx, cy, radius, 0, 2 * math.pi)
            context.stroke()

            size = max(2.0, scale * 0.35)
            context.move_to(cx, cy - size)
            context.line_to(cx + size, cy)
            context.line_to(cx, cy + size)
            context.line_to(cx - size, cy)
            context.close_path()
            context.fill()

        # Time-Space entrances from the server's `wp` list, drawn last so they
        # stay on top. The tooltip carries the TS id and level range.
        for (map_id, tx, ty), _entry in self.timespaces.items():
            if map_id != grid.map_id:
                continue
            if not (0 <= tx < grid.width and 0 <= ty < grid.height):
                continue
            self._draw_timespace_icon(
                context,
                tx * scale + scale / 2,
                ty * scale + scale / 2,
                scale,
                own == (tx, ty),
            )

        context.restore()

    def _timespace_icon_surface(self) -> cairo.ImageSurface | None:
        """Load the nosapki Time-Space icon once; None if it is missing."""
        if self._timespace_icon_loaded:
            return self._timespace_icon
        self._timespace_icon_loaded = True
        try:
            self._timespace_icon = cairo.ImageSurface.create_from_png(
                str(TIMESPACE_ICON)
            )
        except (OSError, cairo.Error):
            self._timespace_icon = None
        return self._timespace_icon

    def _draw_timespace_icon(
        self,
        context,
        cx: float,
        cy: float,
        scale: float,
        standing_on_it: bool,
    ) -> None:
        """Draw the nosapki Time-Space stone in a gold frame.

        When the character stands on the entrance the icon is skipped and only
        the gold ring is drawn, so the own red dot stays readable.
        """
        icon = self._timespace_icon_surface()
        if icon is None:
            # No asset: an amber ring is still a distinct Time-Space marker.
            context.set_source_rgba(1.0, 0.70, 0.0, 1.0)
            context.set_line_width(max(2.0, scale * 0.4))
            context.arc(cx, cy, max(6.0, scale * 1.2), 0, 2 * math.pi)
            context.stroke()
            return

        size = max(22.0, scale * 5.0)
        if standing_on_it:
            context.set_source_rgba(1.0, 0.70, 0.0, 1.0)
            context.set_line_width(max(2.0, scale * 0.4))
            context.arc(cx, cy, size * 0.62, 0, 2 * math.pi)
            context.stroke()
            return

        # Dark disc behind the stone so it reads on white terrain.
        context.set_source_rgba(0.05, 0.05, 0.08, 0.55)
        context.arc(cx, cy, size * 0.56, 0, 2 * math.pi)
        context.fill()
        context.save()
        context.translate(cx - size / 2, cy - size / 2)
        context.scale(size / icon.get_width(), size / icon.get_height())
        context.set_source_surface(icon, 0, 0)
        context.paint()
        context.restore()
        # Gold frame around the icon.
        context.set_source_rgba(1.0, 0.70, 0.0, 1.0)
        context.set_line_width(max(2.0, scale * 0.4))
        context.arc(cx, cy, size * 0.63, 0, 2 * math.pi)
        context.stroke()

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
        self._handle_map_cell(cell, gesture.get_current_button())

    def _handle_map_cell(self, cell: tuple[int, int], button: int = 1) -> None:
        """One map click, already converted to a map cell.

        Split from `_on_map_click` so the drawing behaviour (add/remove a
        waypoint, walk there) is testable without synthesising widget
        coordinates.
        """
        if self.map_grid is None:
            return

        if button == 3:
            if self.waypoints:
                self.waypoints.pop()
                self._rebuild_path()
            elif self.path_start is not None:
                self.path_start = None
                self._rebuild_path()
                self._set_status("start point cleared")
            return

        if not self.map_grid.is_walkable(*cell):
            self._set_status(f"cell {cell} is blocked")
            return
        if cell in self._portal_cells():
            self._set_status(
                f"cell {cell} is a map portal; refused (Block portals is on)"
            )
            return

        draw_path = bool(
            self.draw_path_toggle is not None
            and self.draw_path_toggle.get_active()
        )
        planning = self.bot_running or draw_path
        if not planning:
            # Walk there now through the client's own MoveTo (pathfinding and
            # animation are the game's). _start_travel stands the character
            # up first when POS reports it sitting.
            self._start_travel(cell)
            return

        if not self.bot_running and draw_path and self.path_start is None:
            # The first Draw path press defines where the route begins; it is
            # not anchored to the character. A route that already has
            # waypoints (loaded from a config, or with the start cleared) must
            # keep them: the click then adds a waypoint instead of wiping the
            # whole drawn path.
            if not self.waypoints:
                self.path_start = cell
                self._rebuild_path()
                self._set_status(
                    f"start point {cell}; click to add waypoints"
                )
                return

        self.waypoints.append(cell)
        self._rebuild_path()
        self._set_status(f"path drawn to {cell} — press Start to follow")

    def _start_travel(
        self, cell: tuple[int, int], on_arrival=None
    ) -> bool:
        """Walk to a clicked cell in short client-MoveTo legs.

        A single far `MOVE` makes the client jump (and the server correct), so
        follow the A* path in the same three-cell legs the bot uses. The map
        itself follows the real position via the POS poll. ``on_arrival`` runs
        when the last leg is reached (used to start a planned route at its
        start point).
        """
        self._stop_travel(quiet=True)
        if self.map_grid is None:
            self._set_status("no map loaded")
            return False
        position = self._player_cell()
        if position is None:
            self._set_status("position unknown; cannot walk there")
            return False
        if position == cell:
            self._set_status(f"already at {cell}")
            return False
        if self.engine.world.resting is True:
            # Sitting blocks movement; the client's MoveTo would do nothing.
            char_id = self._char_id()
            if char_id is None:
                self._set_status("character is sitting; char id unknown")
                return False
            if not self._send(f"rest 1 1 {char_id}"):
                return False
            self._set_status(f"standing up, then walking to {cell}")
            GLib.timeout_add(700, self._start_travel, cell, on_arrival)
            return False
        path = astar(
            self.map_grid, position, cell, self._portal_cells()
        )
        if path is None:
            self._set_status(f"no path to {cell}")
            return False
        targets = [path[i] for i in range(2, len(path), 3)]
        if not targets or targets[-1] != cell:
            targets.append(cell)
        self.travel_targets = targets
        self.travel_index = 0
        self.travel_pending = None
        self.travel_on_arrival = on_arrival
        self._travel_pos_at = 0.0
        self.travel_timer = GLib.timeout_add(
            TRAVEL_INTERVAL_MS, self._travel_tick
        )
        self._set_status(f"walking to {cell} ({len(targets)} legs)")
        return False

    def _travel_request_position(self, now: float) -> None:
        """Poll POS faster while travelling, to see the real cell."""
        if now - self._travel_pos_at < TRAVEL_POS_POLL_S:
            return
        self._travel_pos_at = now
        if self.client is not None and self.client.connected:
            try:
                self.client.send_raw(POS_COMMAND)
            except (ConnectionLost, ValueError):
                pass

    def _finish_travel(self) -> None:
        self.travel_targets = []
        self.travel_index = 0
        self.travel_pending = None
        if self.travel_timer is not None:
            GLib.source_remove(self.travel_timer)
            self.travel_timer = None
        callback, self.travel_on_arrival = self.travel_on_arrival, None
        self._set_status("arrived")
        if callback is not None:
            GLib.idle_add(callback)

    def _travel_tick(self) -> bool:
        if self._closing:
            self.travel_timer = None
            return False
        if self.bot_running or not self.travel_targets:
            self.travel_timer = None
            return False
        now = time.monotonic()
        self._travel_request_position(now)
        actual = self._player_cell()

        if self.engine.world.resting is True:
            # Sitting blocks the client's MoveTo: every leg would time out.
            # The sit state can arrive after Start (a stale POS at the button
            # press), so it is checked on every travel tick, not only once.
            # `rest` is a toggle; rate-limit the stand request.
            self.travel_pending = None
            if now >= self.stand_sent_until:
                char_id = self._char_id()
                if char_id is not None:
                    self._send(f"rest 1 1 {char_id}")
                    self.stand_sent_until = now + REST_RETRY_S
                    self._set_status(
                        "sitting; standing up before walking"
                    )
            return True

        if self.travel_pending is not None:
            target, sent_at = self.travel_pending
            last_leg = self.travel_index >= len(self.travel_targets) - 1
            remaining = None
            if actual is not None:
                remaining = max(
                    abs(actual[0] - target[0]),
                    abs(actual[1] - target[1]),
                )
            if last_leg:
                if actual == target:
                    self._finish_travel()
                    return False
            elif (
                remaining is not None
                and remaining <= TRAVEL_HANDOFF_CELLS
                and now - sent_at >= TRAVEL_MIN_LEG_S
            ):
                # Hand the next leg to the client while the character is
                # still moving, so it keeps walking instead of stopping at
                # every three-cell boundary.
                self.travel_index += 1
                self.travel_pending = None
                return True
            if (
                not last_leg
                and actual == target
                and now - sent_at >= TRAVEL_MIN_LEG_S
            ):
                # POS unavailable/stale: fall back to the walk destination.
                self.travel_index += 1
                self.travel_pending = None
                return True
            if now - sent_at > TRAVEL_LEG_TIMEOUT_S:
                if last_leg:
                    final = (
                        self.travel_targets[-1]
                        if self.travel_targets
                        else target
                    )
                    self._stop_travel()
                    self._set_status(f"walk to {final} timed out; stopped")
                    return False
                self.travel_index += 1
                self.travel_pending = None
                self._set_status("walk leg timed out; continuing")
            return True

        if self.travel_index >= len(self.travel_targets):
            self._finish_travel()
            return False

        target = self.travel_targets[self.travel_index]
        if target in self._portal_cells():
            self._stop_travel()
            self._set_status(
                f"portal at {target}; walk stopped (Block portals is on)"
            )
            return False
        if actual == target:
            self.travel_index += 1
            return True
        if not self._client_move(target[0], target[1]):
            self._stop_travel()
            return False
        self.travel_pending = (target, now)
        return True

    def _stop_travel(self, quiet: bool = False) -> None:
        self.travel_targets = []
        self.travel_index = 0
        self.travel_pending = None
        self.travel_on_arrival = None
        if self.travel_timer is not None:
            GLib.source_remove(self.travel_timer)
            self.travel_timer = None
        if not quiet:
            self._set_status("walk stopped")

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
        if drop.owner_id > 0:
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

        for (map_id, tx, ty), entry in self.timespaces.items():
            if map_id != self.map_grid.map_id:
                continue
            _ts_id, min_level, _max_level = entry
            cx, cy = self._map_cell_screen(tx, ty)
            distance = math.hypot(x - cx, y - cy)
            if distance <= radius and (best is None or distance < best[0]):
                # Same wording as the client's own tooltip on the stone.
                best = (
                    distance,
                    f"Time-Space Stone\nRequired Level {min_level}",
                )

        for (portal_map, px, py), destination in self.portals.items():
            if portal_map != self.map_grid.map_id:
                continue
            cx, cy = self._map_cell_screen(px, py)
            distance = math.hypot(x - cx, y - cy)
            if distance <= radius and (best is None or distance < best[0]):
                label = (
                    "Time-Space"
                    if is_time_space_map(destination)
                    else "Portal"
                )
                best = (
                    distance,
                    f"{label}\nposition {px}, {py}\nto map {destination}",
                )

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
        self.path_start = None
        self.path_index = 0
        self.pending_target = None
        self._stop_travel(quiet=True)
        if self.map_area is not None:
            self.map_area.queue_draw()
        self._set_status("path cleared")
        self._set_path_info("no path")

    def _player_cell(self) -> tuple[int, int] | None:
        # Prefer the DLL's real player position (POS): a client `walk` packet
        # carries the MoveTo destination, so `world.position` jumps ahead of
        # the animation. POS is polled twice a second while connected.
        if (
            self.pos_cell is not None
            and time.monotonic() - self.pos_at < 1.5
        ):
            return self.pos_cell
        if self.engine.world.position is not None:
            return self.engine.world.position
        char_id = self._char_id()
        if char_id is not None:
            entity = self.engine.world.entities.get(char_id)
            if entity is not None:
                return (entity.x, entity.y)
        return None

    def _find_player(self, name: str) -> Entity | None:
        """The live player entity matching a name or a char id.

        Player spawns (`in 1 <name> - <charId> <x> <y> …`) are keyed by char
        id and keep the full name, spaces included. A numeric entry is taken
        as the char id, so the id shown in the tooltip/status works too.
        """
        wanted = name.strip()
        if not wanted:
            return None
        if wanted.isdigit():
            entity = self.engine.world.entities.get(int(wanted))
            return entity if entity is not None and entity.kind == 1 else None
        folded = wanted.casefold()
        for entity in self.engine.world.entities.values():
            if entity.kind != 1:
                continue
            # A player who spawned before the host attached has no `in`
            # packet in memory; the log backfill still knows the name.
            name = entity.name or self.log_names.get(entity.entity_id, "")
            if name.casefold() == folded:
                return entity
        return None

    def _resume_waypoint_index(self, position: tuple[int, int]) -> int:
        """Which waypoint to head to first from the current cell.

        Start joins the drawn route at the nearest leg instead of walking
        back to the route start (or re-treading already visited legs): the
        index returned is the end of the nearest segment, so an interrupted
        or manually moved character continues wherever it stands.
        """
        waypoints = self.waypoints
        if not waypoints:
            return 0

        def distance_to_segment(
            start: tuple[int, int], end: tuple[int, int]
        ) -> int:
            return min(
                max(abs(cx - position[0]), abs(cy - position[1]))
                for cx, cy in self._segment_cells(start, end)
            )

        best_index = 0
        if self.path_start is not None:
            best = distance_to_segment(self.path_start, waypoints[0])
        else:
            best = max(
                abs(waypoints[0][0] - position[0]),
                abs(waypoints[0][1] - position[1]),
            )
        for index in range(1, len(waypoints)):
            distance = distance_to_segment(
                waypoints[index - 1], waypoints[index]
            )
            if distance < best:
                best = distance
                best_index = index
        if best_index == len(waypoints) - 1:
            # Standing at the route's last point (or past it): the next leg
            # is the first one, so Stop/Start after finishing re-runs the
            # route instead of idling at its end.
            last = waypoints[-1]
            if (
                max(
                    abs(last[0] - position[0]),
                    abs(last[1] - position[1]),
                )
                <= ROUTE_ARRIVE_CELLS
            ):
                return 0
        return best_index

    def _goals_from_path(
        self, path: list[tuple[int, int]]
    ) -> list[tuple[int, int]]:
        """Split a planned path into the bot's three-cell MoveTo goals."""
        goals = [path[index] for index in range(2, len(path), 3)]
        if not goals or goals[-1] != path[-1]:
            goals.append(path[-1])
        return goals

    def _replan_execution(
        self, position: tuple[int, int] | None = None
    ) -> None:
        """Re-plan the walk from a new position (manual interruption).

        Joins the route at the nearest leg from where the character is now,
        the same way Start does, so an interruption continues the route
        instead of waiting for a timeout or needing Stop/Start.
        """
        if not self.waypoints:
            return
        if position is None:
            position = self._player_cell()
        if position is None:
            return
        exec_path = self._apply_execution_path(position)
        if not exec_path:
            return
        self.bot_goals = self._goals_from_path(exec_path)
        self.path_index = 0

    def _apply_execution_path(
        self, position: tuple[int, int]
    ) -> list[tuple[int, int]]:
        """The route the bot will actually walk, from where it stands.

        The drawn preview in `path_cells` is left alone (clicks redraw the
        route); this plans from the current position to the nearest remaining
        leg, so Stop/Start or a manual move resumes the route instead of
        walking back to the start.
        """
        if self.map_grid is None:
            self._set_status("no map loaded")
            return []
        resume = self._resume_waypoint_index(position)
        remaining = self.waypoints[resume:]
        path: list[tuple[int, int]] = []
        current = position
        for waypoint in remaining:
            leg = astar(
                self.map_grid, current, waypoint, self._portal_cells()
            )
            if leg is None:
                self._set_status(f"no path to {waypoint}")
                return []
            path.extend(leg)
            current = waypoint
        self._set_status(
            f"path from you: {len(path)} cells, "
            f"{len(remaining)}/{len(self.waypoints)} waypoint(s)"
        )
        self._set_path_info(
            f"path: {len(path)} cells · {len(remaining)} waypoint(s)"
        )
        return path

    def _rebuild_path(self) -> None:
        self.path_cells = []
        if not self.bot_running:
            # Drawing/editing must never rewind a running bot: the execution
            # goals and their index belong to the Start that planned them.
            self.path_index = 0
        if self.map_grid is None:
            return
        if not self.waypoints:
            if self.map_area is not None:
                self.map_area.queue_draw()
            if self.path_start is not None:
                self._set_path_info(
                    f"start {self.path_start} · click waypoints"
                )
            return

        # Preview of the drawn route: from the route's start point (or the
        # character when no start is set) through every waypoint. Clicks must
        # never shrink or reset the drawn route; the execution path is planned
        # separately at Start (see _apply_execution_path).
        current = self.path_start
        if current is None:
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
            self._set_status(f"path start {current} is not walkable")
            return

        path: list[tuple[int, int]] = []
        for waypoint in self.waypoints:
            leg = astar(
                self.map_grid, current, waypoint, self._portal_cells()
            )
            if leg is None:
                self._set_status(f"no path to {waypoint}")
                self._set_path_info(f"no path to {waypoint}")
                continue
            path.extend(leg)
            current = waypoint
        self.path_cells = path
        if self.map_area is not None:
            self.map_area.queue_draw()
        origin = "start" if self.path_start is not None else "you"
        self._set_status(
            f"path from {origin}: {len(path)} cells, "
            f"{len(self.waypoints)} waypoint(s)"
        )
        self._set_path_info(
            f"path: {len(path)} cells · {len(self.waypoints)} waypoint(s)"
        )

    def _sync_portal_block(self) -> None:
        """Tell the DLL whether to drop the client's `preq` portal requests.

        Path avoidance keeps the bot off warp tiles, but the client also sends
        `preq` by itself when it is already standing on one (for example right
        after a warp). Dropping that packet in the DLL is the only hard block.
        """
        if self.client is None or not self.client.connected:
            return
        enabled = bool(
            self.block_portal_toggle is not None
            and self.block_portal_toggle.get_active()
        )
        try:
            self.client.send_raw(f"PORTALS|{1 if enabled else 0}")
        except (ConnectionLost, ValueError):
            pass

    def _on_block_portals_toggled(self) -> None:
        """Re-plan around the new obstacles and stop a stale click-to-walk."""
        enabled = bool(
            self.block_portal_toggle is not None
            and self.block_portal_toggle.get_active()
        )
        self._sync_portal_block()
        if self.travel_targets:
            self._stop_travel(quiet=True)
        if (
            enabled
            and self.pending_target is not None
            and self.pending_target in self._portal_cells()
        ):
            # A MOVE toward a warp tile is already in flight; drop the leg so
            # the next tick sends a safe destination instead of waiting for it.
            self.pending_target = None
            self.pending_is_path = False
            self.pending_loot_id = None
        self._rebuild_path()
        if self.map_area is not None:
            self.map_area.queue_draw()
        self._set_status(
            "portals blocked: paths avoid warp tiles"
            if enabled
            else "portal blocking off: paths may cross warp tiles"
        )

    def _sync_speed_boost(self, quiet: bool = False) -> None:
        """Ask the DLL to add a flat bonus to the client's walking speed.

        The DLL rewrites the local player's incoming `cond` speed before the
        client parses it, so the client walks faster and its own `walk`
        packets carry the boosted speed. The value applies immediately, not
        only while the bot runs. 0 is normal speed.
        """
        if self.client is None or not self.client.connected:
            return
        add = 0
        if self.speed_boost_spin is not None:
            add = int(self.speed_boost_spin.get_value())
        try:
            self.client.send_raw(f"SPEED|{add}")
        except (ConnectionLost, ValueError):
            return
        if not quiet:
            self._set_status(
                f"speed boost +{add} (client-side, high ban risk)"
                if add > 0
                else "speed boost off"
            )

    def _on_speed_boost_changed(self) -> None:
        if self._speed_tuning:
            return
        # The scroll bar can emit a value change for every pixel while it is
        # dragged; coalesce them into one SPEED| command.
        if self._speed_boost_source is None:
            self._speed_boost_source = GLib.timeout_add(
                120, self._flush_speed_boost
            )

    def _flush_speed_boost(self) -> bool:
        self._speed_boost_source = None
        self._sync_speed_boost()
        return False

    def _cast_boost_on(self) -> bool:
        """The Cast speed switch state (off = normal)."""
        return bool(
            self.cast_boost_switch is not None
            and self.cast_boost_switch.get_active()
        )

    def _cast_speed_scale(self) -> float:
        """Multiplier for the bot's own cast pause (1.0 = normal, 0 = off).

        The DLL does the client half; the bot drops its post-cast pause while
        Cast speed is on. The server's cooldown still gates each skill through
        ``_server_skill_ready``.
        """
        return 0.0 if self._cast_boost_on() else 1.0

    def _server_skill_ready(self, slot: int, since: float) -> bool:
        """True when the server said `sr <slot>` after our last cast.

        This server sends `sr <castingId>` when a skill's cooldown ends. With
        Cast speed on it is the authoritative ready moment: the bot casts
        again as soon as the server accepts instead of waiting out a longer
        client-side timer (the server starts the cooldown after the cast,
        the local timer starts at send).
        """
        if not self._cast_boost_on():
            return False
        ready = self.engine.world.skill_ready_at.get(slot)
        return ready is not None and ready > since

    def _restore_client_cooldowns(self, quiet: bool = False) -> None:
        """Make sure the client shows the real server cooldowns.

        DLL 0.2.8 could zero the skill-bar cooldowns; that hid the server's
        real cooldown and made casting look broken. The switch is host-side
        now (the bot chains at the server's ready signal), so the client's
        cooldown display always stays accurate. The DLL may still have the
        old state from an earlier click, so turn it off on attach.
        """
        if self.client is None or not self.client.connected:
            return
        try:
            self.client.send_raw("CAST|0")
        except (ConnectionLost, ValueError):
            pass

    def _cast_assist_packet(self, packet: Packet) -> None:
        """Cast speed without the bot: repeat the skill the player pressed.

        The client sends `u_s` when a skill is pressed. With Cast speed on
        (and the bot stopped) that packet is remembered, and every time the
        server sends `sr <slot>` — the end of that skill's cooldown — the
        same press is sent again. One key press therefore keeps casting at
        the server's maximum rate; pressing another skill switches the
        assist to it. The project's own casts are recognized via
        `_host_skill_sends` and never start an assist.
        """
        if not self._cast_boost_on() or self.bot_running:
            return
        now = time.monotonic()
        if packet.direction is Kind.SEND:
            if packet.opcode != "u_s":
                return
            values = packet.ints(3)
            if not values:
                return
            slot = values[0]
            for index, (host_slot, when, _text) in enumerate(
                self._host_skill_sends
            ):
                if host_slot == slot and now - when <= 2.0:
                    self._host_skill_sends.pop(index)
                    return
            if self.assist_packet != packet.text or self.assist_slot != slot:
                self.assist_packet = packet.text
                self.assist_slot = slot
                self.assist_started_at = now
                self._log_line(
                    f"cast speed assist: repeating {packet.text} "
                    f"(press another skill to switch)"
                )
                self._set_status(
                    "cast speed assist: repeating the skill you pressed; "
                    "press another skill to switch"
                )
            self.assist_seen_at = now
            return
        if packet.direction is not Kind.RECV or packet.opcode != "sr":
            return
        values = packet.ints(1)
        if (
            not values
            or self.assist_packet is None
            or values[0] != self.assist_slot
        ):
            return
        if now - self.assist_seen_at > CAST_ASSIST_TTL_S:
            self.assist_packet = None
            self.assist_slot = None
            self._set_status("cast speed assist stopped (idle)")
            return
        if now - self.assist_started_at > CAST_ASSIST_MAX_S:
            self.assist_packet = None
            self.assist_slot = None
            self._set_status(
                "cast speed assist stopped (5 min limit); press a skill "
                "to start again"
            )
            return
        packet_text = self.assist_packet
        self.assist_seen_at = now
        if self._send(packet_text) and now >= self.assist_status_at:
            self.assist_status_at = now + 5.0
            self._set_status(
                f"cast speed assist: repeating {packet_text}"
            )

    def _on_cast_speed_changed(self) -> None:
        if not self._cast_boost_on():
            self.assist_packet = None
            self.assist_slot = None
            self._set_status("cast speed normal")
            return
        message = (
            "cast speed ON: press a skill once — it repeats at the "
            "server's fastest rate; press another to switch"
        )
        self._set_status(message)
        self._log_line(message)
