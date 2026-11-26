"""GTK4 front end: the Application core and the window scaffolding.

The page code lives in one mixin per page (``gui_bazar``, ``gui_map``,
``gui_bot``, ``gui_family``, ``gui_minigame``, ``gui_stats``); every method
still runs on the single Application instance. ``guicommon`` holds the
constants and row models all of them share.

The GTK loop and the socket reader never share a thread: the reader pushes into
a ``queue.Queue`` and a 50 ms GLib timeout drains it on the main loop. The bot
runs on the same GLib main loop and only sends packets through the client; it
never touches GTK from a worker thread.
"""


import os
import queue
from collections import deque
from collections.abc import Callable
import threading
import time
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import (
    Gdk,
    Gio,
    GLib,
    Gtk,
)

from . import __version__
from .bazar import MONITOR_SOURCES, BazarController, default_config_path as default_bazar_config_path
from .client import ConnectionLost, PacketClient
from .dpsmeter import DpsMeter, DpsRow
from .clients import (
    ClientInfo,
    Discovery,
    acquire_client_lock,
    client_lock_owner,
    discover_clients,
    inject_client,
    read_port,
    release_client_lock,
    wait_for_port,
)
from .engine import Engine, InventorySlot
from .mapdata import (
    DEFAULT_NSTC,
    MapGrid,
    load_portals as load_saved_portals,
    normalize_portals,
)
from .minigame import DEFAULT_TARGET_LEVEL, QUARRY_VNUM, MinigameConfig
from .mirror import MirrorReader, MirrorWriter
from .nosmall import ItemInfo
from .spawnlog import client_log_path
from .spells import SpellInfo
from .protocol import Kind, Packet, ProtocolError

from .gui_bazar import BazarPageMixin
from .gui_bot import BotPageMixin
from .gui_family import FamilyPageMixin
from .gui_map import MapPageMixin
from .gui_minigame import MinigamePageMixin
from .gui_stats import StatsPageMixin

from .guicommon import (
    MAX_ROWS,
    TRIM_ROWS,
    LOG_MAX_LINES,
    LOG_TRIM_LINES,
    BOT_LOG_OVERRIDE,
    BOT_LOG_PATH,
    BOT_LOG_MAX_BYTES,
    DRAIN_INTERVAL_MS,
    FAMILY_AUTO_INTERVAL_S,
    FAMILY_CONFIRM_TIMEOUT_S,
    FAMILY_COOLDOWN_S,
    ATTACK_WALK_DELAY,
    MAX_MOVE_LEG_CELLS,
    MAX_CANCEL_STREAK,
    TARGET_FAIL_WINDOW_S,
    TARGET_FAIL_TRIGGER,
    TARGET_FAIL_PAUSE_S,
    MAX_REJECTED_WALKS,
    RECONNECT_DELAY,
    POS_COMMAND,
    TARGET_BLOCK_S,
    PacketRow,
    BazarItemRowView,
    _column,
)


__all__ = [
    "Application",
    "LOG_MAX_LINES",
    "MAX_CANCEL_STREAK",
    "MAX_MOVE_LEG_CELLS",
    "MAX_REJECTED_WALKS",
    "discover_clients",
    "run_gui",
]


class Application(
    BazarPageMixin,
    StatsPageMixin,
    MapPageMixin,
    FamilyPageMixin,
    MinigamePageMixin,
    BotPageMixin,
    Gtk.Application,
):
    def __init__(
        self,
        host: str,
        port: int | None,
        map_data: str | None = None,
        unique: bool = True,
        log_file: str | None = None,
        select_client: bool = False,
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
        # Handler failures do not stop the pump; the first one per opcode
        # goes to the Bot log (see _on_packet_error).
        self.packet_error_count = 0
        self.packet_error_opcodes: set[str] = set()
        # Startup client selector: the window lists the running game windows
        # and injects/attaches to the chosen one instead of always dialing the
        # default port. Off by default so headless tests stay deterministic.
        self.select_client = select_client
        self.client_label: str | None = None
        self.dll_version: str | None = None
        # The game window this GUI attached to; its attach lock keeps a second
        # GUI from dialing the same DLL (the logger serves one host).
        self.attached_client: ClientInfo | None = None
        self._client_generation = 0
        self._drain_timer_started = False
        self._client_scan = 0
        self._client_dialog = None
        self._client_dialog_rows: list[ClientInfo] = []
        self._client_dialog_list = None
        self._client_dialog_action = None
        self._client_dialog_status = None
        self._inject_client: ClientInfo | None = None
        self.discovery: Discovery | None = None

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
        self.map_source: str | None = None
        self.portals = normalize_portals(load_saved_portals())
        # `wp` Time-Space entrances: (map, x, y) -> (ts_id, minLv, maxLv).
        self.timespaces: dict[tuple[int, int, int], tuple[int, int, int]] = {}
        self._timespace_icon: cairo.ImageSurface | None = None
        self._timespace_icon_loaded = False
        self.monster_names: dict[int, str] = {}
        self.spell_descriptions: dict[int, str] = {}
        self.spells: dict[int, SpellInfo] = {}
        self.spells_loaded = False
        self.spells_loading = False
        self.monster_names_loaded = False
        # An explicit --log / NTPL_LOG wins; otherwise the per-client log is
        # chosen when a client is attached (each DLL writes its own file).
        self.log_file_override = log_file or os.environ.get("NTPL_LOG")
        self.log_file = self.log_file_override
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
        self.combat_info = None
        self._combat_info_text = ""
        self.mp_warn_at = 0.0
        self.slot_warn_at = 0.0
        self._target_map_vnums: frozenset[int] | None = None
        # TARGET support is expected from the current DLL; an old DLL answers
        # "unknown command" and the bot falls back to an injected ncif.
        self.dll_target_supported = True
        self.last_target_reply = ""
        # A TARGET the client refused (dead, off-map, ...) blocks that entity
        # for a few seconds so the bot picks another monster instead of
        # attacking with a stale selection.
        self.target_blocked: dict[int, float] = {}
        self.auto_attack_toggle = None
        self.loop_toggle = None
        self.draw_path_toggle = None
        self.block_portal_toggle = None
        # Map portals that must not be stepped on (Block portals); a stale
        # guard message is repeated at most every two seconds.
        self.portal_warn_at = 0.0
        # Gate cells seen on the current map, to replan a drawn route when a
        # `gp` burst arrives after it.
        self._gate_cells_seen: frozenset[tuple[int, int]] = frozenset()
        self.pickup_toggle = None
        self.recover_toggle = None
        self.recover_mp_toggle = None
        self.revive_toggle = None
        self.revive_sent_at = 0.0
        self.follow_player_toggle = None
        self.follow_player_entry = None
        self.follow_attack_check = None
        self.follow_mirror_check = None
        # Flat client-side movement-speed bonus (0 = off), added by the DLL to
        # the local player's `cond` speed; see the Speed + control.
        self.speed_boost_spin = None
        self._speed_boost_source = None
        # True while the DLL reports the server's accepted limit and the spin
        # is updated from it: that must not send a new SPEED command.
        self._speed_tuning = False
        # Client-side cast speed switch (on = zero the skill cooldowns).
        self.cast_boost_switch = None
        # Cast speed assist (no bot): the last skill the player pressed is
        # re-sent whenever the server says its cooldown ended, so one key
        # press keeps that spell/attack going at the server's fastest rate.
        self.assist_packet: str | None = None
        self.assist_slot: int | None = None
        self.assist_seen_at = 0.0
        self.assist_started_at = 0.0
        self.assist_status_at = 0.0
        # Host `SEND|u_s ...` echoes; they are not key presses.
        self._host_skill_sends: list[tuple[int, float, str]] = []
        # Every attached host publishes its own `walk`/`u_s` to the mirror
        # bus; a follower replays the followed character's actions with its
        # own char id substituted. The server's `mv`/`su` broadcasts are the
        # fallback when the followed host does not publish.
        self.mirror_writer: MirrorWriter | None = None
        self.mirror_reader = MirrorReader()
        self.mirror_player_id: int | None = None
        self.mirror_move: tuple[int, int] | None = None
        self.mirror_move_at = 0.0
        self.mirror_casts: list[tuple[int, int, int, int, float]] = []
        self.mirror_recent_casts: dict[tuple[int, int, int], float] = {}
        self.mirror_bus_seen_at = 0.0
        self.mirror_poll_at = 0.0
        self.mirror_warn_at = 0.0
        # Slot -> vnum learned from the followed client's published spells.
        # The follower's own `ski` list is often missing (the GUI attached
        # after login); with the same SP card the leader's slot is valid.
        self.mirror_slot_vnums: dict[int, int] = {}
        self.mirror_slot_owner: int | None = None
        self.mirror_slot_warn_at = 0.0
        # Throttle for the "player not in sight" status: the follow tick runs
        # many times per second and used to write the same line every tick,
        # flooding the Bot log and its file.
        self.follow_warn_at = 0.0
        # player char id -> (monster id they last hit, monotonic time). Fed by
        # the server's `su` packets; only players' monster hits are kept.
        self.player_targets: dict[int, tuple[int, float]] = {}
        self.attack_unknown_toggle = None
        self.recover_banner = None
        self.char_name = None
        self.unknown_warn_at = 0.0

        self.waypoints: list[tuple[int, int]] = []
        self.path_cells: list[tuple[int, int]] = []
        self.bot_goals: list[tuple[int, int]] = []
        self.path_index = 0
        self.pending_target: tuple[int, int] | None = None
        # True for a drawn-path leg (advance path_index on arrival), False
        # for a loot or chase leg (resume the path where it was).
        self.pending_is_path = False
        self.pending_loot_id: int | None = None
        self.pending_since = 0.0
        self.pending_until = 0.0
        # The last MOVE the bot issued; a client walk or server `tp` to any
        # other cell while the bot runs is a manual interruption, so the bot
        # re-plans from the new position instead of waiting or stopping.
        self.command_target: tuple[int, int] | None = None
        self.command_at = 0.0
        self.external_move = False
        self.external_move_position: tuple[int, int] | None = None
        self.replan_at = 0.0
        # The DLL's real player position (POS), preferred for the own marker
        # so the map follows the animation instead of the walk packet's
        # destination.
        self.pos_cell: tuple[int, int] | None = None
        self.pos_at = 0.0
        # The last POS read error, so a build-mismatched DLL that polls twice
        # a second logs it once instead of flooding the Bot log.
        self._pos_error_text = ""
        # Click-to-walk: short client-MoveTo legs along the A* path, so a far
        # click does not become one long MOVE that the client jumps.
        self.travel_targets: list[tuple[int, int]] = []
        self.travel_index = 0
        self.travel_pending: tuple[tuple[int, int], float] | None = None
        self.travel_timer = None
        self.travel_on_arrival = None
        self._travel_pos_at = 0.0
        # Draw path: the first click sets the route's start; Start walks the
        # character there first, then follows the drawn waypoints.
        self.path_start: tuple[int, int] | None = None
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
        self.recovery_pending = False
        self.recover_need_hp = False
        self.recover_need_mp = False
        self.recover_rest_at = 0.0
        # The monster the bot is currently hitting; recovery waits until it
        # dies so a low bar never abandons a half-killed target.
        self.fight_target_id = None
        # Attack pacing / cancel retry. Every attack is queued as
        # [sent_at, landed, target_id]; the server's `su`/`cancel` replies are
        # matched to the oldest queued cast in send order. A `cancel` after a
        # landed `su` is the normal end of the cast, not a refusal.
        self.last_attack_sent_at = 0.0
        self.last_cancel_count = 0
        self.pending_casts: list[list] = []
        self.hit_marks_seen = 0
        # Target id -> last time a cast that landed a `su` was resolved. A
        # target that has been hit is real; a refused cast must not drop it
        # as a ghost (that only applies to never-hit client-only entities).
        self.hit_target_at: dict[int, float] = {}
        self.cancel_warn_at = 0.0
        self.cancel_streak = 0
        self.cancel_streak_target: int | None = None
        self.cancel_events: list[float] = []
        self.target_fail_events: list[float] = []
        self.combat_paused_until = 0.0
        self.combat_pause_pos: tuple[int, int] | None = None
        self.dead_warned = False
        self.map_redraw_scheduled = False
        self.stats_redraw_scheduled = False
        self.bot_running = False
        self.start_when_located = False
        self.bot_timer = None
        self._closing = False
        # Top-level "Bot log" tab: every action the status bar shows, with
        # timestamps, so a fight can be debugged after the fact.
        self.bot_log_view = None
        self.bot_log_buffer = None
        self._bot_log_lines = 0
        self._bot_log_scroll_scheduled = False
        self._bot_log_file = None
        self.bot_log_path = BOT_LOG_PATH

        # NosBazar tab: a pure controller lives here, the tab is only a view.
        # The controller drives the packet flow from a dedicated 100 ms timer
        # and reads bazaar/inventory state from the engine's world.
        self.bazar = BazarController(
            self._bazar_send,
            self._bazar_log,
            item_label=self._bazar_item_label,
            vnum_name=self._bazar_vnum_name,
        )
        self.bazar_timer = None
        self.bazar_page = None
        self.bazar_config_path = default_bazar_config_path()
        self.bazar_config_label = None
        self.bazar_info = None
        self.bazar_results_box = None
        # The Monitor section's third-level views: NosMall (ND/no cost) and
        # NPC resale (gold cost). Each view owns its table and Only priced
        # filter; the singular attributes above point at the active view for
        # older tests and the item-name refresh path.
        self.bazar_results_boxes: dict[str, Gtk.Box] = {}
        self.bazar_results_infos: dict[str, Gtk.Label] = {}
        self.bazar_only_priced_checks: dict[str, Gtk.CheckButton] = {}
        self.bazar_monitor_notebook = None
        self.bazar_monitor_search = None
        self.bazar_monitor_items_list = None
        self.bazar_monitor_items_scroll = None
        self.bazar_monitor_items_info = None
        self.bazar_monitor_group_button = None
        self.bazar_monitor_select_all_button = None
        self.bazar_monitor_unselect_all_button = None
        self._bazar_monitor_selected: set[str] = set()
        self.bazar_monitor_selected_name: str | None = None
        self._bazar_monitor_render_version = -1
        self.item_db_window = None
        self._item_db_list = None
        self._item_db_search = None
        self._item_db_status = None
        self._item_db_matches: list = []
        self._item_db_mode = "monitor"
        self._item_db_add_all_button = None
        self._item_db_armed_at = 0.0
        self._item_db_armed_count = 0
        self._bazar_relist_inventory_armed_at = 0.0
        self._bazar_monitor_inventory_armed_at = 0.0
        self._bazar_monitor_sources = MONITOR_SOURCES
        self.bazar_items_view = None
        self.bazar_items_store = None
        self.bazar_items_selection = None
        self.bazar_items_filter = None
        self.bazar_items_scroll = None
        self.bazar_items_info = None
        self.bazar_only_priced = None
        self.bazar_results_info = None
        self.bazar_form: dict[str, Gtk.Widget] = {}
        self.bazar_selected_name: str | None = None
        #: A row from the inventory that is not configured yet (the unified
        #: Items list); the editor shows a draft until Save creates it.
        self._bazar_selected_slot_vnum: int | None = None
        #: True while a render re-selects the remembered row; that must not
        #: refill the editor and overwrite unsaved values.
        self._bazar_restoring_selection = False
        self.nosmall_window = None
        self._nosmall_groups: list = []
        self._nosmall_status = None
        self._nosmall_list = None
        self._nosmall_matches: list = []
        self._nosmall_add_all_button = None
        self._nosmall_search = None
        # Two-step confirmation for the destructive cleanup button.
        self._nosmall_prune_armed_at = 0.0
        self._nosmall_prune_count = 0
        self.bazar_refresh_spin = None
        self.bazar_npc_entry = None
        self.bazar_character_entry = None
        # Second-level navbar: Items, Relist, Monitor, Config.
        self.bazar_section_notebook = None
        self._bazar_section_index: dict[str, int] = {}
        self.bazar_relist_list = None
        self.bazar_relist_scroll = None
        self.bazar_relist_info = None
        self.bazar_relist_editor_label = None
        self.bazar_relist_hint = None
        self.bazar_relist_form: dict[str, Gtk.Widget] = {}
        self.bazar_relist_form_box = None
        self.bazar_relist_save_button = None
        self.bazar_relist_open_button = None
        self.bazar_relist_selected: str | None = None
        # A second click on the only selected row unselects it; the press
        # handler records whether it was the sole selection before the
        # ListBox updates it.
        self._relist_press_row = None
        self._relist_press_only = False
        # Relist tracking controls: the "Only relisting" filter and the
        # cross-section buttons that add or remove an item from the set.
        self.bazar_relist_filter = None
        self.bazar_relist_add_items_button = None
        self.bazar_relist_add_inventory_button = None
        self.bazar_relist_remove_button = None
        self.bazar_relist_group_button = None
        self.bazar_relist_select_all_button = None
        self.bazar_relist_unselect_all_button = None
        self._bazar_render_version = -1
        self._bazar_character_autofilled = False
        #: The self name was looked for in the whole DLL log once per attach
        #: (the login spawn can be older than the log tail).
        self._self_name_scanned = False
        self._bazar_npc_autofilled = False
        self._bazar_npc_autofill_value = ""
        self._bazar_config_loaded = False
        # Inventory integration: every slot the client reports, with the
        # Item.dat name/NPC price for the picker. Item info loads once in a
        # worker thread because the archive parse is too slow for the loop.
        self.bazar_inventory_info = None
        self.bazar_price_all_button = None
        self._inventory_list = None
        self._inventory_scroll = None
        self._inventory_status = None
        self._inventory_search = None
        self._inventory_add_button = None
        self._inventory_add_all_button = None
        self._inventory_setup_button = None
        self._inventory_checks: set[tuple[int, int]] = set()
        self._inventory_matches: list[InventorySlot] = []
        self._inventory_render_pending = False
        # The last complete inventory is cached per character id, so a GUI
        # attach after the DLL's 16 MB log truncation still has the data.
        self._inventory_cache_loaded = False
        self._inventory_cache_note = ""
        self._inventory_cache_source: int | None = None
        # Set only by live inv/ivn packets or a log backfill, so loading a
        # snapshot does not rewrite its "saved_at" as if it were fresh.
        self._inventory_cache_dirty = False
        # Set by the attach-time log seed; the cache is only a fallback for
        # sessions where neither the live stream nor the log had the dump.
        self._inventory_seeded = False
        self._inventory_log_truncated = False
        # Bags that arrived as a full `inv` dump (live or log replay); the
        # cached note clears once the core login burst (0/1/2) is seen.
        self._inventory_full_bags: set[int] = set()
        # Only a complete login inventory (bags 0, 1 and 2) is cached; a
        # later SP/costume bag burst must not replace it with a partial list.
        self._inventory_core_seen = False
        self.item_info: dict[int, ItemInfo] = {}
        self.item_info_loaded = False
        self._item_info_loading = False
        self._item_info_retry_at = 0.0
        self._item_info_callbacks: list = []
        # Family tab: the invite form, auto-invite and the invited list.
        self.family_name_entry = None
        self.family_invite_button = None
        self.family_auto_toggle = None
        self.family_only_fairy = None
        self.family_invited_info = None
        self.family_invited_list = None
        self.family_invited_scroll = None
        self.family_page = None
        self.packet_page = None
        self.packets_skipped_while_hidden = 0
        self._hidden_packets: deque[Packet] = deque(maxlen=500)
        self._packet_scroll_scheduled = False
        self.family_timer_label = None
        self.family_players_info = None
        self.family_players_list = None
        self.family_players_scroll = None
        self.family_status = None
        self.family_state_path: Path | None = None
        self.family_invite_interval = FAMILY_AUTO_INTERVAL_S
        self.family_confirm_timeout = FAMILY_CONFIRM_TIMEOUT_S
        # The live server blocks invites sent too close together (infoi 2359).
        # Successful test invites were accepted 12.45 s apart; 15 s is safe
        # and is also shown as a countdown in the tab.
        self.family_cooldown_s = FAMILY_COOLDOWN_S
        self._family_next_invite_at = 0.0
        self._family_pending: tuple[str, float] | None = None
        self._family_invited: dict[str, str] = {}
        self._family_no_reply: dict[str, str] = {}
        self._family_no_reply_streak = 0
        self._family_auto_timer: int | None = None
        self._family_auto_guard = False
        self._family_only_fairy_guard = False
        self._family_auto_last = 0.0
        self._family_status_hold_until = 0.0
        self._family_players_sig: tuple | None = None
        self._family_players_next_at = 0.0
        self._family_invited_next_at = 0.0
        self._family_invited_dirty = False
        self._family_timer_source: int | None = None
        # Stats tab: a player DPS meter fed by every `su` packet (attacker
        # type 1, positive damage). The table only refreshes while the page
        # is visible; the meter collects all the time.
        self.dps_meter = DpsMeter()
        self.dps_page = None
        self.dps_store = None
        self.dps_info = None
        self.dps_window = None
        self.dps_names: dict[int, str] = {}
        self.dps_last_rows: list[DpsRow] = []
        self._dps_timer: int | None = None
        # Minigame tab: Sheep Farm key spam and player-only focus. The config
        # is per attached client, like the Family list and NosBazar settings.
        self.minigame_page = None
        self.minigame_toggle = None
        self.minigame_key_entry = None
        self.minigame_interval_spin = None
        self.minigame_focus_switch = None
        self.minigame_radius_spin = None
        self.minigame_status = None
        self.minigame_config = MinigameConfig()
        self.minigame_config_path: Path | None = None
        self.minigame_running = False
        self.minigame_timer: int | None = None
        # Miniland production minigame (Great Quarry): a short start → result
        # → gift packet sequence, confirmed by the server's mlo_mg.
        self.quarry_running = False
        self.quarry_timer: int | None = None
        self.quarry_step = 0
        self.quarry_vnum = QUARRY_VNUM
        self.quarry_slot = 1
        self.quarry_level = DEFAULT_TARGET_LEVEL
        self.quarry_target = 0
        self.quarry_seen = 0
        self.quarry_seen_status = 0
        self.quarry_reward_seen = 0
        self.quarry_claim_level = 0
        self.quarry_reward_before: int | None = None
        # Auto: Play repeats the claim until the production points run out
        # (100 per claim, 2,000 a day).
        self.quarry_auto_running = False
        self.quarry_auto_done = 0
        #: Consecutive runs the server ignored (a reward cooldown); reset on
        #: a confirmed claim.
        self.quarry_auto_retries = 0
        #: Monotonic time of the next Auto claim (0 while a claim is
        #: running or Auto is idle); drives the UI countdown and the
        #: claim start in the 1 s Minigame tick.
        self.quarry_auto_next_at = 0.0
        self.quarry_points_before: int | None = None
        self.quarry_deadline = 0.0
        self.minigame_guard = False
        self.minigame_focus_id: int | None = None
        self.minigame_focus_name = ""
        self.minigame_focus_refresh_at = 0.0
        self.minigame_focus_blocked: dict[int, float] = {}
        # TARGET replies can arrive after the focus moved on; remember every
        # id the tab asked for so a late ERR is never treated as combat.
        self.minigame_target_requests: dict[int, float] = {}
        self.minigame_next_key = 0.0
        self.minigame_next_focus = 0.0
        self.minigame_next_render = 0.0
        # An old DLL answers "unknown command: KEY" and the tab stops.
        self.dll_key_supported = True
        # Do not create a stray minigame-<pid>.json for a window that never
        # used the tab; an existing file is still kept by the shutdown save.
        self._minigame_touched = False
        # Right-click "Copy item name" in the NosBazar items/results lists.
        self._install_copy_action()

    def _install_copy_action(self) -> None:
        action = Gio.SimpleAction.new(
            "copy-item-name", GLib.VariantType.new("s")
        )
        action.connect("activate", self._on_copy_item_name)
        self.add_action(action)

    def _on_copy_item_name(self, _action, parameter) -> None:
        try:
            name = parameter.get_string()
        except AttributeError:
            return
        self._copy_text(name)

    def _copy_text(self, text: str, what: str = "item name") -> None:
        """Put text on the system clipboard and confirm what it was."""
        text = (text or "").strip()
        if not text:
            return
        display = Gdk.Display.get_default()
        if display is None:
            return
        try:
            provider = Gdk.ContentProvider.new_for_bytes(
                "text/plain;charset=utf-8",
                GLib.Bytes.new(text.encode("utf-8")),
            )
            display.get_clipboard().set_content(provider)
        except (AttributeError, TypeError, GLib.Error):
            self._set_status("cannot access the clipboard")
            return
        self._set_status(f"copied {what}: {text}")

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
        if self.select_client:
            self._begin_client_selection(auto_single=True)
        else:
            # No per-client key without the picker; still honour an explicit
            # NTPL_BAZAR_CONFIG / bazar.json so the tab can be prepared before
            # attaching.
            self._bazar_load_default_config()
            self._start_client()

    def _build_ui(self) -> None:
        window = Gtk.ApplicationWindow(
            application=self,
            title=f"NosTale Packet Logger {__version__}",
        )
        window.set_default_size(1280, 820)
        self.window = window

        header = Gtk.HeaderBar()
        window.set_titlebar(header)

        client_button = Gtk.Button(label="Client…")
        client_button.set_tooltip_text(
            "List the running NosTale clients and attach to one of them"
        )
        client_button.connect(
            "clicked", lambda *_: self._begin_client_selection(auto_single=False)
        )
        header.pack_start(client_button)

        packet_page = self._build_packet_page()
        self.packet_page = packet_page
        map_page = self._build_map_page()
        log_page = self._build_log_page()
        bazar_page = self._build_bazar_page()
        family_page = self._build_family_page()
        self.family_page = family_page
        malicious_page = self._build_malicious_page()
        minigame_page = self._build_minigame_page()
        stats_page = self._build_stats_page()

        self.notebook = Gtk.Notebook()
        self.notebook.append_page(packet_page, Gtk.Label(label="Packets"))
        self.notebook.append_page(map_page, Gtk.Label(label="Map / Bot"))
        self.notebook.append_page(log_page, Gtk.Label(label="Bot log"))
        # Appended after the core tabs: the GUI smokes and the README shots
        # assume Map / Bot is page 1 (Packets is 0).
        self.notebook.append_page(bazar_page, Gtk.Label(label="NosBazar"))
        self.notebook.append_page(family_page, Gtk.Label(label="Family"))
        self.notebook.append_page(malicious_page, Gtk.Label(label="Malicious"))
        # Appended last: the screenshots and existing smokes address pages by
        # index, and the core tabs keep their numbers.
        self.notebook.append_page(minigame_page, Gtk.Label(label="Minigame"))
        # Stats follows the same rule: appended last, so existing page
        # numbers (smokes, README shots) keep their meaning.
        self.notebook.append_page(stats_page, Gtk.Label(label="Stats"))
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

        # Spawns first: the log also seeds the map id and position, so the
        # map page can select the right grid immediately.
        self._ensure_spawns_loaded()
        self._ensure_map_loaded()
        self._ensure_monster_names()
        # Skill.dat decodes 1.2 MB of nibble-coded text (~1.5 s). Do it off
        # the main loop so the window and the packet drain stay responsive.
        self._ensure_spells_loaded()
        GLib.timeout_add(250, self._layout_tick)
        GLib.timeout_add(2000, self._auto_map_tick)
        GLib.timeout_add(1000, self._live_sidebar_tick)
        GLib.timeout_add(500, self._poll_position)

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

        # The table belongs to this tab; a window-level Clear suggested it
        # cleared whichever tab was open.
        clear_button = Gtk.Button(label="Clear")
        clear_button.set_tooltip_text(
            "Empty the packet table; the session log stays on disk."
        )
        clear_button.connect("clicked", lambda *_: self.store.remove_all())
        toolbar.append(clear_button)

        self.follow = Gtk.ToggleButton(label="Follow")
        self.follow.set_active(True)
        self.follow.set_tooltip_text("Scroll to the newest packet automatically")
        toolbar.append(self.follow)
        toolbar.append(
            self._help_button(
                "Packets",
                "Captured client traffic, newest first. The filter matches "
                "opcode or packet text. Send writes a raw packet through the "
                "DLL. Follow keeps the newest row in view. Clear empties the "
                "table; the session log stays on disk.",
            )
        )

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.append(toolbar)
        page.append(self.scrolled)
        return page

    def _help_button(self, title: str, text: str) -> Gtk.Button:
        """A compact ? button whose popover explains one section.

        UI standard: explanations live here, never as paragraphs in the
        layout. Live values (positions, counts, states) stay as labels.
        A plain Button (not MenuButton) keeps the label to a single ?.
        """
        button = Gtk.Button(label="?")
        button.add_css_class("ntpl-help")
        button.set_tooltip_text(f"What {title} does")
        button.set_valign(Gtk.Align.CENTER)

        popover = Gtk.Popover()
        popover.set_has_arrow(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        heading = Gtk.Label()
        heading.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
        heading.set_xalign(0)
        body = Gtk.Label(label=text)
        body.set_wrap(True)
        body.set_max_width_chars(46)
        body.set_xalign(0)
        box.append(heading)
        box.append(body)
        popover.set_child(box)
        popover.set_parent(button)
        button.ntpl_help_popover = popover

        def toggle(*_args) -> None:
            if popover.get_visible():
                popover.popdown()
            else:
                popover.popup()

        button.connect("clicked", toggle)
        button.connect("destroy", lambda *_: popover.unparent())
        return button

    def _frame(
        self,
        title: str,
        child: Gtk.Widget,
        help_text: str | None = None,
    ) -> Gtk.Frame:
        if help_text is None:
            frame = Gtk.Frame(label=title)
        else:
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            label = Gtk.Label(label=title)
            label.set_xalign(0)
            header.append(label)
            header.append(self._help_button(title, help_text))
            frame = Gtk.Frame()
            frame.set_label_widget(header)
        frame.set_child(child)
        return frame

    @staticmethod
    def _field_row(*widgets: Gtk.Widget) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for widget in widgets:
            row.append(widget)
        return row

    def _menu_button(
        self,
        label: str,
        tooltip: str,
        entries: tuple[tuple[str, Callable[[], None]] | None, ...],
    ) -> Gtk.MenuButton:
        """A menu button whose popover holds flat action buttons.

        ``None`` inserts a separator. Clicking an entry closes the popover
        before running the action, so a dialog cannot open underneath a menu
        that is still up. The created buttons are kept on
        ``button.ntpl_entries`` for callers that need to drive or test one.
        """
        button = Gtk.MenuButton(label=label)
        button.set_tooltip_text(tooltip)
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        created: dict[str, Gtk.Button] = {}
        for entry in entries:
            if entry is None:
                box.append(Gtk.Separator())
                continue
            text, callback = entry
            item = Gtk.Button(label=text)
            item.add_css_class("flat")
            item.set_halign(Gtk.Align.FILL)
            item.connect(
                "clicked",
                lambda _button, cb=callback: (popover.popdown(), cb()),
            )
            created[text] = item
            box.append(item)
        popover.set_child(box)
        button.set_popover(popover)
        # Attribute kept on the instance; mypy ignores PyGObject shadowing.
        button.ntpl_entries = created  # type: ignore[attr-defined]
        return button

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

    def _build_log_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_top(6)
        toolbar.set_margin_start(6)
        toolbar.set_margin_end(6)
        clear = Gtk.Button(label="Clear log")
        clear.connect("clicked", lambda *_: self._clear_bot_log())
        toolbar.append(clear)
        toolbar.append(
            self._help_button(
                "Bot log",
                "Timestamped bot actions, target selections, walks, pickups, "
                "recovery and DLL errors, newest at the bottom. The same "
                "lines are mirrored to a per-client log file on disk.",
            )
        )
        box.append(toolbar)

        self.bot_log_buffer = Gtk.TextBuffer()
        view = Gtk.TextView(buffer=self.bot_log_buffer)
        view.set_editable(False)
        view.set_cursor_visible(False)
        view.set_monospace(True)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_left_margin(8)
        view.set_right_margin(8)
        view.set_top_margin(4)
        view.set_bottom_margin(4)
        self.bot_log_view = view

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(view)
        box.append(scrolled)

        self._open_bot_log_file()
        return box

    def _open_bot_log_file(self) -> None:
        path = self.bot_log_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > BOT_LOG_MAX_BYTES:
                path.replace(path.with_name(path.name + ".1"))
            self._bot_log_file = path.open(
                "a", encoding="utf-8", buffering=1
            )
            self._bot_log_file.write(
                f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} GUI session =====\n"
            )
        except OSError:
            self._bot_log_file = None

    def _switch_bot_log(self, winpid: int) -> None:
        """Move the tab's file to this client's own bot-<winpid>.log.

        Called once the attached client is known. The per-client logs mirror
        the per-client DLL logs; a shared file from several GUIs interleaves
        two bots' actions and looks like one bot switching targets.
        """
        path = BOT_LOG_PATH.with_name(f"bot-{winpid}.log")
        if path == self.bot_log_path:
            return
        if self._bot_log_file is not None:
            try:
                self._bot_log_file.close()
            except OSError:
                pass
            self._bot_log_file = None
        self.bot_log_path = path
        self._open_bot_log_file()

    def _log_line(self, text: str) -> None:
        """Append one timestamped action line to the Bot log tab."""
        buffer = self.bot_log_buffer
        text = text.strip()
        if buffer is None or not text:
            return
        stamp = time.strftime("%H:%M:%S")
        if self._bot_log_lines:
            buffer.insert(buffer.get_end_iter(), "\n")
        buffer.insert(buffer.get_end_iter(), f"{stamp}  {text}")
        if self._bot_log_file is not None:
            try:
                self._bot_log_file.write(f"{stamp}  {text}\n")
            except OSError:
                self._bot_log_file = None
        self._bot_log_lines += 1
        if self._bot_log_lines > LOG_MAX_LINES:
            trim = buffer.get_iter_at_line(LOG_TRIM_LINES)
            # GTK's C signature has two out parameters (found, iter) and this
            # PyGObject build returns them as a pair. Treating the pair as an
            # iter raised TypeError on every status update once the log
            # reached the cap, which silently froze the bot loop.
            if isinstance(trim, tuple):
                trim = trim[1]
            buffer.delete(buffer.get_start_iter(), trim)
            self._bot_log_lines -= LOG_TRIM_LINES
        if not self._bot_log_scroll_scheduled:
            self._bot_log_scroll_scheduled = True
            GLib.idle_add(self._scroll_bot_log)

    def _scroll_bot_log(self) -> bool:
        self._bot_log_scroll_scheduled = False
        if self.bot_log_view is not None:
            buffer = self.bot_log_view.get_buffer()
            mark = buffer.create_mark(None, buffer.get_end_iter(), False)
            self.bot_log_view.scroll_mark_onscreen(mark)
            buffer.delete_mark(mark)
        return False

    def _clear_bot_log(self) -> None:
        if self.bot_log_buffer is not None:
            self.bot_log_buffer.set_text("")
            self._bot_log_lines = 0

    def _build_bazar_relist_section(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.set_margin_top(6)
        page.set_margin_bottom(6)
        page.set_margin_start(6)
        page.set_margin_end(6)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        # The character is detected from packets (self spawn, party list,
        # seller-list owner id), so the Relist UI never asks for a name.  The
        # entry stays as an internal fallback for the config/older callers
        # and is deliberately not added to the layout.
        self.bazar_character_entry = Gtk.Entry()
        self.bazar_character_entry.set_placeholder_text("your character")
        self.bazar_character_entry.set_max_length(40)
        self.bazar_character_entry.set_width_chars(18)
        self.bazar_character_entry.connect(
            "changed", lambda *_: self._bazar_update_relist_info()
        )

        self.bazar_relist_info = Gtk.Label(label="")
        self.bazar_relist_info.set_xalign(0)
        self.bazar_relist_info.set_wrap(True)
        self.bazar_relist_info.add_css_class("dim-label")
        content.append(self.bazar_relist_info)

        body = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        body.set_vexpand(True)
        body.set_wide_handle(True)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        left.set_size_request(380, -1)
        left.set_vexpand(True)
        left_info = Gtk.Label(label="Relist items")
        left_info.set_xalign(0)
        left_info.add_css_class("dim-label")
        left.append(left_info)
        self.bazar_relist_list = Gtk.ListBox()
        # Multiple selection so one group action can cover several rows;
        # the compact editor follows the row selected last.
        self.bazar_relist_list.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        self.bazar_relist_list.set_activate_on_single_click(True)
        self.bazar_relist_list.connect(
            "row-selected", self._bazar_relist_item_selected
        )
        self.bazar_relist_list.connect(
            "row-activated", self._bazar_relist_row_activated
        )
        # A capture-phase press runs before the ListBox selection handler,
        # so we know whether the click landed on the sole selected row.
        press = Gtk.GestureClick()
        press.set_button(1)
        press.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        press.connect("pressed", self._bazar_relist_pressed)
        self.bazar_relist_list.add_controller(press)
        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_child(self.bazar_relist_list)
        list_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        list_scroll.set_vexpand(True)
        self.bazar_relist_scroll = list_scroll
        left.append(list_scroll)
        left.append(self._build_bazar_relist_tracking())
        body.set_start_child(left)
        body.set_resize_start_child(True)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right.set_vexpand(True)
        right.append(self._build_bazar_relist_editor())
        body.set_end_child(right)
        body.set_resize_end_child(False)
        content.append(body)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        relist_start = Gtk.Button(label="Relist Start")
        relist_start.add_css_class("destructive-action")
        relist_start.set_tooltip_text(
            "Relist one gold below the cheapest other seller, never below min price."
        )
        relist_start.connect("clicked", lambda *_: self._bazar_start_relist())
        buttons.append(relist_start)
        relist_stop = Gtk.Button(label="Relist Stop")
        relist_stop.connect("clicked", lambda *_: self._bazar_stop())
        buttons.append(relist_stop)
        content.append(buttons)

        relist_frame = self._frame(
                "Relisting",
                content,
                help_text=(
                    "The list is the relist set: tick an item to include "
                    "it, untick it to stop changing that listing while the "
                    "Monitor keeps watching it. The list shows only the "
                    "relist set by default (Only relisting is on); choose "
                    "items with the Relist tick in the Items section. Add "
                    "from Items takes the Items selection; Add… puts every "
                    "distinct bag item into the set (second click "
                    "confirms) or searches all of Item.dat and adds the "
                    "picks. Actions… removes the selected rows from "
                    "relisting (the item and its floor stay), selects or "
                    "clears the visible rows, and applies relist / stop "
                    "relisting to every row shown or to the Ctrl+clicked "
                    "rows. Only relisting hides the rest of the list. "
                    "Select an item, set the "
                    "exact Item vnum, the inventory Tab and the Amount, "
                    "then a positive Min price (the per-unit floor). "
                    "Your own listings are recognised by character id (or "
                    "the name from the client's spawn/party list), so there "
                    "is no name field to fill. Relist Start needs nothing "
                    "else: it "
                    "searches only the relist set and sends one c_reg per "
                    "eligible item per pass at cheapest minus one gold, "
                    "but never below the floor. If you already have a "
                    "listing of that item with nothing sold, the pass "
                    "changes its price with the in-game Change action "
                    "(c_mod) instead of adding another; once anything from "
                    "a listing has sold the Change action is gone and a new "
                    "listing is added (c_reg). The server answers an "
                    "accepted c_reg with rc_reg, so the status reads "
                    "register accepted (rc_reg); no answer within 3 s means "
                    "the packet was ignored (register ignored (no rc_reg)), "
                    "and Relist Start repairs the item constants from "
                    "Item.dat on the next try. Verify the listing in the "
                    "bazaar. Items "
                    "without a vnum, tab or positive floor are skipped."
                ),
        )
        relist_frame.set_vexpand(True)
        page.append(relist_frame)
        return page

    def _build_bazar_relist_tracking(self) -> Gtk.Widget:
        """One compact toolbar: add, manage and filter the relist set.

        Same shape as the Monitor toolbar: the everyday action is a plain
        button, the rest live behind Add…/Actions…, and the filter stays
        visible. This used to be five stacked button rows.
        """
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.bazar_relist_add_items_button = Gtk.Button(
            label="Add from Items"
        )
        self.bazar_relist_add_items_button.set_tooltip_text(
            "Add the item selected in the Items section to the relist set."
        )
        self.bazar_relist_add_items_button.connect(
            "clicked", lambda *_: self._bazar_relist_add_from_items()
        )
        row.append(self.bazar_relist_add_items_button)
        self.bazar_relist_add_button = self._menu_button(
            "Add…",
            "Put every inventory item or a database pick into the relist "
            "set.",
            (
                (
                    "Add whole inventory",
                    self._bazar_relist_add_whole_inventory,
                ),
                (
                    "Item database…",
                    lambda: self._open_item_database_picker("relist"),
                ),
            ),
        )
        row.append(self.bazar_relist_add_button)
        self.bazar_relist_group_button = self._menu_button(
            "Actions…",
            "Select or unselect rows, remove the selection from relisting, "
            "or put every shown or selected row in or out of the relist set "
            "at once.",
            (
                ("Select all shown", self._bazar_relist_select_shown),
                ("Unselect all", self._bazar_relist_unselect_all),
                None,
                (
                    "Remove from relisting",
                    self._bazar_relist_remove_selected,
                ),
                None,
                (
                    "Relist all shown",
                    lambda: self._bazar_relist_set_all_shown(True),
                ),
                (
                    "Stop relisting all shown",
                    lambda: self._bazar_relist_set_all_shown(False),
                ),
                None,
                (
                    "Relist selected",
                    lambda: self._bazar_relist_set_selected(True),
                ),
                (
                    "Stop relisting selected",
                    lambda: self._bazar_relist_set_selected(False),
                ),
            ),
        )
        row.append(self.bazar_relist_group_button)
        self.bazar_relist_filter = Gtk.CheckButton(label="Only relisting")
        self.bazar_relist_filter.set_tooltip_text(
            "Show only items in the relist set (on by default); turn it "
            "off to browse every configured item."
        )
        self.bazar_relist_filter.set_active(True)
        self.bazar_relist_filter.connect(
            "toggled", lambda *_: self._bazar_render_relist_items()
        )
        row.append(self.bazar_relist_filter)
        # The named buttons stay available for the smoke and for callers;
        # they are the popover entries now.
        entries = self.bazar_relist_group_button.ntpl_entries
        self.bazar_relist_select_all_button = entries["Select all shown"]
        self.bazar_relist_unselect_all_button = entries["Unselect all"]
        self.bazar_relist_remove_button = entries["Remove from relisting"]
        return row

    def _build_bazar_relist_editor(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(2)
        box.set_margin_start(2)
        box.set_margin_end(2)
        self.bazar_relist_editor_label = Gtk.Label(
            label="No item selected"
        )
        self.bazar_relist_editor_label.set_xalign(0)
        self.bazar_relist_editor_label.set_wrap(True)
        box.append(self.bazar_relist_editor_label)

        form = self.bazar_relist_form
        vnum = Gtk.Entry()
        vnum.set_placeholder_text("item vnum")
        vnum.set_input_purpose(Gtk.InputPurpose.DIGITS)
        vnum.set_max_length(10)
        vnum.set_width_chars(8)
        form["vnum"] = vnum
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
        amount.set_value(1)
        amount.set_digits(0)
        form["amount"] = amount
        minimum = Gtk.SpinButton.new_with_range(0, 2_000_000_000, 1000)
        minimum.set_value(0)
        minimum.set_digits(0)
        minimum.set_width_chars(12)
        form["min"] = minimum
        box.append(
            self._field_row(
                Gtk.Label(label="Amount"),
                amount,
                Gtk.Label(label="Min price"),
                minimum,
            )
        )
        self.bazar_relist_hint = Gtk.Label(label="")
        self.bazar_relist_hint.set_xalign(0)
        self.bazar_relist_hint.set_wrap(True)
        self.bazar_relist_hint.add_css_class("dim-label")
        box.append(self.bazar_relist_hint)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.bazar_relist_save_button = Gtk.Button(
            label="Save relist settings"
        )
        self.bazar_relist_save_button.add_css_class("suggested-action")
        self.bazar_relist_save_button.connect(
            "clicked", lambda *_: self._bazar_relist_save()
        )
        buttons.append(self.bazar_relist_save_button)
        self.bazar_relist_open_button = Gtk.Button(
            label="Open full editor"
        )
        self.bazar_relist_open_button.set_tooltip_text(
            "Edit the name, search packet and cost in Items."
        )
        self.bazar_relist_open_button.connect(
            "clicked", lambda *_: self._bazar_open_full_editor()
        )
        buttons.append(self.bazar_relist_open_button)
        box.append(buttons)
        self._bazar_set_relist_form_sensitive(False)
        return box

    def _build_bazar_monitor_section(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.set_margin_top(6)
        page.set_margin_bottom(6)
        page.set_margin_start(6)
        page.set_margin_end(6)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        monitor_start = Gtk.Button(label="Monitor Start")
        monitor_start.add_css_class("suggested-action")
        monitor_start.set_tooltip_text(
            "Search every item in one pass; sends search packets only."
        )
        monitor_start.connect("clicked", lambda *_: self._bazar_start_monitor())
        tools.append(monitor_start)
        monitor_stop = Gtk.Button(label="Monitor Stop")
        monitor_stop.connect("clicked", lambda *_: self._bazar_stop())
        tools.append(monitor_stop)
        # One search shared by both Monitor tabs. With a few hundred
        # scanned inventory items the table is long; typing a name or vnum
        # narrows both views at once.
        search = Gtk.SearchEntry()
        search.set_placeholder_text("search item name or vnum")
        search.set_hexpand(True)
        search.set_tooltip_text(
            "Filter both Monitor tabs by item name, configured label or vnum."
        )
        search.connect(
            "search-changed", lambda *_: self._bazar_render_results()
        )
        self.bazar_monitor_search = search
        tools.append(search)
        content.append(tools)

        # Third-level navbar: one shared search pass, two dashboards split by
        # the cost basis. Each view owns its table and Only priced filter.
        monitors = Gtk.Notebook()
        monitors.set_vexpand(True)
        monitors.set_scrollable(True)
        self.bazar_monitor_notebook = monitors
        titles = {"nosmall": "NosMall", "npc": "NPC resale"}
        for source in self._bazar_monitor_sources:
            monitors.append_page(
                self._build_bazar_monitor_view(source),
                Gtk.Label(label=titles[source]),
            )
        monitors.connect("switch-page", self._bazar_monitor_switched)
        body = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        body.set_vexpand(True)
        body.set_wide_handle(True)
        body.set_start_child(self._build_bazar_monitor_items_panel())
        body.set_end_child(monitors)
        content.append(body)
        self._bazar_render_monitor_items()
        self._bazar_monitor_switched(monitors, monitors.get_nth_page(0), 0)

        monitor_frame = self._frame(
                "Monitor",
                content,
                help_text=(
                    "One Monitor Start searches every enabled item at the "
                    "2.5 s pacing. The third-level tabs split the results by "
                    "cost basis: NosMall (Cost = NosMall ND, plus items "
                    "whose cost is still unknown) shows gold per ND; NPC "
                    "resale (Cost = NPC gold) shows the gold markup for "
                    "items bought at an NPC and sold in the bazaar, for "
                    "example Pet Food or Bell of Sweet Home. The Item column "
                    "is the real Item.dat name; hover it for the configured "
                    "label and search vnums. Only priced hides rows without "
                    "a current answer; each tab has its own filter. The left "
                    "Monitor items list holds only the monitored items; the "
                    "Monitor and Relist ticks in the Items section choose "
                    "them, and unticking one here removes it from the set. "
                    "Add… adds the whole inventory or picks from the Item.dat "
                    "database; Actions… works on the visible or selected "
                    "rows. A pass where every item times "
                    "out retries after the refresh interval."
                ),
        )
        monitor_frame.set_vexpand(True)
        page.append(monitor_frame)
        return page

    def _build_bazar_monitor_items_panel(self) -> Gtk.Widget:
        """The Monitor set: tick rows to search them, bulk add/import."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_size_request(370, -1)
        box.set_vexpand(True)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        title = Gtk.Label(label="Monitor items")
        title.set_xalign(0)
        # Keep the ? next to the title instead of at the far edge of the
        # panel; every other section header reads the same way.
        header.append(title)
        header.append(
            self._help_button(
                "Monitor items",
                "The Monitor set: every configured item whose Monitor tick "
                "is on in the Items section. Start searches these rows; "
                "unticking one here removes it from the set but keeps the "
                "item configured. Add from Items takes the rows selected "
                "in the Items section, Add… adds the whole inventory or "
                "picks from the Item.dat database, and Actions… monitors "
                "or stops the selection or every shown row and opens the "
                "full editor.",
            )
        )
        box.append(header)
        self.bazar_monitor_items_info = Gtk.Label(label="0 items")
        self.bazar_monitor_items_info.set_xalign(0)
        self.bazar_monitor_items_info.add_css_class("dim-label")
        box.append(self.bazar_monitor_items_info)
        self.bazar_monitor_items_list = Gtk.ListBox()
        self.bazar_monitor_items_list.set_selection_mode(
            Gtk.SelectionMode.MULTIPLE
        )
        self.bazar_monitor_items_list.set_activate_on_single_click(True)
        self.bazar_monitor_items_list.connect(
            "row-selected", self._bazar_monitor_selection_changed
        )
        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self.bazar_monitor_items_list)
        scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        scroll.set_vexpand(True)
        self.bazar_monitor_items_scroll = scroll
        box.append(scroll)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        add_items = Gtk.Button(label="Add from Items")
        add_items.set_tooltip_text(
            "Monitor the rows selected in the Items section."
        )
        add_items.connect(
            "clicked", lambda *_: self._bazar_monitor_add_from_items()
        )
        toolbar.append(add_items)
        toolbar.append(self._build_bazar_monitor_add_button())
        self.bazar_monitor_group_button = (
            self._build_bazar_monitor_group_button()
        )
        toolbar.append(self.bazar_monitor_group_button)
        box.append(toolbar)
        return box

    def _build_bazar_monitor_add_button(self) -> Gtk.MenuButton:
        return self._menu_button(
            "Add…",
            "Add every inventory item or pick from the Item.dat database.",
            (
                (
                    "Add whole inventory",
                    self._bazar_monitor_add_whole_inventory,
                ),
                (
                    "Item database…",
                    lambda: self._open_item_database_picker("monitor"),
                ),
            ),
        )

    def _build_bazar_monitor_group_button(self) -> Gtk.MenuButton:
        """Bulk actions for the Monitor set (shown or selected rows)."""
        return self._menu_button(
            "Actions…",
            "Bulk actions: monitor or stop the visible rows, monitor or "
            "stop the selected rows, select/clear the selection, and open "
            "the full editor.",
            (
                (
                    "Monitor all shown",
                    lambda: self._bazar_monitor_set_all_shown(True),
                ),
                (
                    "Stop monitoring all shown",
                    lambda: self._bazar_monitor_set_all_shown(False),
                ),
                None,
                (
                    "Monitor selected",
                    lambda: self._bazar_monitor_set_selected(True),
                ),
                (
                    "Stop monitoring selected",
                    lambda: self._bazar_monitor_set_selected(False),
                ),
                None,
                (
                    "Select all shown",
                    self._bazar_monitor_select_shown,
                ),
                ("Unselect all", self._bazar_monitor_unselect_all),
                None,
                ("Open full editor", self._bazar_monitor_open_editor),
            ),
        )

    def _build_bazar_monitor_view(self, source: str) -> Gtk.Widget:
        view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        view.set_margin_top(4)
        view.set_margin_start(4)
        view.set_margin_end(4)
        view.set_margin_bottom(4)
        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        info = Gtk.Label(label="No items")
        info.set_xalign(0)
        info.set_hexpand(True)
        info.add_css_class("dim-label")
        tools.append(info)
        only = Gtk.CheckButton(label="Only priced")
        only.set_active(True)
        only.set_tooltip_text("Show only items with a current price.")
        only.connect("toggled", lambda *_: self._bazar_render_results())
        tools.append(only)
        if source == "npc":
            help_title = "NPC resale monitor"
            help_text = (
                "Items whose Cost type is NPC gold — the Item.dat NPC shop "
                "price, such as Pet Food or Bell of Sweet Home. Profit shows "
                "the gold markup (listing price ÷ NPC price); Cost is the "
                "NPC purchase price. Add one in Items with Cost = NPC gold; "
                "leaving the cost at 0 fills the Item.dat price on save. "
                "One Monitor Start searches this list together with NosMall. "
                "The search box above filters both tabs by item name, configured label or vnum."
            )
        else:
            help_title = "NosMall monitor"
            help_text = (
                "Items whose Cost type is NosMall ND, plus items whose cost "
                "is still unknown (price discovery). Profit is the listing "
                "price in gold per ND spent. Import catalog groups with "
                "Import NosMall… in Items and set their ND cost. One Monitor "
                "Start searches this list together with NPC resale. The "
                "search box above filters both tabs by item name, configured "
                "label or vnum."
            )
        tools.append(self._help_button(help_title, help_text))
        view.append(tools)
        results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
        )
        scroll.set_child(results)
        scroll.set_vexpand(True)
        view.append(scroll)
        self.bazar_results_boxes[source] = results
        self.bazar_results_infos[source] = info
        self.bazar_only_priced_checks[source] = only
        return view

    def _build_bazar_items_section(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.set_margin_top(6)
        page.set_margin_bottom(6)
        page.set_margin_start(6)
        page.set_margin_end(6)
        body = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        body.set_vexpand(True)
        body.set_wide_handle(True)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        left.set_size_request(470, -1)
        left.set_vexpand(True)
        items_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        search = Gtk.SearchEntry()
        search.set_placeholder_text("search all available items")
        search.set_hexpand(True)
        search.connect("search-changed", lambda *_: self._bazar_render_items())
        self._inventory_search = search
        self.bazar_items_search = search
        top.append(search)
        new_item = Gtk.Button(label="New")
        new_item.set_tooltip_text("Clear the editor for a new item")
        new_item.connect("clicked", lambda *_: self._bazar_new_item())
        top.append(new_item)
        top.append(self._build_items_import_button())
        top.append(self._build_items_more_button())
        items_box.append(top)

        self.bazar_items_info = Gtk.Label(label="0 items")
        self.bazar_items_info.set_xalign(0)
        self.bazar_items_info.set_wrap(True)
        self.bazar_items_info.add_css_class("dim-label")
        self._inventory_status = self.bazar_items_info
        self.bazar_inventory_info = self.bazar_items_info
        items_box.append(self.bazar_items_info)

        self.bazar_items_store = Gio.ListStore.new(BazarItemRowView)
        self.bazar_items_selection = Gtk.MultiSelection(
            model=self.bazar_items_store
        )
        self.bazar_items_selection.connect(
            "selection-changed", self._bazar_items_selection_changed
        )
        self.bazar_items_view = Gtk.ColumnView(
            model=self.bazar_items_selection
        )
        self.bazar_items_view.set_vexpand(True)
        self.bazar_items_view.add_css_class("bazar-items")
        for column in self._bazar_item_columns():
            self.bazar_items_view.append_column(column)
        items_scroll = Gtk.ScrolledWindow()
        items_scroll.set_child(self.bazar_items_view)
        items_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        items_scroll.set_vexpand(True)
        self.bazar_items_scroll = items_scroll
        # The inventory refresh paths check this name; the catalog is the
        # view they must repaint.
        self._inventory_list = self.bazar_items_view
        self._inventory_scroll = items_scroll
        items_box.append(items_scroll)

        select_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=4
        )
        add_monitor = Gtk.Button(label="Add to Monitor")
        add_monitor.set_tooltip_text(
            "Monitor the selected rows (Ctrl+click selects more than one)."
        )
        add_monitor.connect(
            "clicked", lambda *_: self._bazar_add_selected_to("enabled")
        )
        select_row.append(add_monitor)
        add_relist = Gtk.Button(label="Add to Relist")
        add_relist.set_tooltip_text(
            "Put the selected rows into the relist set (floor 1 where "
            "unset)."
        )
        add_relist.connect(
            "clicked", lambda *_: self._bazar_add_selected_to("relist")
        )
        select_row.append(add_relist)
        delete_item = Gtk.Button(label="Delete")
        delete_item.set_tooltip_text("Delete the selected configured item")
        delete_item.connect("clicked", lambda *_: self._bazar_delete_item())
        select_row.append(delete_item)
        self.bazar_items_filter = Gtk.DropDown.new_from_strings(
            ["All items", "Monitored", "Relisting"]
        )
        self.bazar_items_filter.set_tooltip_text(
            "Show the whole Item.dat catalog, only monitored items, or only "
            "the relist set."
        )
        self.bazar_items_filter.connect(
            "notify::selected", lambda *_: self._bazar_render_items()
        )
        select_row.append(self.bazar_items_filter)
        items_box.append(select_row)

        items_frame = self._frame(
            "Items",
            items_box,
            help_text=(
                "Every item in the local Item.dat database (about 7,805) "
                "plus configured rows the database does not know and "
                "inventory-only vnums, with a search box over all of them. "
                "The Monitor tick searches the item; the Relist tick puts "
                "it in the relist set (floor 1 when none is set). Ticking a "
                "DB row that is not configured adds it first. Select a row "
                "to edit its label, search, vnum, tab/amount, floor, cost "
                "and c_reg fields; a tick or a saved draft creates the "
                "item. Add to Monitor / Add to Relist apply to the selected "
                "rows. The filter under the list shows the whole catalog, "
                "only monitored items, or only the relist set, so the "
                "8,000-row list is quickly narrowed. Import… adds NosMall "
                "catalog groups, searches the "
                "whole Item.dat database, or imports a saved bazar config "
                "(add keeps the current items and skips duplicates; replace "
                "swaps the whole list after a confirmation). ⋯ holds "
                "Rescan inventory, Price all inventory, Add whole "
                "inventory, bulk Monitor/Relist switches and selection. A "
                "row from a bound (negative) vnum or a bag 6/7 slot cannot "
                "be relisted; its Monitor tick still works. The server "
                "sends the inventory only at login: if it is missing or "
                "partial, relog or change channel with this window open."
            ),
        )
        items_frame.set_vexpand(True)
        left.append(items_frame)
        body.set_start_child(left)
        body.set_resize_start_child(True)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right.set_vexpand(True)
        editor_frame = self._frame(
            "Item editor",
            self._build_bazar_editor(),
            help_text=(
                "One bazaar item. Relist needs Item vnum, Tab and a "
                "positive Min price; the Relist tab offers a compact "
                "editor for those fields and the relist-set tick. "
                "Tracking: Monitor searches the item; Relist includes it "
                "in Relist Start passes. Raw packet overrides Search "
                "vnums. Cost is the ND or gold basis used to compute "
                "profit and to choose the Monitor tab: NosMall ND, NPC "
                "gold (auto-filled from Item.dat when 0) or No cost."
            ),
        )
        editor_frame.set_vexpand(True)
        right.append(editor_frame)
        body.set_end_child(right)
        body.set_resize_end_child(False)
        page.append(body)
        return page

    def _build_bazar_config_section(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.set_margin_top(6)
        page.set_margin_bottom(6)
        page.set_margin_start(6)
        page.set_margin_end(6)
        bazaar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.bazar_npc_entry = Gtk.Entry()
        self.bazar_npc_entry.set_placeholder_text("Bibi entity id")
        self.bazar_npc_entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self.bazar_npc_entry.set_max_length(10)
        self.bazar_npc_entry.set_width_chars(10)
        self.bazar_npc_entry.set_tooltip_text(
            "Bibi Basar entity id; learned when you open the bazaar."
        )
        self.bazar_npc_entry.connect(
            "changed", lambda *_: self._bazar_update_relist_info()
        )
        bazaar_box.append(
            self._field_row(
                Gtk.Label(label="Bibi id"), self.bazar_npc_entry
            )
        )
        self.bazar_refresh_spin = Gtk.SpinButton.new_with_range(5, 3600, 5)
        self.bazar_refresh_spin.set_value(30)
        self.bazar_refresh_spin.set_digits(0)
        bazaar_box.append(
            self._field_row(
                Gtk.Label(label="Refresh (s)"), self.bazar_refresh_spin
            )
        )
        page.append(
            self._frame(
                "Bazaar",
                bazaar_box,
                help_text=(
                    "Open the bazaar once by hand (talk to Bibi Basar -> "
                    "NosBazar) so the Bibi entity id and dialog option are "
                    "learned; the id also appears here from the client log. "
                    "Refresh is the wait between passes. Your own listings "
                    "are recognised by character id from the packets, so "
                    "there is no name to enter."
                ),
            )
        )

        config_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6
        )
        self.bazar_config_label = Gtk.Label(
            label=str(self.bazar_config_path)
        )
        self.bazar_config_label.set_xalign(0)
        self.bazar_config_label.set_wrap(True)
        self.bazar_config_label.set_max_width_chars(60)
        self.bazar_config_label.add_css_class("dim-label")
        config_box.append(self.bazar_config_label)
        config_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        save_button = Gtk.Button(label="Save config")
        save_button.connect("clicked", lambda *_: self._bazar_save_config())
        reload_button = Gtk.Button(label="Reload")
        reload_button.set_tooltip_text(
            "Discard the in-memory items and read the JSON file again"
        )
        reload_button.connect("clicked", lambda *_: self._bazar_load_config())
        names_button = Gtk.Button(label="Reload names")
        names_button.set_tooltip_text(
            "Read item names again; use after rebuilding the database."
        )
        names_button.connect(
            "clicked", lambda *_: self._bazar_reload_names()
        )
        config_row.append(save_button)
        config_row.append(reload_button)
        config_row.append(names_button)
        config_box.append(config_row)
        page.append(
            self._frame(
                "Configuration",
                config_box,
                help_text=(
                    "Saved automatically per game client as bazar-<pid>.json. "
                    "Copy it to bazar.json to use the same items in another "
                    "client. Reload discards unsaved edits and reads the JSON "
                    "file again. Reload names re-reads item names from the "
                    "local database; use it after ntpl db build if a row "
                    "still shows a shortcut."
                ),
            )
        )
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
            b".ntpl-help { min-width: 22px; min-height: 22px;"
            b" padding: 0; border-radius: 11px; font-weight: bold; }"
            b".bazar-ok { color: #33b34d; }"
            b".bazar-bad { color: #d93025; }"
            b".bazar-warn { color: #b8860b; }"
            # Thin visible rules between the NosBazar result rows, so the
            # eye can follow an item across the Cost/NB Price/Profit columns.
            b"separator.bazar-sep { background-color: #8f99a4;"
            b" min-height: 1px; }"
        )
        display = Gdk.Display.get_default()
        if display is not None and hasattr(Gtk.StyleContext, "add_provider_for_display"):
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _on_page_switch(self, _notebook, _page, _page_num) -> None:
        self._ensure_map_loaded()
        if self.map_area is not None:
            self.map_area.queue_draw()
        if _page is self.packet_page:
            replay = list(self._hidden_packets)
            self._hidden_packets.clear()
            for packet in replay:
                self._append(packet)
            if self.packets_skipped_while_hidden:
                skipped = self.packets_skipped_while_hidden
                self.packets_skipped_while_hidden = 0
                self._set_status(
                    f"packet view was paused while hidden; showing the last "
                    f"{len(replay)} of {skipped} packet(s), the session log "
                    "has all"
                )

    # -- connection -----------------------------------------------------
    def _start_client(self, port: int | None = None) -> None:
        """(Re)start the reader thread against the selected client.

        A generation counter retires the previous worker when the user picks
        another client: the old thread may be blocked in ``recv`` for up to
        half a second, and must not push its packets into the new session.
        """
        if port is not None:
            self.port = port
        # Publish this client's own movement/spells for another bot that
        # follows this character (mirror bus). A reconnect restarts the file.
        self._open_mirror_writer()
        self._client_generation += 1
        generation = self._client_generation
        old, self.client = self.client, PacketClient(self.host, self.port)
        if old is not None:
            old.close()
        client = self.client

        def worker() -> None:
            while (
                not self._closing
                and generation == self._client_generation
            ):
                try:
                    client.connect(retries=10, delay=0.2)
                except ConnectionLost as exc:
                    if (
                        self._closing
                        or generation != self._client_generation
                    ):
                        return
                    # The port file can hold a stale value read before a new
                    # DLL rewrote it (a reused Windows PID: live 2026-11-23
                    # the GUI dialled 47823 while the DLL listened on
                    # 47822). Re-read it on every failure instead of staying
                    # dead on one wrong port.
                    attached = self.attached_client
                    fresh = (
                        read_port(
                            attached.context.prefix, attached.winpid
                        )
                        if attached is not None
                        else None
                    )
                    if fresh is not None and fresh != client.port:
                        client.port = fresh
                        self.port = fresh
                        attached.port = fresh
                        GLib.idle_add(
                            self._set_status,
                            f"logger is on 127.0.0.1:{fresh} now; retrying",
                        )
                    GLib.idle_add(
                        self._set_status,
                        f"not connected: {exc} — is the logger DLL "
                        "injected? pick a client under Client…, retrying "
                        "every second",
                    )
                    time.sleep(RECONNECT_DELAY)
                    continue

                if self._closing or generation != self._client_generation:
                    return
                GLib.idle_add(self._on_connected, generation)

                try:
                    while (
                        not self._closing
                        and generation == self._client_generation
                    ):
                        message = client.recv(timeout=0.5)
                        if message is not None:
                            self.inbox.put(message)
                except (ConnectionLost, ProtocolError, OSError) as exc:
                    if generation != self._client_generation:
                        return
                    GLib.idle_add(
                        self._set_status,
                        f"disconnected: {exc} — reconnecting…",
                    )

                if not self._closing and generation == self._client_generation:
                    # The DLL accepts one host at a time only while none is
                    # connected; a handover (e.g. the GUI paused and its
                    # socket destroyed for an MCP session) leaves the GUI
                    # disconnected until it reconnects. Reconnect instead of
                    # leaving the window dead.
                    time.sleep(RECONNECT_DELAY)

        threading.Thread(target=worker, name="ntpl-gui-reader", daemon=True).start()
        if not self._drain_timer_started:
            self._drain_timer_started = True
            GLib.timeout_add(DRAIN_INTERVAL_MS, self._drain)

    # -- client selection -----------------------------------------------
    def _begin_client_selection(self, auto_single: bool = False) -> None:
        """Scan for running game windows and either attach or show the picker.

        ``auto_single`` is for startup: one client whose logger is already
        running is attached without a dialog. Everything else (no client,
        several clients, or a client to inject into) asks the user.
        """
        self._client_scan += 1
        scan = self._client_scan
        if self._client_dialog is not None:
            self._set_selector_status("Scanning for NosTale clients…")
        else:
            self._set_status("looking for NosTale clients…")

        def worker() -> None:
            discovery = discover_clients()
            GLib.idle_add(self._on_client_scan, discovery, scan, auto_single)

        threading.Thread(
            target=worker, name="ntpl-client-scan", daemon=True
        ).start()

    def _on_client_scan(
        self, discovery: Discovery, scan: int, auto_single: bool
    ) -> bool:
        if self._closing or scan != self._client_scan:
            return False
        self.discovery = discovery
        clients = discovery.clients
        free = [
            client
            for client in clients
            if client.port
            and client_lock_owner(client.winpid) in (None, os.getpid())
        ]
        free_pids = {client.winpid for client in free}
        busy = [client for client in clients if client.winpid not in free_pids]
        others_owned = bool(busy) and all(
            client_lock_owner(client.winpid) not in (None, os.getpid())
            for client in busy
        )
        if auto_single and free and (
            len(clients) == 1 or (len(free) == 1 and others_owned)
        ):
            self._attach_client(free[0], free[0].port)
            return False
        self._show_client_dialog(discovery)
        return False

    def _show_client_dialog(self, discovery: Discovery) -> None:
        self._close_client_dialog()
        clients = discovery.clients
        self._client_dialog_rows = list(clients)

        dialog = Gtk.Window(title="Select NosTale client")
        dialog.set_transient_for(self.window)
        dialog.set_modal(True)
        dialog.set_resizable(False)
        dialog.set_default_size(500, -1)
        dialog.connect("close-request", self._on_client_dialog_closed)
        self._client_dialog = dialog

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(14)
        box.set_margin_bottom(14)
        box.set_margin_start(14)
        box.set_margin_end(14)

        heading = Gtk.Label()
        if clients:
            heading.set_markup(
                f"<b>Found {len(clients)} NosTale client(s)</b>"
            )
        else:
            heading.set_markup("<b>No NosTale client found</b>")
        heading.set_xalign(0)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.append(heading)
        header.append(
            self._help_button(
                "client picker",
                "Choose the game window to attach to. Listing marks every "
                "game window with a number in its title bar (NosTale [#2]); "
                "pick the row with the same number. A client without the "
                "logger is injected automatically (Inject & connect). "
                "Clients attached by another GUI are disabled. If the list is "
                "empty, start the game and press Refresh.",
            )
        )
        box.append(header)

        if clients:
            listbox = Gtk.ListBox()
            listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
            listbox.add_css_class("boxed-list")
            listbox.connect("row-selected", self._on_client_row_selected)
            listbox.connect(
                "row-activated", lambda _l, _r: self._on_client_connect_clicked()
            )
            first = None
            for client in clients:
                row = Gtk.ListBoxRow()
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                inner.set_margin_top(8)
                inner.set_margin_bottom(8)
                inner.set_margin_start(10)
                inner.set_margin_end(10)
                title = Gtk.Label()
                title.set_markup(
                    f"<b>{GLib.markup_escape_text(client.title)}</b>"
                )
                title.set_xalign(0)
                owner = client_lock_owner(client.winpid)
                if owner is not None and owner != os.getpid():
                    row.set_sensitive(False)
                    subtitle_text = (
                        f"PID {client.winpid} · attached to another GUI "
                        f"(PID {owner})"
                    )
                elif owner == os.getpid():
                    subtitle_text = (
                        f"PID {client.winpid} · attached to this window"
                    )
                else:
                    subtitle_text = f"PID {client.winpid} · {client.status}"
                subtitle = Gtk.Label(label=subtitle_text)
                subtitle.set_xalign(0)
                subtitle.add_css_class("dim-label")
                inner.append(title)
                inner.append(subtitle)
                row.set_child(inner)
                listbox.append(row)
                if first is None and row.get_sensitive():
                    first = row
            self._client_dialog_list = listbox
            box.append(listbox)
            if first is not None:
                listbox.select_row(first)
        self._client_dialog_status = Gtk.Label(
            label=" · ".join(discovery.errors)
        )
        self._client_dialog_status.set_wrap(True)
        self._client_dialog_status.set_xalign(0)
        self._client_dialog_status.add_css_class("dim-label")
        box.append(self._client_dialog_status)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        refresh = Gtk.Button(label="Refresh")
        refresh.connect(
            "clicked",
            lambda *_: self._begin_client_selection(auto_single=False),
        )
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: dialog.close())
        self._client_dialog_action = Gtk.Button(label="Connect")
        self._client_dialog_action.add_css_class("suggested-action")
        self._client_dialog_action.set_sensitive(False)
        self._client_dialog_action.connect(
            "clicked", lambda *_: self._on_client_connect_clicked()
        )
        buttons.append(refresh)
        buttons.append(cancel)
        buttons.append(self._client_dialog_action)
        box.append(buttons)

        dialog.set_child(box)
        if self._client_dialog_list is not None:
            self._on_client_row_selected(
                self._client_dialog_list,
                self._client_dialog_list.get_selected_row(),
            )
        dialog.present()

    def _on_client_dialog_closed(self, *_args) -> bool:
        self._client_dialog = None
        self._client_dialog_list = None
        self._client_dialog_action = None
        self._client_dialog_status = None
        # A Cancel or a refresh drops any injection still running.
        self._inject_client = None
        # With running clients the dialog exists to pick one: cancelling must
        # not dial a default port that may belong to another GUI's client.
        # Only the empty case keeps the old fallback so the window can wait
        # for a logger that is started later.
        has_clients = bool(self.discovery and self.discovery.clients)
        if self.client is None and not self._closing:
            if has_clients:
                self._set_status(
                    "no client selected — use Client… to attach"
                )
            else:
                self._set_status(f"connecting to {self.host}:{self.port}…")
                self._start_client()
        return False

    def _close_client_dialog(self) -> None:
        dialog, self._client_dialog = self._client_dialog, None
        self._client_dialog_list = None
        self._client_dialog_action = None
        self._client_dialog_status = None
        self._inject_client = None
        if dialog is not None:
            dialog.destroy()

    def _set_selector_status(self, text: str) -> None:
        if self._client_dialog_status is not None:
            self._client_dialog_status.set_text(text)
        self._set_status(text)

    def _on_client_row_selected(self, _listbox, row) -> None:
        action = self._client_dialog_action
        if action is None:
            return
        if row is None or row.get_index() >= len(self._client_dialog_rows):
            action.set_sensitive(False)
            return
        client = self._client_dialog_rows[row.get_index()]
        action.set_label("Connect" if client.port else "Inject & connect")
        injectable = self.discovery.injectable if self.discovery else False
        action.set_sensitive(bool(client.port) or injectable)

    def _on_client_connect_clicked(self) -> None:
        listbox = self._client_dialog_list
        row = listbox.get_selected_row() if listbox is not None else None
        if row is None or row.get_index() >= len(self._client_dialog_rows):
            return
        client = self._client_dialog_rows[row.get_index()]
        if client.port:
            self._attach_client(client, client.port)
            return
        self._inject_and_attach(client)

    def _inject_and_attach(self, client: ClientInfo) -> None:
        if not acquire_client_lock(client.winpid):
            owner = client_lock_owner(client.winpid)
            message = (
                f"{client.title} is already attached by another GUI "
                f"(PID {owner})"
                if owner is not None
                else f"cannot attach to {client.title}"
            )
            if self.discovery is not None:
                self._show_client_dialog(self.discovery)
            self._set_selector_status(message)
            return
        action = self._client_dialog_action
        if action is not None:
            action.set_sensitive(False)
        self._inject_client = client
        self._set_selector_status(
            f"Injecting the logger into {client.title} (PID {client.winpid})…"
        )
        discovery = self.discovery
        injector = discovery.injector if discovery else None
        dll = discovery.dll if discovery else None

        def worker() -> None:
            ok, output = inject_client(client, injector, dll)
            port = (
                wait_for_port(client.context, client.winpid) if ok else None
            )
            GLib.idle_add(self._on_client_injected, client, ok, output, port)

        threading.Thread(
            target=worker, name="ntpl-inject", daemon=True
        ).start()

    def _on_client_injected(
        self,
        client: ClientInfo,
        ok: bool,
        output: str,
        port: int | None,
    ) -> bool:
        # The dialog was cancelled or refreshed while the injector ran.
        if self._inject_client is not client:
            release_client_lock(client.winpid)
            return False
        self._inject_client = None
        if ok:
            # An older DLL has no port file; the default remains the best
            # guess and the reconnect loop reports it if it is wrong.
            port = port or client.context.base_port
            client.port = port
            self._attach_client(client, port)
            return False

        release_client_lock(client.winpid)
        if self._client_dialog is None:
            self._set_status(f"injection failed: {output}")
            return False
        self._set_selector_status(f"Injection failed: {output}")
        if self._client_dialog_action is not None:
            self._client_dialog_action.set_sensitive(True)
        return False

    def _update_window_title(self) -> None:
        """Host version, attached client and the DLL version when known."""
        if self.window is None:
            return
        title = f"NosTale Packet Logger {__version__}"
        if self.client_label:
            title += f" — {self.client_label}"
        if self.dll_version:
            title += f" · DLL {self.dll_version}"
        self.window.set_title(title)

    def _attach_client(self, client: ClientInfo, port: int) -> None:
        """Point the reader thread at one client and reset the old world."""
        if not acquire_client_lock(client.winpid):
            owner = client_lock_owner(client.winpid)
            message = (
                f"{client.title} is already attached by another GUI "
                f"(PID {owner})"
                if owner is not None
                else f"cannot attach to {client.title}"
            )
            if self.discovery is not None and self._client_dialog is not None:
                self._show_client_dialog(self.discovery)
                self._set_selector_status(message)
            else:
                self._set_status(message)
            return
        previous, self.attached_client = self.attached_client, client
        if previous is not None and previous.winpid != client.winpid:
            release_client_lock(previous.winpid)
        self._close_client_dialog()
        if self.bot_running:
            self._stop_bot()
        # Another client is another character and another map: packets already
        # parsed for the previous one must not leak into the new session.
        self.engine = Engine()
        self.player_targets.clear()
        self._mirror_reset()
        self.char_id = None
        self.last_move = None
        self.status_received = False
        # Backfill from this client's own log, named by its Windows PID. The
        # startup load may have read another client's file when several run.
        if self.log_file_override is None:
            self.log_file = str(
                client_log_path(client.context.prefix, client.winpid)
            )
        self.spawns_loaded = False
        self._ensure_spawns_loaded()
        if BOT_LOG_OVERRIDE is None:
            self._switch_bot_log(client.winpid)
        # The bazar tab is per character too: its controller, results and
        # config file are reset together with the world.
        self._bazar_reset_for_attach(client.winpid)
        self._bazar_seed_from_log()
        self._family_reset_for_attach(client.winpid)
        self._minigame_reset_for_attach(client.winpid)
        # The minigame window is often open before this host attaches (or
        # before a GUI restart); seed its object and points from the log.
        self._minigame_seed_from_log()
        # A new client has its own DLL; the old "not supported" latches do
        # not apply to it.
        self.dll_target_supported = True
        self.client_label = f"{client.title} · PID {client.winpid}"
        self.dll_version = None
        self._update_window_title()
        self._set_status(
            f"attaching to {self.client_label} on 127.0.0.1:{port}…"
        )
        self._start_client(port)

    def _drain(self) -> bool:
        dirty = False
        # A busy town can deliver thousands of packets per tick. Draining
        # 1000 of them in one main-loop callback froze the window ("Not
        # Responding"). Process a bounded batch with a 15 ms budget so GTK
        # can paint and handle input between batches.
        deadline = time.monotonic() + 0.015
        processed = 0
        while processed < 500:
            if processed % 32 == 0 and time.monotonic() >= deadline:
                break
            try:
                message = self.inbox.get_nowait()
            except queue.Empty:
                break
            processed += 1

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
                            if len(parts) >= 5:
                                try:
                                    rest = int(parts[4])
                                except ValueError:
                                    rest = -1
                                if rest >= 0:
                                    self.engine.world.resting = bool(rest)
                            self.pos_cell = (x, y)
                            self.pos_at = time.monotonic()
                            self._pos_error_text = ""
                            self._learn_char_id(char_id)
                            # The POS poll runs twice a second; do not flood
                            # the status bar (it hid action messages).
                            self._update_char_info()
                            dirty = True
                elif message.payload.startswith("MOVE: character is sitting"):
                    # The DLL's own MoveTo refused because the client is
                    # seated, meaning POS/rest confirmations lagged behind.
                    # Trust the DLL: cancel the refused leg and stand at once
                    # instead of retrying MOVE every second.
                    self.engine.world.resting = True
                    self.stand_sent_until = 0.0
                    self.pending_target = None
                    self.pending_is_path = False
                    self.pending_loot_id = None
                    self._set_status(message.payload)
                elif message.payload.startswith("TARGET "):
                    # DLL answer to a bot TARGET. The attack has already gone
                    # out in the same tick (the DLL handles commands in
                    # order); a refusal only blocks the entity for a while.
                    self.last_target_reply = message.payload
                    parts = message.payload.split()
                    if len(parts) >= 3 and parts[2] == "ERR":
                        try:
                            blocked = int(parts[1])
                        except ValueError:
                            blocked = None
                        reason = " ".join(parts[3:])
                        now = time.monotonic()
                        minigame_refusal = blocked is not None and (
                            blocked == self.minigame_focus_id
                            or self.minigame_target_requests.get(
                                blocked, 0.0
                            )
                            > now
                        )
                        if minigame_refusal and blocked is not None:
                            # A player focus refusal is not a combat target:
                            # do not delete the player from the world or
                            # pause the bot. Retry another player instead.
                            self.minigame_target_requests.pop(blocked, None)
                            self.minigame_focus_blocked[blocked] = (
                                now + TARGET_BLOCK_S
                            )
                            if blocked == self.minigame_focus_id:
                                self.minigame_focus_id = None
                                self.minigame_focus_name = ""
                            self.minigame_focus_refresh_at = 0.0
                            self._log_line(
                                f"minigame focus refused {blocked}: {reason}"
                            )
                            self._minigame_set_status(
                                f"Focus refused for player {blocked}: "
                                f"{reason}",
                                "bazar-warn",
                            )
                        else:
                            if blocked is not None:
                                self.target_blocked[blocked] = (
                                    time.monotonic() + TARGET_BLOCK_S
                                )
                                if (
                                    "entity not found" in reason
                                    or "client refused" in reason
                                ):
                                    # The client has no live object for it.
                                    # The server sends no `out` for a killed
                                    # monster, so without this the bot keeps
                                    # selecting the corpse and every attack
                                    # is cancelled.
                                    self.engine.world.entities.pop(
                                        blocked, None
                                    )
                                    dirty = True
                            self._set_status(message.payload)
                            # Hundreds of ids can look like monsters in the
                            # world state while the client cannot select them
                            # (killed while out of view, never `out`).
                            # Dropping them one by one used to look like
                            # frantic target switching; after a few in a row,
                            # pause combat briefly instead.
                            self.target_fail_events = [
                                when
                                for when in self.target_fail_events
                                if now - when <= TARGET_FAIL_WINDOW_S
                            ]
                            self.target_fail_events.append(now)
                            if (
                                len(self.target_fail_events)
                                >= TARGET_FAIL_TRIGGER
                                and now >= self.combat_paused_until
                            ):
                                self.combat_paused_until = (
                                    now + TARGET_FAIL_PAUSE_S
                                )
                                self.combat_pause_pos = self._player_cell()
                                self.target_fail_events.clear()
                                self._set_status(
                                    "target selection keeps failing (the "
                                    "client has no such monster); pausing "
                                    "combat"
                                )
                    elif len(parts) >= 5 and parts[2] == "OK":
                        try:
                            selected_id = int(parts[1])
                        except ValueError:
                            selected_id = None
                        if (
                            selected_id != self.minigame_focus_id
                            and selected_id
                            not in self.minigame_target_requests
                        ):
                            self._log_line(
                                f"monster {parts[1]} selected "
                                f"(entity {parts[3]}, controller {parts[4]})"
                            )
                    dirty = True
                elif message.payload.startswith("SPEED CORRECTED"):
                    # The DLL turned the boost off after the server corrected
                    # the player's position (the 2026-11-20 teleport/kick).
                    if self.speed_boost_spin is not None:
                        self._speed_tuning = True
                        self.speed_boost_spin.set_value(0)
                        self._speed_tuning = False
                    self._set_status(
                        "speed boost disabled: the server corrected the "
                        "player's position"
                    )
                elif message.payload.startswith("ntpl "):
                    # The initial DLL status line. Show a clear warning when
                    # the game client still runs an old DLL: a host update
                    # alone cannot add the immediate Speed + logic.
                    version_text = message.payload.split()[1] \
                        if len(message.payload.split()) > 1 else ""
                    try:
                        version = tuple(
                            int(part)
                            for part in version_text.split(".")[:3]
                        )
                    except ValueError:
                        version = ()
                    self.dll_version = version_text or None
                    self._update_window_title()
                    self._set_status(message.payload)
                    if version and version < (0, 2, 6):
                        warning = (
                            "client DLL is older than 0.2.6 — the running "
                            "game still has the old module; restart the game "
                            "client (not just the GUI) to load the uncapped "
                            "Speed + build (Cast speed is host-side)"
                        )
                        self._set_status(warning)
                        # The status bar is overwritten by map auto-selection
                        # a moment later, so keep the warning in the log.
                        self._log_line(warning)
                elif message.payload.startswith("SPEED APPLIED"):
                    parts = message.payload.split()
                    try:
                        value = int(parts[2])
                    except (IndexError, ValueError):
                        value = 0
                    self._set_status(
                        f"speed boost +{value} applied (the server sees the "
                        "normal walk speed)"
                    )
                elif message.payload.startswith("SPEED EXTEND FAILED"):
                    # The DLL could not lift the client's own 59 limit on
                    # this build; the stored value is still the user's choice.
                    self._set_status(
                        "this game client cannot exceed +59 (DLL build "
                        "mismatch) — restart the game to load DLL 0.2.6"
                    )
                elif message.payload.startswith("SPEED UNSUPPORTED"):
                    # Even the lowest boost was corrected. No client-side
                    # speed above the server's cond is accepted here.
                    self._speed_tuning = True
                    if self.speed_boost_spin is not None:
                        self.speed_boost_spin.set_value(0)
                    self._speed_tuning = False
                    self._set_status(
                        "this server rejects even +1: Speed + is not usable "
                        "here; use an in-game speed buff or mount"
                    )
                elif message.payload.startswith("SPEED TUNED"):
                    # The server rejected a higher value but accepted +N; the
                    # DLL settled on it. Show the accepted value without
                    # sending a new SPEED command.
                    parts = message.payload.split()
                    try:
                        value = int(parts[2])
                    except (IndexError, ValueError):
                        value = 0
                    self._speed_tuning = True
                    if self.speed_boost_spin is not None:
                        self.speed_boost_spin.set_value(value)
                    self._speed_tuning = False
                    self._set_status(
                        f"server limited the speed boost to +{value} "
                        "(highest it accepts)"
                    )
                elif message.payload.startswith("CAST "):
                    # DLL acknowledgement of CAST|1/0. The switch already
                    # shows the state; changing it here would resend.
                    self._set_status(
                        "cast speed ON: client cooldowns zeroed"
                        if message.payload.strip() == "CAST ON"
                        else "cast speed normal"
                    )
                elif message.payload.startswith("KEY OK"):
                    # The DLL accepts KEY| (it reports only state changes).
                    if not self.dll_key_supported:
                        self.dll_key_supported = True
                    self._log_line("minigame keyboard ready")
                elif message.payload.startswith("KEY ERR"):
                    reason = message.payload[len("KEY "):]
                    self._log_line(f"minigame keyboard refused: {reason}")
                    self._minigame_abort(
                        f"Minigame stopped: {reason}"
                    )
                elif message.payload.startswith("unknown command: CAST"):
                    # Old DLLs do not know the Cast speed command. The
                    # feature is host-side, so nothing is missing; stay
                    # quiet instead of showing a scary status line.
                    pass
                elif message.payload.startswith("unknown command: KEY"):
                    if self.dll_key_supported:
                        self.dll_key_supported = False
                        self._log_line(
                            "minigame: this DLL has no KEY command; "
                            "restart the game and inject 0.3.0"
                        )
                        if self.minigame_running:
                            self._minigame_abort(
                                "Minigame stopped: this DLL has no KEY "
                                "command. Restart the game client and "
                                "inject DLL 0.3.0."
                            )
                        else:
                            self._minigame_set_status(
                                "This DLL has no KEY command. Restart the "
                                "game client and inject DLL 0.3.0.",
                                "bazar-bad",
                            )
                elif message.payload.startswith(
                    "unknown command: TARGET"
                ):
                    if self.dll_target_supported:
                        self.dll_target_supported = False
                        # Retry the current minigame focus through ncif now
                        # instead of after the normal refresh.
                        self.minigame_focus_refresh_at = 0.0
                        self._set_status(
                            "DLL is too old for target selection; "
                            "falling back to ncif (re-inject the new DLL)"
                        )
                elif message.payload.startswith("POS:"):
                    # The DLL's player-object reader is build-specific and
                    # polls twice a second. When the client build moved the
                    # object, every poll returns the same error; log it once
                    # (a successful POS clears the latch) and keep the status
                    # bar updated instead of filling the Bot log.
                    if message.payload != self._pos_error_text:
                        self._pos_error_text = message.payload
                        self._log_line(
                            message.payload
                            + " (client build changed? rebuild and "
                            "reinject the DLL)"
                        )
                    if self.status is not None:
                        self.status.set_text(message.payload)
                else:
                    self._set_status(message.payload)
            elif message.kind is Kind.QUIT:
                self._set_status("disconnected")
            else:
                packet = Packet.from_message(message)
                # One handler bug must never stop the pump: an exception
                # escaping this callback would abort the GLib source and the
                # window would silently stop receiving packets.
                try:
                    self.engine.feed(packet)
                    self._track_packet(packet)
                    # Cast speed without the bot: repeat the skill the player
                    # pressed when its server cooldown ends.
                    self._cast_assist_packet(packet)
                    self._bazar_on_packet(packet)
                    self._family_on_packet(packet)
                    self._dps_on_packet(packet)
                    self._learn_char_id(self.engine.world.self_id)
                    # Switch the map as soon as the server announces it
                    # instead of waiting for the next 2 s auto-map tick.
                    if packet.opcode == "ski":
                        for info in self.spell_rows:
                            self._update_spell_desc(info)
                    if packet.opcode in ("c_map", "mapout", "at"):
                        map_id = self.engine.world.map_id
                        if map_id is not None and (
                            self.map_grid is None
                            or map_id != self.map_grid.map_id
                        ):
                            self._auto_map_tick()
                except Exception as error:  # noqa: BLE001 - keep the pump up
                    self._on_packet_error(packet.opcode, error)
                if (
                    self.packet_page is not None
                    and self.notebook is not None
                    and self.notebook.get_current_page()
                    == self.notebook.page_num(self.packet_page)
                ):
                    self._append(packet)
                else:
                    # The table is a view, not the capture: the DLL session
                    # log already has every packet. Feeding 500 hidden rows a
                    # second blocked the GTK main loop ("Not Responding").
                    # Keep the recent 500 for when the tab is reopened.
                    self.packets_skipped_while_hidden += 1
                    self._hidden_packets.append(packet)
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

    def _on_packet_error(self, opcode: str, error: Exception) -> None:
        """Record an exception raised while handling one packet.

        The pump keeps running; the first failure per opcode is written to the
        Bot log so a broken handler is visible instead of silently dropping
        packets.
        """
        self.packet_error_count += 1
        if opcode in self.packet_error_opcodes:
            return
        self.packet_error_opcodes.add(opcode)
        self._log_line(
            f"packet handler error on {opcode}: "
            f"{type(error).__name__}: {error}"
        )

    # -- callbacks ------------------------------------------------------
    def _append(self, packet: Packet) -> None:
        self.store.append(PacketRow(packet))
        if self.store.get_n_items() > MAX_ROWS:
            self.store.splice(0, TRIM_ROWS, [])
        if self.follow.get_active() and not self._packet_scroll_scheduled:
            self._packet_scroll_scheduled = True
            GLib.idle_add(self._scroll_to_end)

    def _scroll_to_end(self) -> bool:
        self._packet_scroll_scheduled = False
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

    def _on_connected(self, generation: int | None = None) -> bool:
        if generation is not None and generation != self._client_generation:
            return False
        if not self.status_received:
            where = f"connected to {self.host}:{self.client.port}"
            if self.client_label:
                where = f"{self.client_label} · {where}"
            self._set_status(where)
        # A reconnect may be a restarted game with a newer DLL; let the first
        # command report support again instead of keeping an old latch.
        self.dll_key_supported = True
        self.dll_target_supported = True
        # The GUI can attach after login, so there may be no `at` packet.
        # Ask the DLL for the client's own local position instead of sending a
        # probe walk (which would make the server teleport the character).
        self._sync_portal_block()
        self._sync_speed_boost(quiet=True)
        self._restore_client_cooldowns(quiet=True)
        # The direct --port path has no attach-time reset, so seed the
        # production minigame object here too; the picker path already did,
        # and the method returns immediately when the state is known.
        self._minigame_seed_from_log()
        self._request_position()
        return False

    def _request_position(self) -> bool:
        if self.client is None or not self.client.connected:
            return False
        try:
            self.client.send_raw(POS_COMMAND)
        except (ConnectionLost, ValueError):
            return False
        return True

    def _poll_position(self) -> bool:
        """Ask the DLL for the client's real position twice a second.

        The client's own `walk` packet carries the MoveTo *destination*, so
        without this the map marker jumps to the target; POS reports the
        actual position the client is at right now.
        """
        if self._closing:
            return False
        if self.client is not None and self.client.connected:
            try:
                self.client.send_raw(POS_COMMAND)
            except (ConnectionLost, ValueError):
                pass
        return True

    def _set_status(self, text: str) -> None:
        if self.status is not None:
            self.status.set_text(text)
        self._log_line(text)

    def _set_path_info(self, text: str) -> None:
        if getattr(self, "path_info", None) is not None:
            self.path_info.set_text(text)

    def _char_id(self) -> int | None:
        return self.char_id or self.engine.world.self_id

    def _learn_char_id(self, char_id: int | None) -> None:
        if char_id is None or char_id <= 0:
            return
        previous = self.char_id
        changed = previous is not None and previous != char_id
        self.char_id = char_id
        if changed:
            # Another character on the same client: drop the old character's
            # slots so a pending save cannot file them under the new id, then
            # load the new character's snapshot.
            self._inventory_cache_loaded = False
            self._inventory_cache_note = ""
            self._inventory_cache_dirty = False
            self._inventory_core_seen = False
            self._inventory_full_bags.clear()
            self.engine.world.inventory = {}
            if self._inventory_cache_source is not None:
                GLib.source_remove(self._inventory_cache_source)
                self._inventory_cache_source = None
            if self._inventory_list is not None:
                self._inventory_schedule_render()
        if previous is None or changed:
            # Only on the first POS (or a character change): this path runs
            # on every packet, so do not rewrite the cache each time.
            self._maybe_load_inventory_cache()
            self._save_inventory_cache()
        self._update_char_info()

    def do_shutdown(self) -> None:
        self._closing = True
        self._client_generation += 1
        self._stop_bot()
        self._minigame_stop(quiet=True)
        if self.minigame_config_path is not None and (
            self._minigame_touched
            or self.minigame_config_path.is_file()
        ):
            self._minigame_save_config()
        if self._speed_boost_source is not None:
            GLib.source_remove(self._speed_boost_source)
            self._speed_boost_source = None
        # Restore the client's normal speed before the link goes away. The
        # DLL also reverts when the host disconnects, but an explicit 0 makes
        # the shutdown deterministic.
        if self.client is not None and self.client.connected:
            try:
                self.client.send_raw("SPEED|0")
                self.client.send_raw("CAST|0")
            except (ConnectionLost, ValueError):
                pass
        if self._inventory_cache_source is not None:
            GLib.source_remove(self._inventory_cache_source)
            self._inventory_cache_source = None
        self._save_inventory_cache()
        self._close_mirror_writer(remove=True)
        self.mirror_reader.close()
        if self.bazar.running:
            self.bazar.stop("shutdown")
        if self.bazar_timer is not None:
            GLib.source_remove(self.bazar_timer)
            self.bazar_timer = None
        # Do not create a stray bazar.json in a window that never used it.
        if self._bazar_config_loaded or self.bazar.config.items:
            self._bazar_save_config(quiet=True)
        if self.attached_client is not None:
            release_client_lock(self.attached_client.winpid)
            self.attached_client = None
        if self.client is not None:
            self.client.close()
        Gtk.Application.do_shutdown(self)


def run_gui(
    host: str = "127.0.0.1",
    port: int | None = None,
    map_data: str | None = None,
    log_file: str | None = None,
    select_client: bool = True,
) -> int:
    app = Application(
        host,
        port,
        map_data,
        unique=False,
        log_file=log_file,
        select_client=select_client,
    )
    return int(app.run([]))
