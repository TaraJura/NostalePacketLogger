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
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from .bazar import (
    BazarController,
    BazarError,
    BazarItem,
    build_search_packet,
    default_config_path as default_bazar_config_path,
    load_config as load_bazar_config,
    normalize_search_packet,
    save_config as save_bazar_config,
)
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
from .clients import (
    ClientInfo,
    Discovery,
    acquire_client_lock,
    client_lock_owner,
    discover_clients,
    inject_client,
    release_client_lock,
    wait_for_port,
)
from .engine import Engine, Entity, GroundItem, InventorySlot
from .mapdata import (
    DEFAULT_NSTC,
    MapDataError,
    MapGrid,
    add_portal,
    astar,
    choose_map_for_positions,
    is_time_space_map,
    load_nstc,
    load_portals as load_saved_portals,
    normalize_portals,
    save_portals,
    set_gate,
    terrain_rgb,
)
from .monsternames import load_monster_names, load_skill_descriptions
from .mirror import (
    KIND_MOVE,
    KIND_SPELL,
    MirrorReader,
    MirrorWriter,
)
from .spawnlog import (
    client_log_path,
    load_bazaar_open,
    load_gates,
    load_inventory as load_log_inventory,
    load_last_position,
    load_player_spawns,
    load_portals as load_log_portals,
    load_skill_list,
    load_skill_slots,
    load_spawns,
    load_timespaces,
)
from .spells import SpellInfo, load_spells
from .protocol import Kind, Packet, ProtocolError

#: nosapki.com's Time-Space stone icon (64x64 PNG), drawn on the map at a
#: `gp` destination in the 2xxx range. Missing assets fall back to the ring.
ASSET_DIR = Path(__file__).with_name("assets")
TIMESPACE_ICON = ASSET_DIR / "ts-1.png"

MAX_ROWS = 20000
TRIM_ROWS = 2000
#: Bot action log: enough history to debug a fight or a walk, trimmed in
#: chunks so a long session does not grow the text buffer forever.
LOG_MAX_LINES = 4000
LOG_TRIM_LINES = 500
#: The Bot log tab is mirrored to this file so the log is readable outside the
#: GUI (`tail -f`), survives a restart, and can be inspected by an agent.
#: NTPL_BOT_LOG overrides the path for every window; without it each window
#: moves to bot-<winpid>.log when it attaches, so several GUIs never interleave
#: their actions into one file (which looks like one bot switching targets).
#: The file rotates once when it gets large.
BOT_LOG_OVERRIDE = os.environ.get("NTPL_BOT_LOG")
BOT_LOG_PATH = Path(
    BOT_LOG_OVERRIDE or str(Path.home() / ".config/ntpl" / "bot.log")
)
BOT_LOG_MAX_BYTES = 4 * 1024 * 1024
DRAIN_INTERVAL_MS = 50
BOT_INTERVAL_MS = 50
#: Click-to-walk tick; the next leg is handed to the client before the
#: character reaches the end of the current one, so the walk never stops at a
#: leg boundary. POS is polled faster while travelling to see the real cell.
TRAVEL_INTERVAL_MS = 100
TRAVEL_HANDOFF_CELLS = 1
TRAVEL_MIN_LEG_S = 0.35
TRAVEL_POS_POLL_S = 0.2
TRAVEL_LEG_TIMEOUT_S = 5.0
DEFAULT_SCALE = 3
# Live positions fed to the attach-time map ranking. More points separate
# same-layout maps; ranking 779 grids costs ~16 ms at this cap.
AUTO_MAP_POINTS = 512
# The server ignores a walk sent during the skill animation. The bot attacks,
# then waits this long before it sends the next path step.
ATTACK_WALK_DELAY = 0.4
#: Target acquisition radius when the Combat search field is gone; the
#: equipped skill's own range is used when it is larger.
ATTACK_SEARCH_CELLS = 15
#: The client's MoveTo is animated and accepted by the server only for short
#: hops. A longer destination makes the client send a single far `walk` that
#: the server rejects, leaving the character standing still, so every bot leg
#: is chopped to at most this many cells.
MAX_MOVE_LEG_CELLS = 3
#: Consecutive `cancel` replies to attacks on the same target. A target that
#: lands hits resets the streak, so strict focus keeps it until death; a
#: client-only ghost (a monster that died while the bot was dead/out of range)
#: refuses every hit forever and is dropped after this many, with a status
#: line naming it. High enough that a live fight never reaches it.
MAX_CANCEL_STREAK = 3
#: Unresolved casts kept to pair `su`/`cancel` replies with the attack that
#: caused them. With a short skill cooldown the next attack is already sent
#: when the previous cast's cancel arrives, so a cancel does not necessarily
#: belong to the newest attack.
CAST_PENDING_MAX = 8
CAST_PENDING_TTL_S = 3.0
#: Refused attacks within this window (across targets) pause combat. The
#: server refuses everything in a town/safe zone or through walls; hopping to
#: the next target just looks like a robot spamming `u_s`.
CANCEL_WINDOW_S = 10.0
CANCEL_PAUSE_S = 30.0
CANCEL_PAUSE_TRIGGER = 3
#: TARGET selection failures (the client has no object for the id — usually a
#: monster killed while out of view, which the server never reports with
#: `out`) within this window pause combat briefly instead of hopping through
#: a burst of ghost ids.
TARGET_FAIL_WINDOW_S = 10.0
TARGET_FAIL_TRIGGER = 3
TARGET_FAIL_PAUSE_S = 5.0
#: The server only accepts `get` when the character is on or next to the
#: drop, so the bot walks to everything farther than this.
PICKUP_RANGE_CELLS = 1
#: Drops farther than this are ignored (usually leftovers from other fights).
LOOT_APPROACH_CELLS = 20
#: Keep this close to a followed player; stop walking inside this radius.
FOLLOW_STOP_CELLS = 2
#: `rest 1 1 <charId>` is a toggle, and its confirmation can lag when several
#: clients share the prefix. Re-sending too early stands the character back
#: up, so recovery never finishes and the character appears to sit/stand at
#: random. Wait this long before repeating a sit/stand request.
REST_RETRY_S = 5.0
#: `_finish_kill_first` only defers recovery while the bot is actually
#: attacking. A stale `fight_target_id` (a kill whose `st` was missed, or a
#: fight that stopped) used to block sitting forever, so recovery only
#: "worked" after Stop/Start.
FINISH_KILL_GRACE_S = 12.0
#: How long the followed player's last `su` target stays valid for
#: "attack the same target as the player". A new hit refreshes it.
FOLLOW_TARGET_TTL_S = 5.0
#: While following, only drops this close to the followed player are taken,
#: so the follower never wanders away from the player for loot.
FOLLOW_LOOT_RADIUS = 10
#: Mirror mode: a movement the followed player made is worth replaying while
#: it is fresh; a stale cell would walk the bot backwards.
MIRROR_MOVE_TTL_S = 4.0
#: A mirrored spell is a reaction to the followed player's cast, not a queued
#: rotation: older entries are dropped instead of cast late.
MIRROR_CAST_TTL_S = 5.0
#: The followed player's own host publishes its packets to the mirror bus.
#: While those events arrive, the server's broadcast `mv`/`su` fallback is
#: ignored to avoid doing the same action twice.
MIRROR_BUS_GRACE_S = 1.5
#: A publisher and the server broadcast describe the same cast; the second
#: copy within this window is dropped.
MIRROR_DEDUPE_S = 1.0
#: Pending mirrored casts kept at once (a splash rotation can burst).
MIRROR_CAST_QUEUE_MAX = 32
#: Minimum gap between mirrored casts. The leader's own cooldowns pace the
#: real rotation; this only keeps one bot tick from firing several queued
#: spells back to back.
MIRROR_CAST_MIN_INTERVAL_S = 0.25
#: A waypoint counts as reached within this Chebyshev distance; standing on
#: the route's last waypoint also means "the next leg is the first one".
ROUTE_ARRIVE_CELLS = 1
#: Pause after a `get` before the next pickup. The server confirms with
#: `get 1 <charId> <dropId> 0` within a few ticks, so a pile of drops at the
#: feet is collected at about three per second.
PICKUP_COOLDOWN_S = 0.3
DEFAULT_SKILL_TEMPLATE = "u_s {skill} {type} {target}"

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
# Target selection goes through the client's own selector in the DLL:
#   TARGET|entityId -> the client highlights the monster and sends its ncif
# The server validates u_s against the client's local selection, so an
# injected ncif alone is not enough.
TARGET_PREFIX = "TARGET|"
# A monster the client refuses to select is skipped this long.
TARGET_BLOCK_S = 5.0
#: A monster that hit the character this recently is the Retaliate priority.
RETALIATE_WINDOW_S = 5.0
#: A target that landed a hit this recently is alive: a refused cast is
#: pacing/state (skill cooldown, late target selection), not a ghost.
HIT_TARGET_GRACE_S = 30.0


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
        # Startup client selector: the window lists the running game windows
        # and injects/attaches to the chosen one instead of always dialing the
        # default port. Off by default so headless tests stay deterministic.
        self.select_client = select_client
        self.client_label: str | None = None
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
        # -- cross-client packet mirroring (Follow frame) ------------------
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
        self.bazar = BazarController(self._bazar_send, self._bazar_log)
        self.bazar_timer = None
        self.bazar_page = None
        self.bazar_config_path = default_bazar_config_path()
        self.bazar_config_label = None
        self.bazar_info = None
        self.bazar_status = None
        self.bazar_results_box = None
        self.bazar_items_list = None
        self.bazar_items_info = None
        self.bazar_only_priced = None
        self.bazar_results_info = None
        self.bazar_form: dict[str, Gtk.Widget] = {}
        self.bazar_selected_name: str | None = None
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
        self.bazar_live_relist = None
        self.bazar_refresh_spin = None
        self.bazar_npc_entry = None
        self.bazar_character_entry = None
        self._bazar_render_version = -1
        self._bazar_character_autofilled = False
        self._bazar_npc_autofilled = False
        self._bazar_npc_autofill_value = ""
        self._bazar_config_loaded = False
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

    def _copy_text(self, text: str) -> None:
        """Put an item name on the system clipboard and confirm it."""
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
        self._set_status(f"copied item name: {text}")

    def _bazar_copy_menu(self, anchor: Gtk.Widget, name: str) -> None:
        """A right-click menu with the one action that matters here."""
        menu = Gio.Menu()
        item = Gio.MenuItem.new("Copy item name", None)
        item.set_action_and_target_value(
            "app.copy-item-name", GLib.Variant("s", name)
        )
        menu.append_item(item)
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_has_arrow(False)
        popover.set_parent(anchor)
        popover.connect("closed", lambda p: p.unparent())
        popover.popup()

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
        window = Gtk.ApplicationWindow(application=self, title="NosTale Packet Logger")
        window.set_default_size(1280, 820)
        self.window = window

        header = Gtk.HeaderBar()
        window.set_titlebar(header)

        clear = Gtk.Button(label="Clear")
        clear.connect("clicked", lambda *_: self.store.remove_all())
        header.pack_start(clear)

        client_button = Gtk.Button(label="Client…")
        client_button.set_tooltip_text(
            "List the running NosTale clients and attach to one of them"
        )
        client_button.connect(
            "clicked", lambda *_: self._begin_client_selection(auto_single=False)
        )
        header.pack_start(client_button)

        packet_page = self._build_packet_page()
        map_page = self._build_map_page()
        log_page = self._build_log_page()
        bazar_page = self._build_bazar_page()

        self.notebook = Gtk.Notebook()
        self.notebook.append_page(packet_page, Gtk.Label(label="Packets"))
        self.notebook.append_page(map_page, Gtk.Label(label="Map / Bot"))
        self.notebook.append_page(log_page, Gtk.Label(label="Bot log"))
        # Appended last: the GUI smokes and the README shots assume Map / Bot
        # is page 1 (Packets is 0).
        self.notebook.append_page(bazar_page, Gtk.Label(label="NosBazar"))
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

    def _build_log_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_top(6)
        toolbar.set_margin_start(6)
        toolbar.set_margin_end(6)
        clear = Gtk.Button(label="Clear log")
        clear.connect("clicked", lambda *_: self._clear_bot_log())
        toolbar.append(clear)
        hint = Gtk.Label(
            label="Bot actions, target selections and DLL errors, newest "
            "at the bottom."
        )
        hint.add_css_class("dim-label")
        hint.set_xalign(0)
        toolbar.append(hint)
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

    # -- NosBazar -------------------------------------------------------

    def _bazar_send(self, packet: str) -> bool:
        """Controller send callback: one game packet through the client."""
        if self.client is None or not self.client.connected:
            self._bazar_log("not connected")
            return False
        try:
            self.client.send_packet(packet)
        except (ConnectionLost, ValueError) as exc:
            self._bazar_log(f"send failed: {exc}")
            return False
        return True

    def _bazar_log(self, text: str) -> None:
        self._log_line(text)
        if self.bazar_status is not None:
            self.bazar_status.set_text(text.strip())

    @staticmethod
    def _bazar_path_for(winpid: int) -> Path:
        override = os.environ.get("NTPL_BAZAR_CONFIG")
        if override:
            return Path(override)
        # Honour XDG_CONFIG_HOME via the shared default (bazar.json lives in
        # the same directory); only the file name changes per client.
        return default_bazar_config_path().with_name(
            f"bazar-{winpid}.json"
        )

    def _bazar_load_default_config(self) -> None:
        path = default_bazar_config_path()
        try:
            if not path.is_file() or path.stat().st_size <= 2:
                return
        except OSError:
            return
        self.bazar_config_path = path
        self._bazar_load_config(path, fallback=False)

    def _bazar_load_config(
        self, path: str | Path | None = None, fallback: bool = True
    ) -> bool:
        """Load the per-client config, optionally seeding from bazar.json."""
        target = Path(path) if path is not None else self.bazar_config_path
        candidates = [target]
        default = default_bazar_config_path()
        if fallback and default != target:
            candidates.append(default)
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                config = load_bazar_config(candidate)
            except BazarError as exc:
                self._bazar_log(f"config: {exc}")
                return False
            self._bazar_stop()
            self.bazar.config = config
            self.bazar.set_items(
                config.items,
                character=config.character,
                refresh=config.refresh,
            )
            self.bazar.config.npc_id = config.npc_id
            self.bazar.config.open_option = config.open_option
            self.bazar.config.open_page = config.open_page
            if self.bazar_character_entry is not None:
                self.bazar_character_entry.set_text(config.character)
            if self.bazar_npc_entry is not None:
                self.bazar_npc_entry.set_text(
                    "" if config.npc_id is None else str(config.npc_id)
                )
            self._bazar_npc_autofilled = False
            self._bazar_npc_autofill_value = ""
            if self.bazar_refresh_spin is not None:
                self.bazar_refresh_spin.set_value(config.refresh)
            self.bazar_config_path = target
            self._bazar_config_loaded = True
            if self.bazar_config_label is not None:
                self.bazar_config_label.set_text(str(target))
            self._bazar_render_items()
            self._bazar_render_results()
            self._bazar_update_info()
            self._bazar_log(
                f"loaded {len(config.items)} NosBazar item(s) from "
                f"{candidate.name}"
            )
            return True

        self._bazar_stop()
        self.bazar.set_items([])
        self.bazar_selected_name = None
        if self.bazar_config_label is not None:
            self.bazar_config_label.set_text(str(target))
        self._bazar_render_items()
        self._bazar_render_results()
        return False

    def _bazar_apply_settings(self) -> None:
        """Copy the character / NPC / refresh widgets into the config."""
        if self.bazar_character_entry is not None:
            text = self.bazar_character_entry.get_text().strip()
            if not text and self.engine.world.self_name:
                text = self.engine.world.self_name
                self.bazar_character_entry.set_text(text)
            self.bazar.config.character = text
        if self.bazar_refresh_spin is not None:
            self.bazar.config.refresh = float(
                self.bazar_refresh_spin.get_value()
            )
        if self.bazar_npc_entry is not None:
            text = self.bazar_npc_entry.get_text().strip()
            if text.isdigit() and int(text) > 0:
                self.bazar.config.npc_id = int(text)
            else:
                self.bazar.config.npc_id = None

    def _bazar_save_config(self, quiet: bool = False) -> bool:
        self._bazar_apply_settings()
        state = self.engine.world.bazar
        if state.option_learned:
            # Persist the dialog option actually used on this server; the
            # entity id itself is per map, so only the option/page go in.
            self.bazar.config.open_option = state.open_option
            self.bazar.config.open_page = state.open_page
        try:
            save_bazar_config(self.bazar_config_path, self.bazar.config)
        except BazarError as exc:
            self._bazar_log(f"config: {exc}")
            return False
        self._bazar_config_loaded = True
        if self.bazar_config_label is not None:
            self.bazar_config_label.set_text(str(self.bazar_config_path))
        if not quiet:
            self._bazar_log(f"saved {self.bazar_config_path}")
        return True

    def _bazar_start_timer(self) -> None:
        if self.bazar_timer is None:
            self.bazar_timer = GLib.timeout_add(100, self._bazar_tick)

    def _bazar_stop(self, reason: str = "") -> None:
        if self.bazar_timer is not None:
            GLib.source_remove(self.bazar_timer)
            self.bazar_timer = None
        if self.bazar.running:
            self.bazar.stop(reason)
        # Never leave the live-relist gate armed after a stop.
        if self.bazar_live_relist is not None:
            self.bazar_live_relist.set_active(False)
        self._bazar_update_info()

    def _bazar_open(self) -> None:
        if self.client is None or not self.client.connected:
            self._bazar_log("not connected")
            return
        self._bazar_apply_settings()
        if not self.bazar.open_bazaar(self.engine.world):
            self._bazar_log(self.bazar.last_error or "cannot open NosBazar")
            return
        self._bazar_start_timer()
        self._bazar_update_info()

    def _bazar_start_monitor(self) -> None:
        if self.client is None or not self.client.connected:
            self._bazar_log("not connected")
            return
        self._bazar_apply_settings()
        if not self.bazar.start_monitor(self.engine.world):
            self._bazar_log(self.bazar.last_error or "cannot start monitor")
            return
        self._bazar_start_timer()
        self._bazar_render_results()
        self._bazar_update_info()

    def _bazar_start_relist(self) -> None:
        if self.bazar_live_relist is None or not self.bazar_live_relist.get_active():
            self._bazar_log(
                "relisting is disabled; tick 'Allow live re-listing' first"
            )
            return
        if self.client is None or not self.client.connected:
            self._bazar_log("not connected")
            return
        self._bazar_apply_settings()
        if not self.bazar.start_relist(self.engine.world):
            self._bazar_log(self.bazar.last_error or "cannot start relist")
            return
        self._bazar_start_timer()
        self._bazar_render_results()
        self._bazar_update_info()

    def _bazar_tick(self) -> bool:
        if self._closing:
            self.bazar_timer = None
            return False
        if not self.bazar.running:
            self.bazar_timer = None
            self._bazar_update_info()
            return False
        if self.client is None or not self.client.connected:
            self.bazar.stop("disconnected")
            self.bazar_timer = None
            self._bazar_update_info()
            return False
        self.bazar.tick(self.engine.world)
        if self.bazar.version != self._bazar_render_version:
            self._bazar_render_results()
        self._bazar_update_info()
        if not self.bazar.running:
            # "Open bazaar" stops by itself once the window answers.
            self.bazar_timer = None
            return False
        return True

    def _bazar_update_info(self) -> None:
        if self.bazar_info is None:
            return
        if not self.bazar.running:
            state = "bazaar open" if self.engine.world.bazar.open else "idle"
            self.bazar_info.set_text(f"NosBazar {state}")
            return
        phase = {
            "open": "opening",
            "waiting": "waiting for Bibi Basar",
            "pass": "searching",
            "cooldown": "refresh wait",
            "idle": "idle",
        }.get(self.bazar.phase, self.bazar.phase)
        summary = self.bazar.summary
        suffix = f" · {summary}" if summary else ""
        self.bazar_info.set_text(
            f"{self.bazar.mode} · {phase}{suffix} · "
            f"refresh {self.bazar.config.refresh:g}s"
        )

    def _bazar_on_packet(self, packet: Packet) -> None:
        if packet.opcode in ("c_map", "mapout"):
            # Entity ids belong to a map instance. Drop an auto-filled id so
            # the controller uses the new map's Bibi (or the user reopens).
            if (
                self._bazar_npc_autofilled
                and self.bazar_npc_entry is not None
            ):
                self.bazar_npc_entry.set_text("")
                self._bazar_npc_autofilled = False
                self._bazar_npc_autofill_value = ""
            return
        if packet.opcode in (
            "rc_blist", "c_blist", "c_slist", "npc_req", "n_run", "in",
            "wopen",
        ):
            self._bazar_sync_from_world()

    def _bazar_sync_from_world(self) -> None:
        world = self.engine.world
        if (
            self.bazar_character_entry is not None
            and not self._bazar_character_autofilled
            and not self.bazar_character_entry.get_text().strip()
            and world.self_name
        ):
            self.bazar_character_entry.set_text(world.self_name)
            self._bazar_character_autofilled = True
        if (
            self.bazar_npc_entry is not None
            and world.bazar.npc_id is not None
        ):
            current = self.bazar_npc_entry.get_text().strip()
            if not current or (
                self._bazar_npc_autofilled
                and current == self._bazar_npc_autofill_value
            ):
                value = str(world.bazar.npc_id)
                self.bazar_npc_entry.set_text(value)
                self._bazar_npc_autofilled = True
                self._bazar_npc_autofill_value = value

    def _bazar_render_items(self) -> None:
        if self.bazar_items_list is None:
            return
        child = self.bazar_items_list.get_first_child()
        while child is not None:
            self.bazar_items_list.remove(child)
            child = self.bazar_items_list.get_first_child()
        items = self.bazar.config.items
        enabled = sum(1 for item in items if item.enabled and item.search_packet)
        if self.bazar_items_info is not None:
            self.bazar_items_info.set_text(
                f"{len(items)} item(s) · {enabled} with a search"
            )
        for item in items:
            row = Gtk.ListBoxRow()
            row.item_name = item.name  # type: ignore[attr-defined]
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            name = Gtk.Label(label=item.name)
            name.set_xalign(0)
            name.set_hexpand(True)
            # Full name, wrapped instead of ellipsized: the list is the
            # place to identify an item, so a cut-off label is useless.
            name.set_wrap(True)
            name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            name.set_max_width_chars(34)
            detail = Gtk.Label(
                label=(
                    f"{' '.join(str(v) for v in item.vnums) or 'no search'}"
                    f" · {item.cost_text()}"
                    f" · min {item.min_price:,}"
                )
            )
            detail.set_xalign(0)
            detail.add_css_class("dim-label")
            detail.set_ellipsize(Pango.EllipsizeMode.END)
            detail.set_max_width_chars(34)
            box.append(name)
            box.append(detail)
            row.set_child(box)
            right = Gtk.GestureClick()
            right.set_button(3)

            def on_right(
                _gesture, _n_press, _x, _y, row=row, name=item.name
            ) -> None:
                self.bazar_selected_name = name
                self.bazar_items_list.select_row(row)
                self._bazar_copy_menu(row, name)

            right.connect("pressed", on_right)
            row.add_controller(right)
            self.bazar_items_list.append(row)
        selected = None
        child = self.bazar_items_list.get_first_child()
        while child is not None:
            if getattr(child, "item_name", None) == self.bazar_selected_name:
                selected = child
                break
            child = child.get_next_sibling()
        if selected is not None:
            self.bazar_items_list.select_row(selected)
        else:
            self.bazar_items_list.unselect_all()

    def _bazar_item_selected(
        self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None
    ) -> None:
        if row is None:
            return
        name = getattr(row, "item_name", None)
        if not name:
            return
        for item in self.bazar.config.items:
            if item.name == name:
                self.bazar_selected_name = item.name
                self._bazar_form_from_item(item)
                return

    @staticmethod
    def _bazar_standard_search(packet: str | None) -> bool:
        if not packet:
            return True
        parts = packet.split()
        return (
            len(parts) >= 10
            and parts[0] == "c_blist"
            and all(part == "0" for part in parts[1:9])
        )

    def _bazar_form_from_item(self, item: BazarItem) -> None:
        form = self.bazar_form
        if not form:
            return
        self.bazar_selected_name = item.name
        form["name"].set_text(item.name)
        if self._bazar_standard_search(item.search_packet):
            form["vnums"].set_text(
                " ".join(str(vnum) for vnum in item.vnums)
            )
            form["raw"].set_text("")
        else:
            form["vnums"].set_text("")
            form["raw"].set_text(item.search_packet or "")
        form["vnum"].set_text("" if item.vnum is None else str(item.vnum))
        form["tab"].set_selected(
            0 if item.inv_tab is None else item.inv_tab + 1
        )
        form["amount"].set_value(item.amount)
        form["min"].set_value(item.min_price)
        value, unit = item.cost()
        if unit == "ND":
            form["cost_type"].set_selected(1)
            form["cost"].set_value(value or 0)
        elif unit == "g":
            form["cost_type"].set_selected(2)
            form["cost"].set_value(value or 0)
        else:
            form["cost_type"].set_selected(0)
            form["cost"].set_value(0)
        form["unk1"].set_value(item.unk1)
        form["unk2"].set_value(item.unk2)
        form["durability"].set_value(item.durability)
        form["medal"].set_value(item.medal)

    def _bazar_new_item(self) -> None:
        self.bazar_selected_name = None
        if self.bazar_items_list is not None:
            self.bazar_items_list.unselect_all()
        form = self.bazar_form
        if not form:
            return
        form["name"].set_text("")
        form["vnums"].set_text("")
        form["raw"].set_text("")
        form["vnum"].set_text("")
        form["tab"].set_selected(0)
        form["amount"].set_value(5)
        form["min"].set_value(0)
        form["cost_type"].set_selected(0)
        form["cost"].set_value(0)
        form["unk1"].set_value(9)
        form["unk2"].set_value(4)
        form["durability"].set_value(1)
        form["medal"].set_value(2)
        form["name"].grab_focus()

    def _bazar_delete_item(self) -> None:
        if not self.bazar_selected_name:
            self._bazar_log("select an item to delete")
            return
        items = [
            item
            for item in self.bazar.config.items
            if item.name != self.bazar_selected_name
        ]
        self._bazar_log(f"deleted {self.bazar_selected_name}")
        self.bazar_selected_name = None
        self._bazar_set_items(items)
        self._bazar_new_item()
        self._bazar_save_config(quiet=True)

    def _bazar_item_from_form(self) -> BazarItem | None:
        form = self.bazar_form
        if not form:
            return None
        name = form["name"].get_text().strip()
        if not name:
            self._bazar_log("item name is required")
            return None
        raw = form["raw"].get_text().strip()
        try:
            if raw:
                search_packet = normalize_search_packet(raw)
            else:
                vnums: list[int] = []
                for text in form["vnums"].get_text().replace(",", " ").split():
                    value = int(text)
                    if value <= 0:
                        raise ValueError(text)
                    vnums.append(value)
                search_packet = build_search_packet(vnums) if vnums else None
        except (ValueError, BazarError) as exc:
            self._bazar_log(f"search packet: {exc}")
            return None
        vnum_text = form["vnum"].get_text().strip()
        vnum = None
        if vnum_text:
            if not vnum_text.isdigit() or int(vnum_text) <= 0:
                self._bazar_log("item vnum must be a positive number")
                return None
            vnum = int(vnum_text)
        if search_packet is None and vnum is None:
            self._bazar_log("an item needs a search and/or an item vnum")
            return None
        tab_index = form["tab"].get_selected()
        inv_tab = None if tab_index == 0 else tab_index - 1
        cost_index = form["cost_type"].get_selected()
        cost = float(form["cost"].get_value())
        return BazarItem(
            name=name,
            search_packet=search_packet,
            vnum=vnum,
            inv_tab=inv_tab,
            amount=int(form["amount"].get_value()),
            min_price=int(form["min"].get_value()),
            nos_cost=cost if cost_index == 1 and cost > 0 else None,
            npc_cost=cost if cost_index == 2 and cost > 0 else None,
            unk1=int(form["unk1"].get_value()),
            unk2=int(form["unk2"].get_value()),
            durability=int(form["durability"].get_value()),
            medal=int(form["medal"].get_value()),
        )

    def _bazar_save_item(self) -> None:
        item = self._bazar_item_from_form()
        if item is None:
            return
        items = list(self.bazar.config.items)
        old_name = self.bazar_selected_name
        if old_name is not None:
            index = next(
                (
                    index
                    for index, existing in enumerate(items)
                    if existing.name == old_name
                ),
                None,
            )
        else:
            index = None
        for position, existing in enumerate(items):
            if existing.name == item.name and position != index:
                self._bazar_log(f"an item named '{item.name}' already exists")
                return
        if index is None:
            items.append(item)
        else:
            items[index] = item
        self._bazar_apply_settings()
        self.bazar_selected_name = item.name
        self._bazar_set_items(items)
        self._bazar_save_config(quiet=True)

    def _bazar_set_items(self, items: list[BazarItem]) -> None:
        # A running pass holds the old item objects; stop rather than mix the
        # old pending search with a new item list.
        if self.bazar.running:
            self._bazar_stop("items changed")
        self.bazar.set_items(
            items,
            character=self.bazar.config.character,
            refresh=self.bazar.config.refresh,
        )
        self._bazar_render_items()
        self._bazar_render_results()

    def _load_nosmall_groups(self) -> list:
        """Read groups from the SQLite DB, or parse the client data."""
        try:
            from . import database
            if database.DEFAULT_DB_PATH.is_file():
                connection = database.connect()
                try:
                    groups = database.nosmall_groups(connection, limit=5000)
                finally:
                    connection.close()
                if groups:
                    return groups
        except Exception:  # noqa: BLE001 - fall back to the archive parser
            pass
        from .nosmall import load_catalog
        return load_catalog()

    def _nosmall_costs(self) -> dict[int, float]:
        """ND costs known for catalog groups.

        The curated reference map is the base; an item already in this
        window's list (for example one added by hand with its Cost edited)
        contributes its own vnums, so the importer can learn new prices.
        """
        try:
            from .nosmall import known_reference_costs
            costs = dict(known_reference_costs())
        except Exception:  # noqa: BLE001
            costs = {}
        for item in self.bazar.config.items:
            if item.nos_cost is None or item.nos_cost <= 0:
                continue
            vnums = item.vnums or (
                [item.vnum] if item.vnum is not None else []
            )
            for vnum in vnums:
                costs.setdefault(int(vnum), float(item.nos_cost))
        return costs

    @staticmethod
    def _nosmall_group_cost(group, costs: dict[int, float]) -> float | None:
        from .nosmall import group_cost
        return group_cost(group, costs)

    def _nosmall_importable(self) -> list:
        """Catalog groups worth importing: priced, non-pet items only.

        A group without an ND cost cannot produce a profit figure, so it is
        left out entirely; pets, fairies and partner supplies are companion
        items and are always excluded.
        """
        from .nosmall import importable_groups

        return importable_groups(self._nosmall_groups, self._nosmall_costs())

    def _open_nosmall_picker(self) -> None:
        if self.nosmall_window is not None:
            self.nosmall_window.present()
            return
        groups = self._load_nosmall_groups()
        self._nosmall_groups = groups
        importable = len(self._nosmall_importable())
        window = Gtk.Window(title="NosMall catalog")
        window.set_application(self)
        window.set_transient_for(self.window)
        window.set_modal(True)
        window.set_default_size(780, 620)
        window.connect("close-request", self._close_nosmall_picker)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        search = Gtk.SearchEntry()
        search.set_placeholder_text(
            "search catalog by item name, vnum or base vnum"
        )
        search.connect(
            "search-changed",
            lambda entry: self._nosmall_render(entry.get_text()),
        )
        self._nosmall_search = search
        box.append(search)
        hint = Gtk.Label(
            label=(
                f"{importable} importable NosMall item(s) with a known ND "
                "cost. Items without an ND cost cannot show a profit and are "
                "not listed; pets, fairies and partner supplies are excluded. "
                "To offer another item, add it with New and set its Cost "
                "first — or maintain the curated price map — then reopen this "
                "window."
            )
        )
        hint.set_xalign(0)
        hint.set_wrap(True)
        hint.add_css_class("dim-label")
        box.append(hint)
        self._nosmall_status = Gtk.Label(label="")
        self._nosmall_status.set_xalign(0)
        self._nosmall_status.add_css_class("dim-label")
        box.append(self._nosmall_status)
        self._nosmall_list = Gtk.ListBox()
        self._nosmall_list.set_activate_on_single_click(True)
        self._nosmall_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self._nosmall_list)
        scrolled.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        scrolled.set_vexpand(True)
        box.append(scrolled)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._nosmall_add_all_button = Gtk.Button(
            label="Add all matching"
        )
        self._nosmall_add_all_button.set_tooltip_text(
            "Add every group matching the current search in one go. A full "
            "catalog pass is long: each group is searched with 2.5 s pacing."
        )
        self._nosmall_add_all_button.connect(
            "clicked", lambda *_: self._nosmall_add_all()
        )
        buttons.append(self._nosmall_add_all_button)
        prune = Gtk.Button(label="Remove unpriced & pets")
        prune.set_tooltip_text(
            "Remove every item from this window's item list that has no ND "
            "cost, plus every pet, fairy and partner item. Priced ordinary "
            "items are kept. Click twice to confirm; a running monitor is "
            "stopped because the item list changes."
        )
        prune.connect("clicked", lambda *_: self._nosmall_prune())
        buttons.append(prune)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: window.close())
        buttons.append(close)
        box.append(buttons)
        window.set_child(box)
        self.nosmall_window = window
        self._nosmall_render("")
        window.present()

    def _nosmall_render(self, query: str) -> None:
        if self._nosmall_list is None:
            return
        child = self._nosmall_list.get_first_child()
        while child is not None:
            self._nosmall_list.remove(child)
            child = self._nosmall_list.get_first_child()

        needle = query.strip().casefold()
        importable = self._nosmall_importable()
        costs = self._nosmall_costs()
        matches = []
        for group in importable:
            if not needle:
                matches.append(group)
                continue
            if needle in group.name.casefold():
                matches.append(group)
                continue
            if any(needle in str(vnum) for vnum in group.vnums):
                matches.append(group)
                continue
            if needle == str(group.base_vnum):
                matches.append(group)
        self._nosmall_matches = matches
        shown = matches[:300]
        if self._nosmall_add_all_button is not None:
            self._nosmall_add_all_button.set_label(
                f"Add all matching ({len(matches)})"
            )
            self._nosmall_add_all_button.set_sensitive(bool(matches))
        if self._nosmall_status is not None:
            extra = (
                f" · showing first {len(shown)}"
                if len(matches) > len(shown)
                else ""
            )
            self._nosmall_status.set_text(
                f"{len(matches)} matching priced, non-pet group(s){extra}"
            )
        for group in shown:
            cost = self._nosmall_group_cost(group, costs)
            row = Gtk.ListBoxRow()
            content = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )
            name = Gtk.Label(label=group.name)
            name.set_xalign(0)
            name.set_hexpand(True)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(46)
            detail = Gtk.Label(
                label=(
                    " ".join(str(vnum) for vnum in group.vnums)
                    + (f" · {cost:g} ND" if cost is not None else "")
                )
            )
            detail.set_xalign(1)
            detail.add_css_class("dim-label")
            detail.set_ellipsize(Pango.EllipsizeMode.END)
            detail.set_max_width_chars(32)
            content.append(name)
            content.append(detail)
            row.set_child(content)
            row.connect(
                "activate", lambda _row, group=group: self._nosmall_add(group)
            )
            self._nosmall_list.append(row)

    def _nosmall_item_for(self, group, items: list) -> BazarItem | None:
        """Build a Bazar item for a catalog group, or None if not eligible."""
        from .nosmall import is_pet_group

        if is_pet_group(group):
            if self._nosmall_status is not None:
                self._nosmall_status.set_text(
                    f"{group.name} is a pet/partner item and is excluded"
                )
            return None
        cost = self._nosmall_group_cost(group, self._nosmall_costs())
        if cost is None:
            if self._nosmall_status is not None:
                self._nosmall_status.set_text(
                    f"{group.name} has no ND cost; profit cannot be "
                    "calculated, so it is excluded"
                )
            return None
        try:
            packet = group.search_packet()
        except Exception as exc:  # noqa: BLE001
            if self._nosmall_status is not None:
                self._nosmall_status.set_text(f"cannot build packet: {exc}")
            return None
        if any(item.search_packet == packet for item in items):
            if self._nosmall_status is not None:
                self._nosmall_status.set_text(
                    f"{group.name} is already in the item list"
                )
            return None
        name = group.name[:80]
        used = {item.name for item in items}
        if name in used:
            name = f"{group.name[:70]} [{group.base_vnum}]"
        counter = 2
        while name in used:
            name = f"{group.name[:66]} [{group.base_vnum}/{counter}]"
            counter += 1
        return BazarItem(
            name=name,
            search_packet=packet,
            vnum=group.vnums[0] if group.vnums else group.base_vnum,
            inv_tab=None,
            amount=5,
            min_price=0,
            nos_cost=cost,
        )

    def _nosmall_add(self, group) -> None:
        items = list(self.bazar.config.items)
        item = self._nosmall_item_for(group, items)
        if item is None:
            return
        self._bazar_set_items(items + [item])
        self._bazar_save_config(quiet=True)
        if self._nosmall_status is not None:
            cost = item.cost()[0]
            self._nosmall_status.set_text(
                f"Added {item.name}"
                + (f" ({cost:g} ND)" if cost is not None else "")
                + f" · {len(items) + 1} item(s) configured"
            )

    def _nosmall_add_all(self) -> None:
        groups = list(self._nosmall_matches)
        if not groups:
            return
        items = list(self.bazar.config.items)
        added = 0
        for group in groups:
            item = self._nosmall_item_for(group, items)
            if item is not None:
                items.append(item)
                added += 1
        if not added:
            if self._nosmall_status is not None:
                self._nosmall_status.set_text(
                    "All matching groups are already in the item list"
                )
            return
        self._bazar_set_items(items)
        self._bazar_save_config(quiet=True)
        if self._nosmall_status is not None:
            self._nosmall_status.set_text(
                f"Added {added} group(s) · {len(items)} item(s) configured"
            )

    def _nosmall_prune(self) -> None:
        """Remove unpriced and pet rows from the configured item list.

        Destructive, so it takes two clicks: the first arms the action and
        reports the count, the second within five seconds applies it. Only
        rows with an ND cost that are not pets survive.
        """
        from .nosmall import is_pet_group, split_priced_non_pet

        pet_vnums: set[int] = set()
        for group in self._nosmall_groups:
            if is_pet_group(group):
                pet_vnums.update(group.vnums)
                pet_vnums.add(group.base_vnum)
        keep, removed = split_priced_non_pet(
            list(self.bazar.config.items), pet_vnums
        )
        if not removed:
            if self._nosmall_status is not None:
                self._nosmall_status.set_text(
                    "Nothing to remove: every configured item has an ND "
                    "cost and none is a pet"
                )
            return
        now = time.monotonic()
        armed = (
            self._nosmall_prune_armed_at > 0.0
            and now - self._nosmall_prune_armed_at <= 5.0
        )
        if not armed:
            self._nosmall_prune_armed_at = now
            self._nosmall_prune_count = len(removed)
            if self._nosmall_status is not None:
                self._nosmall_status.set_text(
                    f"Click 'Remove unpriced & pets' again to delete "
                    f"{len(removed)} item(s) and keep {len(keep)}"
                )
            return
        self._nosmall_prune_armed_at = 0.0
        self._nosmall_prune_count = 0
        self._bazar_set_items(keep)
        self._bazar_save_config(quiet=True)
        if self._nosmall_status is not None:
            self._nosmall_status.set_text(
                f"Removed {len(removed)} unpriced/pet item(s) · "
                f"{len(keep)} priced item(s) kept"
            )
        # The picker list itself is unaffected by the item list, but the
        # price map now includes only the kept items' costs.
        self._nosmall_render(
            self._nosmall_search.get_text()
            if getattr(self, "_nosmall_search", None) is not None
            else ""
        )

    def _close_nosmall_picker(self, _window=None) -> bool:
        self.nosmall_window = None
        self._nosmall_list = None
        self._nosmall_status = None
        self._nosmall_matches = []
        self._nosmall_add_all_button = None
        self._nosmall_search = None
        self._nosmall_prune_armed_at = 0.0
        self._nosmall_prune_count = 0
        return False

    def _bazar_render_results(self) -> None:
        if self.bazar_results_box is None:
            return
        child = self.bazar_results_box.get_first_child()
        while child is not None:
            self.bazar_results_box.remove(child)
            child = self.bazar_results_box.get_first_child()
        self._bazar_render_version = self.bazar.version

        items = self.bazar.config.items
        if not items:
            if self.bazar_results_info is not None:
                self.bazar_results_info.set_text(
                    "Cheapest listing per item, most profitable first."
                )
            empty = Gtk.Label(
                label=(
                    "No items yet — add one with the editor on the right. "
                    "The search uses item vnums, not the display name."
                )
            )
            empty.set_xalign(0)
            empty.set_wrap(True)
            empty.add_css_class("dim-label")
            self.bazar_results_box.append(empty)
            return

        quotes = self.bazar.sorted_quotes()
        only_priced = (
            self.bazar_only_priced is not None
            and self.bazar_only_priced.get_active()
        )
        if only_priced:
            visible = [quote for quote in quotes if quote.price is not None]
        else:
            visible = list(quotes)
        hidden = len(quotes) - len(visible)
        if self.bazar_results_info is not None:
            text = "Most profitable first"
            if only_priced:
                text += f" · {len(visible)} priced"
            if hidden:
                text += f" · {hidden} hidden"
            self.bazar_results_info.set_text(text)

        if not visible:
            empty = Gtk.Label(
                label=(
                    "No priced items yet — wait for the pass or untick "
                    "'Only priced' to see timeouts and pending items."
                )
            )
            empty.set_xalign(0)
            empty.set_wrap(True)
            empty.add_css_class("dim-label")
            self.bazar_results_box.append(empty)
            return

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(3)
        grid.set_margin_end(14)
        grid.set_margin_top(2)
        headers = ("Item", "Cost", "NB Price", "Profit", "Seller", "Status")
        aligns = (0.0, 0.0, 1.0, 1.0, 0.0, 0.0)
        for column, (title, align) in enumerate(zip(headers, aligns)):
            label = Gtk.Label()
            label.set_markup(f"<b>{title}</b>")
            label.set_xalign(align)
            grid.attach(label, column, 0, 1, 1)
        for row_index, quote in enumerate(visible, start=1):
            price = "n/a" if quote.price is None else f"{quote.price:,}"
            seller = quote.seller.replace("�", "").strip()
            cells = (
                quote.item.name,
                quote.item.cost_text(),
                price,
                quote.profit_text(),
                seller or "-",
                quote.status_text(),
            )
            for column, (text, align) in enumerate(zip(cells, aligns)):
                label = Gtk.Label(label=text)
                label.set_xalign(align)
                if column == 0:
                    # Render the full item name; wrap long names instead of
                    # cutting them with an ellipsis. 30 chars keeps the price,
                    # seller and status columns visible in the narrow pane.
                    label.set_wrap(True)
                    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                    label.set_max_width_chars(30)
                    label.set_hexpand(True)
                    right = Gtk.GestureClick()
                    right.set_button(3)

                    def on_right(
                        _gesture, _n_press, _x, _y, label=label, name=text
                    ) -> None:
                        self._bazar_copy_menu(label, name)

                    right.connect("pressed", on_right)
                    label.add_controller(right)
                else:
                    label.set_ellipsize(Pango.EllipsizeMode.END)
                    if column == 4:
                        label.set_max_width_chars(18)
                    elif column == 5:
                        label.set_max_width_chars(26)
                if column == 5:
                    if quote.status == "ok":
                        label.add_css_class("bazar-ok")
                    elif quote.status in (
                        "timeout", "bad", "error", "quantity"
                    ):
                        label.add_css_class("bazar-bad")
                    elif quote.status in (
                        "floor", "own", "no-slot", "no-vnum", "register-sent"
                    ):
                        label.add_css_class("bazar-warn")
                    else:
                        label.add_css_class("dim-label")
                if quote.listing is not None:
                    label.set_tooltip_text(
                        " | ".join(quote.listing.raw_fields)
                    )
                grid.attach(label, column, row_index, 1, 1)
        self.bazar_results_box.append(grid)

    def _bazar_seed_from_log(self) -> None:
        """Learn the Bibi id/option and inventory from this client's DLL log.

        A GUI attaches after login: the client's earlier manual bazaar opening
        and the login inventory dump are still in the log, not in the live
        packet stream.  The entity id and the inventory slots are needed
        before the first search/relist.
        """
        path = self.log_file
        if not path:
            return
        world = self.engine.world
        npc_id, option, page = load_bazaar_open(path)
        if npc_id is not None:
            state = world.bazar
            state.npc_id = npc_id
            state.open_option = option if option is not None else state.open_option
            state.open_page = page if page is not None else state.open_page
            state.option_learned = option is not None
            self._bazar_sync_from_world()
            self._bazar_log(
                f"bazaar NPC {npc_id} learned from the client log"
                + (
                    f" (option {option}/{page})"
                    if option is not None
                    else ""
                )
            )
        bags, gold = load_log_inventory(path)
        if bags:
            for bag, slots in bags.items():
                world.inventory[bag] = {
                    index: InventorySlot(bag, index, vnum, quantity)
                    for index, (vnum, quantity) in slots.items()
                }
            count = sum(len(slots) for slots in bags.values())
            if count:
                self._bazar_log(
                    f"inventory: {count} slot(s) in {len(bags)} bag(s) "
                    f"seeded from the log"
                )
        if gold is not None:
            world.gold = gold
        self._bazar_sync_from_world()

    def _bazar_reset_for_attach(self, winpid: int) -> None:
        """Reset the tab together with the engine when another client attaches."""
        self._bazar_stop("client changed")
        self.bazar_selected_name = None
        self._bazar_config_loaded = False
        self._bazar_character_autofilled = False
        self._bazar_npc_autofilled = False
        self._bazar_npc_autofill_value = ""
        self.bazar_config_path = self._bazar_path_for(winpid)
        if self.bazar_config_label is not None:
            self.bazar_config_label.set_text(str(self.bazar_config_path))
        if self.bazar_character_entry is not None:
            self.bazar_character_entry.set_text("")
        if self.bazar_npc_entry is not None:
            self.bazar_npc_entry.set_text("")
        self._bazar_load_config(fallback=True)

    def _build_bazar_page(self):
        """NosBazar market monitor and relisting controls.

        The page is a thin view over :class:`ntpl.bazar.BazarController`:
        buttons start/stop it, the item list edits its config, and the left
        side renders the controller's latest quotes.
        """
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.set_margin_top(6)
        page.set_margin_start(8)
        page.set_margin_end(8)
        page.set_margin_bottom(6)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        open_button = Gtk.Button(label="Open bazaar")
        open_button.set_tooltip_text(
            "Replay the Bibi Basar interaction through the client's own send "
            "path. The NPC entity id and dialog option are learned when you "
            "open the bazaar by hand once."
        )
        open_button.connect("clicked", lambda *_: self._bazar_open())
        toolbar.append(open_button)

        monitor_start = Gtk.Button(label="Monitor Start")
        monitor_start.add_css_class("suggested-action")
        monitor_start.set_tooltip_text(
            "Search every configured item and show the cheapest listing and "
            "its gold-per-ND / NPC markup. Sends only search packets."
        )
        monitor_start.connect("clicked", lambda *_: self._bazar_start_monitor())
        toolbar.append(monitor_start)
        monitor_stop = Gtk.Button(label="Monitor Stop")
        monitor_stop.connect("clicked", lambda *_: self._bazar_stop())
        toolbar.append(monitor_stop)

        relist_start = Gtk.Button(label="Relist Start")
        relist_start.add_css_class("destructive-action")
        relist_start.set_tooltip_text(
            "Search each item, then re-register your inventory stack one gold "
            "below the cheapest other seller, never below min_price. Requires "
            "the live-relist checkbox and your character name."
        )
        relist_start.connect("clicked", lambda *_: self._bazar_start_relist())
        toolbar.append(relist_start)
        relist_stop = Gtk.Button(label="Relist Stop")
        relist_stop.connect("clicked", lambda *_: self._bazar_stop())
        toolbar.append(relist_stop)

        self.bazar_info = Gtk.Label(label="NosBazar idle")
        self.bazar_info.set_xalign(0)
        self.bazar_info.set_hexpand(True)
        toolbar.append(self.bazar_info)
        page.append(toolbar)

        body = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        body.set_vexpand(True)
        body.set_wide_handle(True)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        result_tools = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        self.bazar_results_info = Gtk.Label(
            label="Cheapest listing per item, most profitable first."
        )
        self.bazar_results_info.set_xalign(0)
        self.bazar_results_info.set_hexpand(True)
        self.bazar_results_info.add_css_class("dim-label")
        result_tools.append(self.bazar_results_info)
        self.bazar_only_priced = Gtk.CheckButton(label="Only priced")
        self.bazar_only_priced.set_active(True)
        self.bazar_only_priced.set_tooltip_text(
            "Hide items with no listing, a timeout or no answer yet, so only "
            "items with a current price remain (most profitable first)"
        )
        self.bazar_only_priced.connect(
            "toggled", lambda *_: self._bazar_render_results()
        )
        result_tools.append(self.bazar_only_priced)
        left.append(result_tools)
        self.bazar_results_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2
        )
        results_scroll = Gtk.ScrolledWindow()
        results_scroll.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
        )
        results_scroll.set_child(self.bazar_results_box)
        results_scroll.set_vexpand(True)
        left.append(results_scroll)
        body.set_start_child(left)
        body.set_resize_start_child(True)

        right_scroll = Gtk.ScrolledWindow()
        right_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        right_scroll.set_size_request(410, -1)
        right_scroll.set_vexpand(True)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        right.set_margin_top(2)
        right.set_margin_bottom(2)
        right.set_margin_start(2)
        right.set_margin_end(2)

        monitor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.bazar_live_relist = Gtk.CheckButton(
            label="Allow live re-listing (changes your listings)"
        )
        self.bazar_live_relist.set_active(False)
        self.bazar_live_relist.set_tooltip_text(
            "Safety gate: Relist Start does nothing unless this is ticked. "
            "Re-listing spends the listing fee and changes your prices."
        )
        monitor_box.append(self.bazar_live_relist)
        self.bazar_character_entry = Gtk.Entry()
        self.bazar_character_entry.set_placeholder_text("your character")
        self.bazar_character_entry.set_max_length(40)
        self.bazar_character_entry.set_width_chars(18)
        self.bazar_character_entry.set_tooltip_text(
            "Used to recognise your own listing so the bot never undercuts "
            "itself. Filled automatically when you open the bazaar."
        )
        self.bazar_npc_entry = Gtk.Entry()
        self.bazar_npc_entry.set_placeholder_text("Bibi entity id")
        self.bazar_npc_entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self.bazar_npc_entry.set_max_length(10)
        self.bazar_npc_entry.set_width_chars(10)
        self.bazar_npc_entry.set_tooltip_text(
            "Entity id of the Bibi Basar NPC ('in 2 793 <id>' or the client's "
            "own npc_req). Learned automatically when you open the bazaar; "
            "enter it manually only if you know it."
        )
        monitor_box.append(
            self._field_row(
                Gtk.Label(label="Character"),
                self.bazar_character_entry,
                Gtk.Label(label="Bibi id"),
                self.bazar_npc_entry,
            )
        )
        self.bazar_refresh_spin = Gtk.SpinButton.new_with_range(5, 3600, 5)
        self.bazar_refresh_spin.set_value(30)
        self.bazar_refresh_spin.set_digits(0)
        monitor_box.append(
            self._field_row(
                Gtk.Label(label="Refresh (s)"), self.bazar_refresh_spin
            )
        )
        monitor_hint = Gtk.Label(
            label=(
                "Open the bazaar once by hand (talk to Bibi Basar → NosBazar) "
                "so the client id and dialog option can be learned. Searches "
                "are paced at 2.5 s; all-timeout passes back off and retry "
                "after the refresh interval."
            )
        )
        monitor_hint.set_xalign(0)
        monitor_hint.set_wrap(True)
        monitor_hint.set_max_width_chars(40)
        monitor_hint.add_css_class("dim-label")
        monitor_box.append(monitor_hint)
        right.append(self._frame("Monitor", monitor_box))

        items_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.bazar_items_info = Gtk.Label(label="0 items")
        self.bazar_items_info.set_xalign(0)
        self.bazar_items_info.add_css_class("dim-label")
        items_box.append(self.bazar_items_info)
        self.bazar_items_list = Gtk.ListBox()
        self.bazar_items_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.bazar_items_list.set_activate_on_single_click(True)
        self.bazar_items_list.connect(
            "row-selected", self._bazar_item_selected
        )
        items_scroll = Gtk.ScrolledWindow()
        items_scroll.set_child(self.bazar_items_list)
        items_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        items_scroll.set_min_content_height(140)
        items_scroll.set_max_content_height(280)
        items_box.append(items_scroll)
        item_buttons = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        import_item = Gtk.Button(label="Import NosMall…")
        import_item.set_tooltip_text(
            "Browse the client's full NosMall catalog (name + item vnums) "
            "and add a group with its c_blist search packet"
        )
        import_item.connect("clicked", lambda *_: self._open_nosmall_picker())
        new_item = Gtk.Button(label="New")
        new_item.set_tooltip_text("Clear the editor for a new item")
        new_item.connect("clicked", lambda *_: self._bazar_new_item())
        delete_item = Gtk.Button(label="Delete")
        delete_item.set_tooltip_text("Delete the selected item")
        delete_item.connect("clicked", lambda *_: self._bazar_delete_item())
        item_buttons.append(import_item)
        item_buttons.append(new_item)
        item_buttons.append(delete_item)
        items_box.append(item_buttons)
        right.append(self._frame("Items", items_box))

        right.append(self._frame("Item editor", self._build_bazar_editor()))

        config_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6
        )
        self.bazar_config_label = Gtk.Label(
            label=str(self.bazar_config_path)
        )
        self.bazar_config_label.set_xalign(0)
        self.bazar_config_label.set_wrap(True)
        self.bazar_config_label.set_max_width_chars(40)
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
        config_row.append(save_button)
        config_row.append(reload_button)
        config_box.append(config_row)
        config_hint = Gtk.Label(
            label=(
                "Saved automatically per game client as bazar-<pid>.json. "
                "Copy it to bazar.json to use the same items in another "
                "client."
            )
        )
        config_hint.set_xalign(0)
        config_hint.set_wrap(True)
        config_hint.set_max_width_chars(40)
        config_hint.add_css_class("dim-label")
        config_box.append(config_hint)
        right.append(self._frame("Configuration", config_box))

        right_scroll.set_child(right)
        body.set_end_child(right_scroll)
        body.set_resize_end_child(False)
        page.append(body)

        self.bazar_status = Gtk.Label(label="NosBazar idle")
        self.bazar_status.set_xalign(0)
        self.bazar_status.set_wrap(True)
        self.bazar_status.add_css_class("dim-label")
        page.append(self.bazar_status)

        self.bazar_page = page
        self._bazar_render_items()
        self._bazar_render_results()
        self._bazar_update_info()
        return page

    def _build_bazar_editor(self) -> Gtk.Widget:
        """The add/edit form for one item; values live in ``bazar_form``."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        form = self.bazar_form

        def entry(key: str, placeholder: str, width: int = 14) -> Gtk.Entry:
            widget = Gtk.Entry()
            widget.set_placeholder_text(placeholder)
            widget.set_max_length(200)
            widget.set_width_chars(width)
            form[key] = widget
            return widget

        name_entry = entry("name", "display name", 18)
        copy_name = Gtk.Button(label="Copy")
        copy_name.set_tooltip_text(
            "Copy the item name to the clipboard (also available by "
            "right-clicking an item row or a result row)"
        )
        copy_name.connect(
            "clicked", lambda *_: self._copy_text(name_entry.get_text())
        )
        box.append(
            self._field_row(
                Gtk.Label(label="Name"), name_entry, copy_name
            )
        )
        box.append(
            self._field_row(
                Gtk.Label(label="Search vnums"),
                entry("vnums", "e.g. 1261 2174", 18),
            )
        )
        raw = entry("raw", "optional full c_blist …", 18)
        raw.set_tooltip_text(
            "Advanced: a captured c_blist packet wins over Search vnums. "
            "Leave it empty for the normal vnum search."
        )
        box.append(
            self._field_row(Gtk.Label(label="Raw packet"), raw)
        )
        vnum = entry("vnum", "item vnum", 8)
        vnum.set_input_purpose(Gtk.InputPurpose.DIGITS)
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
        amount.set_value(5)
        amount.set_digits(0)
        form["amount"] = amount
        minimum = Gtk.SpinButton.new_with_range(0, 2_000_000_000, 1000)
        minimum.set_value(0)
        minimum.set_digits(0)
        minimum.set_width_chars(12)
        form["min"] = minimum
        box.append(
            self._field_row(
                Gtk.Label(label="Amount"), amount,
                Gtk.Label(label="Min price"), minimum,
            )
        )
        cost_type = Gtk.DropDown.new_from_strings(
            ["No cost", "NosMall ND", "NPC gold"]
        )
        cost_type.set_selected(0)
        form["cost_type"] = cost_type
        cost = Gtk.SpinButton.new_with_range(0, 1_000_000_000, 0.5)
        cost.set_digits(2)
        cost.set_value(0)
        form["cost"] = cost
        box.append(
            self._field_row(
                Gtk.Label(label="Cost"), cost_type, cost
            )
        )

        advanced = Gtk.Expander(label="Advanced c_reg fields")
        advanced_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6
        )
        adv_widgets: dict[str, Gtk.SpinButton] = {}
        for key, value in (
            ("unk1", 9),
            ("unk2", 4),
            ("durability", 1),
            ("medal", 2),
        ):
            spin = Gtk.SpinButton.new_with_range(0, 1000, 1)
            spin.set_value(value)
            spin.set_digits(0)
            form[key] = spin
            adv_widgets[key] = spin
        advanced_box.append(
            self._field_row(
                Gtk.Label(label="unk1"), adv_widgets["unk1"],
                Gtk.Label(label="unk2"), adv_widgets["unk2"],
            )
        )
        advanced_box.append(
            self._field_row(
                Gtk.Label(label="durability"), adv_widgets["durability"],
                Gtk.Label(label="medal"), adv_widgets["medal"],
            )
        )
        advanced.set_child(advanced_box)
        box.append(advanced)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        save = Gtk.Button(label="Save item")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self._bazar_save_item())
        clear = Gtk.Button(label="Clear")
        clear.connect("clicked", lambda *_: self._bazar_new_item())
        buttons.append(save)
        buttons.append(clear)
        box.append(buttons)
        editor_hint = Gtk.Label(
            label=(
                "Relist needs Item vnum + Tab + a Min price floor. Items "
                "without those still work in Monitor."
            )
        )
        editor_hint.set_xalign(0)
        editor_hint.set_wrap(True)
        editor_hint.set_max_width_chars(40)
        editor_hint.add_css_class("dim-label")
        box.append(editor_hint)
        return box

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
        self.draw_path_toggle = Gtk.ToggleButton(label="Draw path")
        self.draw_path_toggle.set_active(False)
        self.draw_path_toggle.set_tooltip_text(
            "On: the first map click sets the route start (cyan ring), later "
            "clicks add waypoints. Start joins the route from wherever the "
            "character stands — at the nearest leg — and follows it; the "
            "start point is not walked to. Off: a map click walks the "
            "character there immediately."
        )
        self._bind_toggle_color(self.draw_path_toggle)
        path_box.append(self.draw_path_toggle)
        self.block_portal_toggle = Gtk.ToggleButton(label="Block portals")
        # Off by default: the character must be able to use portals normally
        # unless the user opts in for farming.
        self.block_portal_toggle.set_active(False)
        self.block_portal_toggle.set_tooltip_text(
            "Keep the character off map portals while farming: A* paths "
            "route around warp tiles, a map click on one is refused, and "
            "every MOVE is checked against the portal cells, so the bot "
            "cannot accidentally enter another map."
        )
        self._bind_toggle_color(self.block_portal_toggle)
        self.block_portal_toggle.connect(
            "toggled", lambda *_: self._on_block_portals_toggled()
        )
        path_box.append(self.block_portal_toggle)
        sidebar.append(self._frame("Path", path_box))

        # Follow a named player (a mule that trails the character you play).
        follow_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.follow_player_toggle = Gtk.ToggleButton(label="Follow player")
        self.follow_player_toggle.set_active(False)
        self.follow_player_toggle.set_tooltip_text(
            "Keep the character near the named player: it stands up and "
            "walks toward them. While this is on it does no normal combat, "
            "recovery or drawn path; it picks up loot near the player when "
            "the Pickup toggle is on, and attacks the player's own target "
            "if the checkbox below is on. Type the player's name, or their "
            "char id, and press Start."
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
            "While following, also attack the monster the followed player is "
            "attacking (their latest `su`) as soon as it is inside the "
            "skill's range with a clear line — also while still walking to "
            "the player; following resumes between casts. Nothing else is "
            "attacked; combat, pickup and recovery stay off."
        )
        self.follow_mirror_check = Gtk.CheckButton(
            label="Mirror movement & spells"
        )
        self.follow_mirror_check.set_active(False)
        self.follow_mirror_check.set_tooltip_text(
            "Replay the followed character's own walk and skill packets. "
            "Every attached client publishes its outgoing movement and "
            "spells to the shared mirror bus (~/.cache/ntpl/mirror), so a "
            "second client can shadow it exactly across separate GUI "
            "processes; the server's `mv`/`su` broadcasts are the fallback. "
            "Self-target spells are retargeted from the player's char id to "
            "the follower's own id. Only skills the follower has learned are "
            "cast. Use together with Follow player."
        )
        follow_box.append(
            self._field_row(self.follow_player_toggle, self.follow_player_entry)
        )
        follow_box.append(self.follow_attack_check)
        follow_box.append(self.follow_mirror_check)
        sidebar.append(self._frame("Follow", follow_box))

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
            "range, cast time, cooldown and MP from Skill.dat for the skill "
            "the bot is using; skill 0 follows the equipped SP card"
        )
        combat_box.append(self.combat_info)
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
            "Pick up your own drops within 10 cells"
        )
        self._bind_toggle_color(self.pickup_toggle)
        loot_box.append(self._field_row(self.pickup_toggle))
        sidebar.append(self._frame("Loot", loot_box))

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
            "Accept the in-place revival dialog (Continue here? You'll "
            "need 10 Seeds of Power) and resume. Off: the bot stops when "
            "the character dies and you revive manually."
        )
        self._bind_toggle_color(self.revive_toggle)
        recover_box.append(self.revive_toggle)
        sidebar.append(self._frame("Recovery", recover_box))

        # Malicious: client-side changes that apply immediately and do not
        # need the bot to run. Kept separate so they are never mistaken for
        # ordinary bot settings; all of them are high ban risk.
        malicious_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        malicious_hint = Gtk.Label(
            label=(
                "Client-side tricks. These apply as soon as they are set, "
                "whether or not the bot is running. High ban risk."
            )
        )
        malicious_hint.set_xalign(0)
        malicious_hint.set_wrap(True)
        malicious_hint.set_max_width_chars(34)
        malicious_hint.add_css_class("dim-label")
        malicious_box.append(malicious_hint)
        # Speed +: the DLL adds this flat value to the local player's incoming
        # `cond` speed, the way a NosTale speed buff works (+1, +2, +3...).
        self.speed_boost_spin = Gtk.SpinButton.new_with_range(0, 50, 1)
        self.speed_boost_spin.set_value(0)
        self.speed_boost_spin.set_hexpand(True)
        self.speed_boost_spin.set_tooltip_text(
            "Add this value to the client's walking speed, the same way a "
            "NosTale speed buff works (+1, +2, +3...). 0 = normal. The "
            "client caps walking speed at 59. The DLL adds the bonus to the "
            "server's `cond` speed before the client parses it, so movement "
            "stays smooth. It applies immediately and does not need the bot "
            "to run. This is a client-side speed hack: it violates the game "
            "rules and is high ban risk. The DLL reverts to normal if the "
            "GUI disconnects."
        )
        self.speed_boost_spin.connect(
            "value-changed", lambda *_: self._on_speed_boost_changed()
        )
        malicious_box.append(
            self._field_row(Gtk.Label(label="Speed +"), self.speed_boost_spin)
        )
        sidebar.append(self._frame("Malicious", malicious_box))

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
            b".bazar-ok { color: #33b34d; }"
            b".bazar-bad { color: #d93025; }"
            b".bazar-warn { color: #b8860b; }"
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
                world.entities[player_id] = Entity(
                    player_id, 1, name=name, x=px, y=py
                )
            else:
                entity.x, entity.y = px, py
                if not entity.name:
                    entity.name = name
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
        # floor pace the next action.
        cooldown = info.cast_time if info is not None else 0.0
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
        if self.follow_player_toggle is not None:
            self.follow_player_toggle.set_active(config.follow)
        if self.follow_player_entry is not None:
            self.follow_player_entry.set_text(config.follow_player)
        if self.follow_attack_check is not None:
            self.follow_attack_check.set_active(config.follow_attack)
        if self.follow_mirror_check is not None:
            self.follow_mirror_check.set_active(config.follow_mirror)
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
        self._sync_speed_boost()

    def _on_page_switch(self, _notebook, _page, _page_num) -> None:
        self._ensure_map_loaded()
        if self.map_area is not None:
            self.map_area.queue_draw()

    # -- connection -----------------------------------------------------

    def _mirror_writer_path(self) -> Path:
        """This host's publisher file, unique per client/port."""
        owner = (
            self.attached_client.winpid
            if self.attached_client is not None
            else os.getpid()
        )
        return (
            self.mirror_reader.directory
            / f"client-{owner}-{self.port or 0}.jsonl"
        )

    def _open_mirror_writer(self) -> None:
        self._close_mirror_writer()
        if self.port is None:
            return
        try:
            self.mirror_writer = MirrorWriter(path=self._mirror_writer_path())
        except OSError:
            self.mirror_writer = None

    def _close_mirror_writer(self, remove: bool = False) -> None:
        writer, self.mirror_writer = self.mirror_writer, None
        if writer is None:
            return
        path = writer.path
        writer.close()
        if remove:
            try:
                path.unlink()
            except OSError:
                pass

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
                    # The DLL accepts one host at a time and replaces the old
                    # one, so another CLI/monitor can kick the GUI off. Reconnect
                    # instead of leaving the window dead.
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
        box.append(heading)

        hint = Gtk.Label(
            label=(
                "Choose the window to attach to. Listing marks every game "
                "window with a number in its title bar (NosTale [#2]); pick "
                "the row with the same number. A client without the logger "
                "is injected automatically (Inject && connect). Clients "
                "attached by another GUI are disabled."
            )
        )
        hint.set_wrap(True)
        hint.set_xalign(0)
        hint.set_max_width_chars(56)
        hint.add_css_class("dim-label")
        box.append(hint)

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
        else:
            empty = Gtk.Label(
                label=(
                    "Start the game, then press Refresh. The window lists "
                    "every NostaleClientX.exe window in the running Wine "
                    "prefix."
                )
            )
            empty.set_wrap(True)
            empty.set_xalign(0)
            empty.add_css_class("dim-label")
            box.append(empty)

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
        self.client_label = f"{client.title} · PID {client.winpid}"
        if self.window is not None:
            self.window.set_title(
                f"NosTale Packet Logger — {self.client_label}"
            )
        self._set_status(
            f"attaching to {self.client_label} on 127.0.0.1:{port}…"
        )
        self._start_client(port)

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
                            if len(parts) >= 5:
                                try:
                                    rest = int(parts[4])
                                except ValueError:
                                    rest = -1
                                if rest >= 0:
                                    self.engine.world.resting = bool(rest)
                            self.pos_cell = (x, y)
                            self.pos_at = time.monotonic()
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
                        if blocked is not None:
                            self.target_blocked[blocked] = (
                                time.monotonic() + TARGET_BLOCK_S
                            )
                            if (
                                "entity not found" in reason
                                or "client refused" in reason
                            ):
                                # The client has no live object for it. The
                                # server sends no `out` for a killed monster,
                                # so without this the bot keeps selecting the
                                # corpse and every attack is cancelled.
                                self.engine.world.entities.pop(blocked, None)
                                dirty = True
                        self._set_status(message.payload)
                        # Hundreds of ids can look like monsters in the world
                        # state while the client cannot select them (killed
                        # while out of view, never `out`). Dropping them one
                        # by one used to look like frantic target switching;
                        # after a few in a row, pause combat briefly instead.
                        now = time.monotonic()
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
                                "target selection keeps failing (the client "
                                "has no such monster); pausing combat"
                            )
                    elif len(parts) >= 5 and parts[2] == "OK":
                        self._log_line(
                            f"monster {parts[1]} selected "
                            f"(entity {parts[3]}, controller {parts[4]})"
                        )
                    dirty = True
                elif message.payload.startswith(
                    "unknown command: TARGET"
                ):
                    if self.dll_target_supported:
                        self.dll_target_supported = False
                        self._set_status(
                            "DLL is too old for target selection; "
                            "falling back to ncif (re-inject the new DLL)"
                        )
                else:
                    self._set_status(message.payload)
            elif message.kind is Kind.QUIT:
                self._set_status("disconnected")
            else:
                packet = Packet.from_message(message)
                self.engine.feed(packet)
                self._track_packet(packet)
                self._bazar_on_packet(packet)
                self._learn_char_id(self.engine.world.self_id)
                # Switch the map as soon as the server announces it instead of
                # waiting for the next 2 s auto-map tick.
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
        now = time.monotonic()
        if packet.direction is not Kind.RECV:
            # The client's own outgoing action is what another host mirrors.
            self._mirror_publish(packet)
            # Client sends are deliberately not used for interruption
            # detection: the client's own `walk` for a MoveTo need not carry
            # the exact commanded cell (it walks its internal path), so
            # comparing it caused false "interrupted" replans. The server's
            # `tp` below is authoritative.
            return
        if packet.opcode in ("tp", "mv"):
            values = packet.ints(4) if packet.opcode == "tp" else packet.ints(5)
            if values and values[0] == 1:
                self.last_move = (values[0], values[1], values[2], values[3])
                self.last_move_time = time.monotonic()
                char_id = self._char_id()
                if (
                    packet.opcode == "tp"
                    and self.bot_running
                    and char_id is not None
                    and values[1] == char_id
                ):
                    expected = self.pending_target or self.command_target
                    if (
                        expected is not None
                        and (values[2], values[3]) != expected
                    ):
                        # The server put the character on a cell the bot did
                        # not ask for (a correction, or the player took over
                        # and walked). Re-plan from there.
                        self.external_move = True
                        self.external_move_position = (values[2], values[3])
                # The server answers an injected walk with
                # `tp 1 <charId> <x> <y>`; match it to the pending target to
                # learn our own char id even before the first `rest`.
                if (
                    packet.opcode == "tp"
                    and self.pending_target is not None
                    and (values[2], values[3]) == self.pending_target
                ):
                    self._learn_char_id(values[1])
                if (
                    packet.opcode == "mv"
                    and self.mirror_player_id is not None
                    and values[1] == self.mirror_player_id
                    and self._mirror_follow_active()
                    and now - self.mirror_bus_seen_at > MIRROR_BUS_GRACE_S
                ):
                    # Another client's host did not publish its walk: use the
                    # server's broadcast of the followed player's movement.
                    self.mirror_move = (values[2], values[3])
                    self.mirror_move_at = now
        elif packet.opcode == "at":
            values = packet.ints(1)
            if values:
                self._learn_char_id(values[0])
        elif packet.opcode == "su":
            # su <attackerType> <attackerId> <targetType> <targetId> ...
            # Track which monster each player is hitting, so "attack the
            # same target as the player" can follow it. Only players (type 1)
            # hitting monsters (type 3); our own hits are irrelevant here.
            values = packet.ints(4)
            if values is None:
                return
            if values[0] == 1 and values[2] == 3:
                self.player_targets[values[1]] = (values[3], now)
                if len(self.player_targets) > 32:
                    cutoff = now - FOLLOW_TARGET_TTL_S
                    self.player_targets = {
                        char_id: entry
                        for char_id, entry in self.player_targets.items()
                        if entry[1] >= cutoff
                    }
            # The fifth field is the skill vnum (mirror mode only; `u_s`
            # carries a slot, so the broadcast is the vnum source).
            skill = None
            if len(packet.args) >= 5:
                try:
                    skill = int(packet.args[4])
                except ValueError:
                    skill = None
            if (
                skill is not None
                and skill > 0
                and values[0] == 1
                and self.mirror_player_id is not None
                and values[1] == self.mirror_player_id
                and self._mirror_follow_active()
                and now - self.mirror_bus_seen_at > MIRROR_BUS_GRACE_S
            ):
                self._mirror_queue_cast(
                    skill, -1, values[2], values[3], now
                )

    def _mirror_publish(self, packet: Packet) -> None:
        """Publish this client's own walk/skill for a follower to mirror."""
        writer = self.mirror_writer
        if writer is None or not writer.available:
            return
        world = self.engine.world
        name = self._mirror_self_name()
        if packet.opcode == "walk":
            values = packet.ints(4)
            if values is not None and len(values) >= 2 and (
                values[0] >= 0 and values[1] >= 0
            ):
                writer.move(
                    values[0],
                    values[1],
                    values[3] if len(values) >= 4 else 0,
                    char_id=self._char_id() or 0,
                    name=name,
                    map_id=world.map_id,
                )
        elif packet.opcode == "u_s":
            values = packet.ints(3)
            if values is not None and len(values) >= 3:
                slot = values[0]
                writer.spell(
                    skill=world.skill_vnum(slot) or 0,
                    slot=slot,
                    target_type=values[1],
                    target_id=values[2],
                    char_id=self._char_id() or 0,
                    name=name,
                    map_id=world.map_id,
                )

    def _mirror_self_name(self) -> str:
        """Our own name for the bus, also for a client seen before attach."""
        world = self.engine.world
        if world.self_name:
            return world.self_name
        char_id = self._char_id()
        if char_id is not None:
            return self.log_names.get(char_id, "")
        return ""

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

    def _on_connected(self, generation: int | None = None) -> bool:
        if generation is not None and generation != self._client_generation:
            return False
        if not self.status_received:
            where = f"connected to {self.host}:{self.client.port}"
            if self.client_label:
                where = f"{self.client_label} · {where}"
            self._set_status(where)
        # The GUI can attach after login, so there may be no `at` packet.
        # Ask the DLL for the client's own local position instead of sending a
        # probe walk (which would make the server teleport the character).
        self._sync_portal_block()
        self._sync_speed_boost(quiet=True)
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

    def _send_revive(self) -> None:
        """Confirm the server's in-place revival dialog.

        The client's Continue button emits this burst right before the server
        answers with `cond` + `revive 1 <charId> 0` (and spends 10 Seeds of
        Power). Reproducing it is the only way to accept the dialog without
        clicking the game window.
        """
        for packet in (
            "mall 50",
            "c_close 1",
            "c_close 0",
            "bp_close",
            "f_stash_end",
            "mall 50",
            "c_close 1",
            "mall 50",
            "c_close 1",
        ):
            if not self._send(packet):
                return

    def _client_move(self, x: int, y: int) -> bool:
        """Walk through the game's own MoveTo function (not packet injection)."""
        if self.client is None or not self.client.connected:
            self._set_status("not connected")
            return False

        # Last line of defence: with Block portals on, no MOVE may end on a
        # warp tile or cross one on the way. Paths already avoid them; this
        # catches a path built before the toggle or a freshly received `gp`.
        blocked = self._portal_cells()
        if blocked:
            position = self._player_cell()
            route = (
                self._segment_cells(position, (x, y))
                if position is not None
                else [(x, y)]
            )
            # Leaving the cell under the character is always allowed; only
            # entering a portal is blocked.
            hit = next(
                (cell for cell in route[1:] if cell in blocked), None
            )
            if hit is not None:
                now = time.monotonic()
                if now >= self.portal_warn_at:
                    self.portal_warn_at = now + 2.0
                    self._set_status(
                        f"MOVE to {x},{y} crosses portal {hit}; refused "
                        "(Block portals is on)"
                    )
                return False

        try:
            self.client.send_raw(f"{MOVE_PREFIX}{x} {y}")
        except (ConnectionLost, ValueError) as exc:
            self._set_status(f"move failed: {exc}")
            self._stop_bot()
            return False
        self.command_target = (x, y)
        self.command_at = time.monotonic()
        return True

    def _client_target(self, entity_id: int) -> bool:
        """Ask the DLL to select the monster in the client.

        Returns False when the DLL does not support TARGET (or the link is
        down); the caller then falls back to injecting ncif.
        """
        if not self.dll_target_supported:
            return False
        if self.client is None or not self.client.connected:
            return False
        try:
            self.client.send_raw(f"{TARGET_PREFIX}{entity_id}")
        except (ConnectionLost, ValueError):
            return False
        return True

    def _finish_kill_first(self) -> bool:
        """True while a half-killed target is still in attack range.

        Recovery waits for it: a low HP/MP bar must not abandon a fight the
        bot already started. When the monster dies it leaves the entity list
        (the `st` death handler) and recovery starts on the next tick.

        Strict focus keeps a live target even when the server refuses every
        cast, so a paused fight must not block recovery: otherwise the bot
        neither attacks (the refusal pause) nor heals and stands there until
        the pause runs out. That looked like "recovery stopped working".
        """
        if self._combat_paused(time.monotonic()):
            return False
        if (
            time.monotonic() - self.last_attack_sent_at
            > FINISH_KILL_GRACE_S
        ):
            # Nothing has been sent at this target for a while: it is not a
            # fight in progress (a kill the server never reported with `st`,
            # or a fight that already stopped). Heal now instead of waiting
            # for a death that may never arrive.
            return False
        entity_id = self.fight_target_id
        if entity_id is None:
            return False
        monster = self.engine.world.entities.get(entity_id)
        if monster is None or monster.kind != 3:
            return False
        position = self._player_cell()
        if position is None:
            return False
        distance = max(
            abs(monster.x - position[0]), abs(monster.y - position[1])
        )
        info = self.spells.get(self._basic_skill_vnum() or 0)
        reach = (
            float(info.range)
            if info is not None and info.range > 0
            else float(ATTACK_SEARCH_CELLS)
        )
        if distance > reach:
            return False
        if (
            info is not None
            and info.mp_cost > 0
            and self.engine.world.stats.mp < info.mp_cost
        ):
            # Cannot cast the next hit anyway: heal now.
            return False
        return True

    def _target_blocked(self, entity_id: int) -> bool:
        until = self.target_blocked.get(entity_id)
        if until is None:
            return False
        if time.monotonic() >= until:
            self.target_blocked.pop(entity_id, None)
            return False
        return True

    def _ensure_target(self, entity_id: int, now: float) -> bool:
        """Select the monster in the client and attack in the same tick.

        The DLL handles TARGET synchronously and processes the following
        SEND|u_s only after it returns, so the client's selection is applied
        before the skill goes out even though the host does not wait for the
        status reply. Waiting for that reply (or for a later tick) added
        seconds of latency during packet bursts, because STATUS lines queue
        behind the live packet stream.

        A refusal is reported asynchronously (``TARGET <id> ERR ...``) and
        blocks the entity for a few seconds, so the bot moves on to another
        monster instead of attacking with a stale selection.
        """
        if self._target_blocked(entity_id):
            return False
        if not self.dll_target_supported:
            # Old DLL: keep the previous behaviour, an injected ncif.
            self._send(f"ncif 3 {entity_id}")
            return True
        if not self._client_target(entity_id):
            self._send(f"ncif 3 {entity_id}")
        return True

    def _start_bot(self) -> None:
        if self.bot_running:
            return
        following = bool(
            self.follow_player_toggle is not None
            and self.follow_player_toggle.get_active()
        )
        if following and (
            self.follow_player_entry is None
            or not self.follow_player_entry.get_text().strip()
        ):
            self._set_status("follow is on; type the player name first")
            return
        position = self._player_cell()
        if position is None:
            self.start_when_located = True
            self._request_position()
            self._set_status(
                "locating character; starting when the position arrives"
            )
            return
        if self.waypoints:
            # Plan the walk from where the character stands (nearest leg),
            # without touching the drawn preview in path_cells.
            exec_path = self._apply_execution_path(position)
            if not exec_path:
                self._set_status(
                    "no walkable path to the waypoints; cannot start"
                )
                self._set_path_info("no path")
                return
        else:
            exec_path = list(self.path_cells)
            if not exec_path:
                # A path is optional: without one the bot can stand and
                # fight / pick up / recover (stationary combat mode).
                if not (
                    following
                    or (self.auto_attack_toggle is not None
                     and self.auto_attack_toggle.get_active())
                    or (self.pickup_toggle is not None
                        and self.pickup_toggle.get_active())
                    or (self.recover_toggle is not None
                        and self.recover_toggle.get_active())
                    or (self.recover_mp_toggle is not None
                        and self.recover_mp_toggle.get_active())
                ):
                    self._set_status("add waypoints or enable combat/pickup")
                    return
        # The client's MoveTo already does the pathfinding and animation. Give
        # it a multi-cell leg per call instead of one cell: a per-cell MOVE
        # starts and stops the walk every few hundred ms, which looks robotic.
        # Three cells per MOVE is short enough that the client animates it and
        # long enough that the movement is continuous.
        if exec_path:
            self.bot_goals = self._goals_from_path(exec_path)
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
        self.recovery_pending = False
        self.recover_need_hp = False
        self.recover_need_mp = False
        self.recover_rest_at = 0.0
        # The monster the bot is currently hitting; recovery waits until it
        # dies so a low bar never abandons a half-killed target.
        self.fight_target_id = None
        # Attack pacing / cancel retry.
        self.last_attack_sent_at = 0.0
        self.last_cancel_count = self.engine.world.cancel_count
        self.pending_casts.clear()
        self.hit_marks_seen = self.engine.world.self_hit_count
        self.hit_target_at.clear()
        self.cancel_warn_at = 0.0
        self.cancel_streak = 0
        self.cancel_streak_target = None
        self.cancel_events.clear()
        self.target_fail_events.clear()
        self.follow_warn_at = 0.0
        self.combat_paused_until = 0.0
        self.combat_pause_pos = None
        self.dead_warned = False
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
        self.target_blocked.clear()
        # Stop a click-to-walk too; Stop is the one "stop moving" button.
        self._stop_travel(quiet=True)
        if self.bot_timer is not None:
            GLib.source_remove(self.bot_timer)
            self.bot_timer = None
        self.recovering = False
        self.recovery_pending = False
        self.recover_need_hp = False
        self.recover_need_mp = False
        self.fight_target_id = None
        self.pending_casts.clear()
        self._mirror_reset()
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

    def _portal_cells(self) -> frozenset[tuple[int, int]]:
        """Warp cells on the current map while **Block portals** is active.

        The union of the server's own `gp` list, the engine's crossing
        fallback and the persisted portal map. `gp` is exact; a learned
        crossing can be a cell off, and including both is deliberate:
        blocking one extra cell is harmless, stepping on the real portal is
        not.
        """
        toggle = self.block_portal_toggle
        if toggle is None or not toggle.get_active() or self.map_grid is None:
            return frozenset()
        map_id = self.map_grid.map_id
        cells = {
            (x, y)
            for (gate_map, x, y) in self.engine.world.gates
            if gate_map == map_id
        }
        cells.update(
            (x, y)
            for (portal_map, x, y) in self.engine.world.portals
            if portal_map == map_id
        )
        cells.update(
            (x, y)
            for (portal_map, x, y) in self.portals
            if portal_map == map_id
        )
        return frozenset(cells)

    @staticmethod
    def _segment_cells(
        start: tuple[int, int], end: tuple[int, int]
    ) -> list[tuple[int, int]]:
        """Cells on the straight 8-direction walk from `start` to `end`."""
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        steps = max(abs(dx), abs(dy))
        if steps == 0:
            return [start]
        return [
            (
                round(start[0] + dx * index / steps),
                round(start[1] + dy * index / steps),
            )
            for index in range(steps + 1)
        ]

    def _walkable_cell(self, x: int, y: int) -> tuple[int, int] | None:
        """The cell itself when usable, else a usable neighbour.

        Portal cells are skipped while blocking is on, so a drop lying on a
        warp tile is picked up from the cell next to it instead.
        """
        grid = self.map_grid
        if grid is None:
            return None
        blocked = self._portal_cells()

        def usable(nx: int, ny: int) -> bool:
            return (
                0 <= nx < grid.width
                and 0 <= ny < grid.height
                and grid.is_walkable(nx, ny)
                and (nx, ny) not in blocked
            )

        if usable(x, y):
            return (x, y)
        for dx, dy in (
            (1, 0), (-1, 0), (0, 1), (0, -1),
            (1, 1), (-1, -1), (1, -1), (-1, 1),
        ):
            nx, ny = x + dx, y + dy
            if usable(nx, ny):
                return (nx, ny)
        return None

    def _approach_cell(
        self, monster: Entity
    ) -> tuple[int, int] | None:
        """A walkable cell to path toward for a monster outside range."""
        return self._walkable_cell(monster.x, monster.y)

    def _mirror_follow_active(self) -> bool:
        """Mirror mode is only meaningful while Follow player is on."""
        return bool(
            self.follow_player_toggle is not None
            and self.follow_player_toggle.get_active()
            and self.follow_mirror_check is not None
            and self.follow_mirror_check.get_active()
        )

    def _mirror_reset(self) -> None:
        self.mirror_player_id = None
        self.mirror_move = None
        self.mirror_move_at = 0.0
        self.mirror_casts.clear()
        self.mirror_recent_casts.clear()
        self.mirror_bus_seen_at = 0.0
        self.mirror_slot_vnums.clear()
        self.mirror_slot_owner = None
        self.mirror_slot_warn_at = 0.0

    def _mirror_poll(self, follow_name: str, now: float) -> None:
        """Refresh the followed id and read recent actions from the bus.

        Runs in the bot tick even while a walk is pending, so the latest
        followed-player cell/spell is ready as soon as the leg completes.
        """
        if now - self.mirror_poll_at < 0.05:
            return
        self.mirror_poll_at = now
        player = self._find_player(follow_name)
        follower_id = self._char_id()
        if player is not None and player.entity_id == follower_id:
            # Following ourselves would replay our own casts forever.
            player = None
        self.mirror_player_id = (
            player.entity_id
            if player is not None
            else (
                int(follow_name)
                if follow_name.isdigit()
                and int(follow_name) != follower_id
                else None
            )
        )
        if (
            player is not None
            and self.mirror_slot_owner != player.entity_id
        ):
            # A new followed character has its own skill slots.
            self.mirror_slot_owner = player.entity_id
            self.mirror_slot_vnums.clear()
        try:
            events = self.mirror_reader.poll()
        except OSError:
            events = []
        world = self.engine.world
        for event in events:
            if follower_id is not None and event.char_id == follower_id:
                # Our own publisher file; never mirror ourselves.
                continue
            if not event.matches(follow_name, self.mirror_player_id):
                continue
            if (
                event.map_id is not None
                and world.map_id is not None
                and event.map_id != world.map_id
            ):
                continue
            self.mirror_bus_seen_at = now
            if event.kind == KIND_MOVE:
                self.mirror_move = (event.x, event.y)
                self.mirror_move_at = now
            elif event.kind == KIND_SPELL:
                if event.skill > 0 and event.slot >= 0:
                    # The followed client resolved this slot; with the same
                    # SP card the follower can use the same slot even when
                    # its own `ski` list was never captured.
                    self.mirror_slot_vnums[event.slot] = event.skill
                self._mirror_queue_cast(
                    event.skill,
                    event.slot,
                    event.target_type,
                    event.target_id,
                    now,
                )

    def _mirror_queue_cast(
        self,
        skill: int,
        slot: int,
        target_type: int,
        target_id: int,
        now: float,
    ) -> None:
        """Queue one spell to replay, dropping the bus/broadcast duplicate."""
        if skill <= 0 and slot < 0:
            return
        key = (
            skill if skill > 0 else -slot,
            target_type,
            target_id if target_type == 3 else 0,
        )
        last = self.mirror_recent_casts.get(key)
        if last is not None and now - last < MIRROR_DEDUPE_S:
            return
        self.mirror_recent_casts[key] = now
        if len(self.mirror_recent_casts) > 64:
            cutoff = now - MIRROR_CAST_TTL_S
            self.mirror_recent_casts = {
                cast_key: when
                for cast_key, when in self.mirror_recent_casts.items()
                if when >= cutoff
            }
        self.mirror_casts.append((skill, slot, target_type, target_id, now))
        if len(self.mirror_casts) > MIRROR_CAST_QUEUE_MAX:
            del self.mirror_casts[:-MIRROR_CAST_QUEUE_MAX]

    def _mirror_warn(self, now: float, text: str) -> None:
        if now >= self.mirror_warn_at:
            self.mirror_warn_at = now + 10.0
            self._set_status(text)

    def _mirror_cast_step(self, now: float) -> bool:
        """Cast the oldest fresh mirrored spell, if it is possible now.

        A cast that fails for a temporary reason (cooldown, MP, range, a
        lost line) stays at the head of the queue and is retried on a later
        tick; movement still runs meanwhile. A cast that can never work
        here (unknown vnum/slot, a third-party target, a despawned monster)
        is dropped so it cannot block the ones behind it.
        """
        while self.mirror_casts:
            skill, slot, target_type, target_id, at = self.mirror_casts[0]
            if now - at > MIRROR_CAST_TTL_S:
                # A reaction to an old cast is worse than no reaction.
                self.mirror_casts.pop(0)
                continue
            result = self._mirror_cast(
                skill, slot, target_type, target_id, now
            )
            if result is True:
                self.mirror_casts.pop(0)
                return True
            if result is None:
                self.mirror_casts.pop(0)
                continue
            return False
        return False

    def _mirror_cast(
        self,
        skill: int,
        slot: int,
        target_type: int,
        target_id: int,
        now: float,
    ) -> bool | None:
        """Replay the followed player's spell with our own slot and ids.

        Returns True when sent, False when it may work on a later tick, and
        None when the cast can never be mirrored here.
        """
        world = self.engine.world
        vnum = skill
        if vnum <= 0 and slot >= 0:
            vnum = (
                world.skill_vnum(slot)
                or self.mirror_slot_vnums.get(slot)
                or 0
            )
        own_slot = self._skill_slot(vnum) if vnum > 0 else None
        if (
            own_slot is None
            and 0 <= slot < 32
            and not self._known_skills()
        ):
            # The follower attached after login and never saw `ski`. With the
            # same SP card the followed client's `u_s` slot is valid here too.
            own_slot = slot
            if now >= self.mirror_slot_warn_at:
                self.mirror_slot_warn_at = now + 30.0
                self._log_line(
                    "mirror: own skill list unknown; using the followed "
                    f"client's u_s slot {slot}"
                    + (f" for spell {vnum}" if vnum > 0 else "")
                )
        if own_slot is None:
            if vnum <= 0:
                self._mirror_warn(
                    now, "mirror: a followed spell has no known vnum/slot"
                )
            else:
                self._mirror_warn(
                    now,
                    f"mirror: spell {vnum} has no u_s slot here (not "
                    "learned, or use it once with the logger attached)",
                )
            return None
        info = self.spells.get(vnum) if vnum > 0 else None
        stats = world.stats
        if (
            info is not None
            and info.mp_cost > 0
            and stats.mp < info.mp_cost
        ):
            return False
        if now < self.attack_cooldown_until:
            return False
        char_id = self._char_id()
        monster: Entity | None = None
        if target_type == 1:
            # A self/ally cast in the followed client. The packet carries
            # their char id, so replaying it verbatim would target the other
            # character; retarget to us. A third-party ally is skipped.
            if char_id is None:
                return False
            if target_id not in (0, self.mirror_player_id, char_id):
                return None
            target_type, target_id = 1, char_id
        else:
            monster = world.entities.get(target_id)
            if monster is None or monster.kind != 3:
                return None
            if self._target_blocked(target_id):
                return False
            if not self._has_line_of_sight(monster):
                return False
            if not self._spell_in_range(info, monster):
                return False
            if not self._ensure_target(target_id, now):
                return False
            target_type = 3
        packet = self._skill_packet(
            target_id, skill=own_slot, target_type=target_type
        )
        if not self._send(packet):
            return False
        self._note_attack(
            target_id if monster is not None else None, now
        )
        cast_time = info.cast_time if info is not None else 0.0
        # Mirror the leader's cadence, not the spell's full cooldown: the
        # server enforces each skill's cooldown, and the leader already
        # respected it. A full cooldown here would block the *next* mirrored
        # spell (a different skill has its own cooldown).
        self.attack_cooldown_until = now + MIRROR_CAST_MIN_INTERVAL_S
        self.walk_not_before = now + max(self.attack_walk_delay, cast_time)
        target_text = "self" if monster is None else str(target_id)
        spell_text = str(vnum) if vnum > 0 else f"slot {own_slot}"
        self._set_status(
            f"mirror spell {spell_text} → {target_text}: {packet}"
        )
        return True

    def _mirror_move_step(self, player: Entity, now: float) -> bool:
        """Replay the followed player's latest walk destination.

        The destination is the one their client commanded, so the follower
        trails the same route instead of re-planning from the map each tick.
        A far cell is chopped into the usual <=3-cell client MoveTo legs.
        """
        target = self.mirror_move
        if target is None:
            return False
        if now - self.mirror_move_at > MIRROR_MOVE_TTL_S:
            self.mirror_move = None
            return False
        position = self._player_cell()
        if position is None:
            return False
        if target == position:
            self.mirror_move = None
            return False
        if now < self.walk_not_before:
            # A mirrored cast just played; walking resumes right after it,
            # and the follow tick must not fall back to the normal trail.
            return True
        if self.map_grid is not None:
            if not self.map_grid.is_walkable(*target):
                nearest = self._walkable_cell(*target)
                if nearest is None:
                    self.mirror_move = None
                    return False
                target = nearest
            distance = max(
                abs(target[0] - position[0]),
                abs(target[1] - position[1]),
            )
            if distance > MAX_MOVE_LEG_CELLS:
                path = astar(
                    self.map_grid, position, target, self._portal_cells()
                )
                if path is None or len(path) < 2:
                    self.mirror_move = None
                    return False
                target = path[min(MAX_MOVE_LEG_CELLS, len(path) - 1)]
        if target == position:
            return False
        if not self._client_move(target[0], target[1]):
            return False
        self.pending_target = target
        self.pending_is_path = False
        self.pending_loot_id = None
        self.pending_since = now
        self.pending_distance = max(
            abs(target[0] - position[0]),
            abs(target[1] - position[1]),
        )
        self.pending_until = now + min(
            60.0,
            max(
                self.walk_timeout,
                self.pending_distance * self.walk_timeout_per_cell,
            ),
        )
        self._set_status(
            f"mirror move to {target} "
            f"(following {player.name or 'player'})"
        )
        return True

    def _mirror_apply(self, player: Entity, now: float) -> bool:
        """Spells first, then the latest movement, in mirror mode."""
        own_id = self._char_id()
        if own_id is not None and player.entity_id == own_id:
            return False
        self.mirror_player_id = player.entity_id
        if not self._mirror_follow_active():
            return False
        if self._mirror_cast_step(now):
            return True
        return self._mirror_move_step(player, now)

    def _follow_attack_step(self, player: Entity, now: float) -> bool:
        """Attack the monster the followed player last hit, if it is in reach.

        Only when the Follow frame's "Attack the same target as the player"
        checkbox is on. This is not general combat: the bot never picks its
        own target and never chases the monster. It fires whenever the
        monster is inside the skill's range with a clear line — also while
        still walking toward the player — and resumes following between
        casts.
        """
        if (
            self.follow_attack_check is None
            or not self.follow_attack_check.get_active()
        ):
            return False
        entry = self.player_targets.get(player.entity_id)
        if entry is None:
            return False
        target_id, when = entry
        if now - when > FOLLOW_TARGET_TTL_S:
            return False
        monster = self.engine.world.entities.get(target_id)
        if monster is None or monster.kind != 3:
            return False
        if self._target_blocked(monster.entity_id):
            return False
        position = self._player_cell()
        if position is None:
            return False
        info = self.spells.get(self._basic_skill_vnum() or 0)
        attack_range = (
            float(info.range)
            if info is not None and info.range > 0
            else float(ATTACK_SEARCH_CELLS)
        )
        distance = max(
            abs(monster.x - position[0]), abs(monster.y - position[1])
        )
        if distance > attack_range or not self._has_line_of_sight(monster):
            return False
        if now < self.attack_cooldown_until:
            return False
        stats = self.engine.world.stats
        if info is not None and info.mp_cost > 0 and stats.mp < info.mp_cost:
            return False
        if not self._ensure_target(monster.entity_id, now):
            return False
        packet = self._skill_packet(monster.entity_id, skill=0)
        if not self._send(packet):
            return False
        self._note_attack(monster.entity_id, now)
        self.fight_target_id = monster.entity_id
        cooldown = (
            max(info.cooldown, info.cast_time) if info is not None else 1.0
        )
        self.attack_cooldown_until = now + cooldown
        cast_pause = info.cast_time if info is not None else 0.0
        self.walk_not_before = now + max(self.attack_walk_delay, cast_pause)
        self._set_status(f"follow-attack {monster.entity_id}: {packet}")
        return True

    def _follow_loot_step(self, player: Entity, now: float) -> bool:
        """Pick up own/free drops near the followed player.

        Follow mode used to ignore loot completely. With the Pickup toggle on
        (the same toggle the normal bot uses) the follower collects drops
        lying within FOLLOW_LOOT_RADIUS of the player, then resumes following.
        A drop at the feet is taken with one `get`; a farther one is walked to
        with the usual client-MoveTo legs.
        """
        if (
            self.pickup_toggle is None
            or not self.pickup_toggle.get_active()
        ):
            return False
        char_id = self._char_id()
        if char_id is None or now < self.pickup_cooldown_until:
            return False
        drop = self._nearest_drop(LOOT_APPROACH_CELLS)
        if drop is None:
            return False
        if (
            max(abs(drop.x - player.x), abs(drop.y - player.y))
            > FOLLOW_LOOT_RADIUS
        ):
            return False
        position = self._player_cell()
        if position is None:
            return False
        distance = max(
            abs(drop.x - position[0]), abs(drop.y - position[1])
        )
        if distance > PICKUP_RANGE_CELLS:
            if not self._walk_to_loot(drop, now):
                self.pickup_ignored[drop.drop_id] = now + 5.0
            return True
        if not self._send(f"get 1 {char_id} {drop.drop_id}"):
            return False
        attempts = self.pickup_attempts.get(drop.drop_id, 0) + 1
        self.pickup_attempts[drop.drop_id] = attempts
        if attempts >= 3:
            self.pickup_ignored[drop.drop_id] = now + 15.0
        self.pickup_cooldown_until = now + PICKUP_COOLDOWN_S
        self._set_status(
            f"pickup {drop.item_vnum} x{drop.amount} ({drop.drop_id})"
        )
        return True

    def _follow_missing_text(self, name: str) -> str:
        """Explain why a follow target was not found, including char ids.

        The client only sends an entity's name in its ``in 1 <name> - <id>``
        spawn. A player already in view when the DLL is injected never sends
        that packet again, so the host knows the position (from ``mv 1 <id>``)
        but not the name. Such a player is still followable by char id.
        """
        world = self.engine.world
        own_id = self._char_id()
        named: list[str] = []
        unnamed: list[int] = []
        for entity in world.entities.values():
            if entity.kind != 1 or entity.entity_id == own_id:
                continue
            label = entity.name or self.log_names.get(entity.entity_id, "")
            if label:
                named.append(label)
            else:
                unnamed.append(entity.entity_id)
        wanted = name.strip()
        head = (
            f"following: char {wanted} is not in sight"
            if wanted.isdigit()
            else f"following: {wanted} is not in sight"
        )
        parts = [head]
        map_id = world.map_id
        if map_id is None and self.map_grid is not None:
            map_id = self.map_grid.map_id
        if map_id is not None:
            parts.append(f"map {map_id}")
        if named:
            parts.append(
                "visible players: " + ", ".join(sorted(set(named))[:8])
            )
        if unnamed and not wanted.isdigit():
            parts.append(
                "unnamed players (enter the char id): "
                + ", ".join(str(value) for value in sorted(set(unnamed))[:8])
            )
        if not named and not unnamed:
            parts.append("no other player visible — same map and closer?")
        return " · ".join(parts)

    def _follow_step(self, name: str, now: float) -> bool:
        """Walk toward the specified player; fight only the player's target.

        Always returns True: follow mode owns the tick, so the general
        combat, recovery and the drawn path stay idle until the toggle is
        turned off. Loot is still collected when the Pickup toggle is on
        (drops near the player), and with the "Attack the same target"
        checkbox on the bot fires at the monster the player last hit as soon
        as that monster is in range — it keeps walking toward the player
        between casts instead of waiting to arrive first.
        """
        player = self._find_player(name)
        if player is None:
            if now >= self.follow_warn_at:
                self.follow_warn_at = now + 5.0
                self._set_status(self._follow_missing_text(name))
            return True
        position = self._player_cell()
        if position is None:
            return True
        # Mirror mode replays the followed character's own walk/skill
        # packets; it owns movement and casts before loot and follow-attack.
        # Without fresh mirrored actions the normal trail below still runs.
        if self._mirror_apply(player, now):
            return True
        # Loot near the player comes first; following resumes right after.
        if self._follow_loot_step(player, now):
            return True
        # Attack the player's target whenever it is in range, even while
        # still approaching the player. Walking resumes between casts.
        if self._follow_attack_step(player, now):
            return True
        distance = max(abs(player.x - position[0]), abs(player.y - position[1]))
        if distance <= FOLLOW_STOP_CELLS:
            self._set_status(
                f"following {player.name or name} "
                f"(char {player.entity_id}, {distance} cells)"
            )
            return True
        if now < self.walk_not_before:
            return True
        if self.map_grid is None:
            self._set_status(f"following {name}: map data not loaded")
            return True
        goal = self._walkable_cell(player.x, player.y)
        if goal is None:
            self._set_status(
                f"following {name}: no walkable cell near "
                f"{player.x},{player.y}"
            )
            return True
        path = astar(self.map_grid, position, goal, self._portal_cells())
        if path is None or len(path) < 2:
            self._set_status(f"following {name}: no walkable path")
            return True
        leg = path[min(3, len(path) - 1)]
        if leg == position:
            return True
        if not self._client_move(leg[0], leg[1]):
            return True
        self.pending_target = leg
        self.pending_is_path = False
        self.pending_loot_id = None
        self.pending_since = now
        self.pending_distance = max(
            abs(leg[0] - position[0]), abs(leg[1] - position[1])
        )
        self.pending_until = now + min(
            60.0,
            max(
                self.walk_timeout,
                self.pending_distance * self.walk_timeout_per_cell,
            ),
        )
        self._set_status(
            f"following {player.name or name} to {player.x},{player.y} "
            f"({distance} cells)"
        )
        return True

    def _walk_to_loot(self, drop: GroundItem, now: float) -> bool:
        """Walk one leg toward a drop, then pick it up and resume.

        `get` is only accepted next to the item; the old code sent it from
        up to 10 cells away and the server silently rejected it.
        """
        if self.pending_target is not None or self.map_grid is None:
            return False
        position = self._player_cell()
        if position is None:
            return False
        if now < self.walk_not_before:
            return True
        goal = self._walkable_cell(drop.x, drop.y)
        if goal is None:
            return False
        path = astar(
            self.map_grid, position, goal, self._portal_cells()
        )
        if path is None or len(path) < 2:
            return False
        leg = path[min(3, len(path) - 1)]
        if leg == position:
            return False
        if not self._client_move(leg[0], leg[1]):
            return False
        self.pending_target = leg
        self.pending_is_path = False
        self.pending_loot_id = drop.drop_id
        self.pending_since = now
        self.pending_distance = max(
            abs(leg[0] - position[0]), abs(leg[1] - position[1])
        )
        self.pending_until = now + min(
            60.0,
            max(
                self.walk_timeout,
                self.pending_distance * self.walk_timeout_per_cell,
            ),
        )
        self._set_status(
            f"walking to drop {drop.item_vnum} at {drop.x},{drop.y}"
        )
        return True

    def _chase_step(
        self, monster: Entity, attack_range: float, now: float
    ) -> bool:
        """Walk one leg toward an out-of-range target.

        The game's own behaviour when a monster is clicked: select it, walk
        into range, then attack. The search radius still bounds which
        monsters are considered, so the bot does not run across the map.
        """
        if self.pending_target is not None or self.map_grid is None:
            return False
        position = self._player_cell()
        if position is None:
            return False
        if now < self.walk_not_before:
            return True
        goal = self._approach_cell(monster)
        if goal is None:
            return False
        path = astar(
            self.map_grid, position, goal, self._portal_cells()
        )
        if path is None or len(path) < 2:
            return False
        leg = path[min(3, len(path) - 1)]
        if leg == position:
            return False
        if not self._ensure_target(monster.entity_id, now):
            return False
        if not self._client_move(leg[0], leg[1]):
            return False
        self.pending_target = leg
        self.pending_is_path = False
        self.pending_loot_id = None
        self.pending_since = now
        self.pending_distance = max(
            abs(leg[0] - position[0]), abs(leg[1] - position[1])
        )
        self.pending_until = now + min(
            60.0,
            max(
                self.walk_timeout,
                self.pending_distance * self.walk_timeout_per_cell,
            ),
        )
        self._set_status(
            f"chasing monster {monster.entity_id} at "
            f"{monster.x},{monster.y}"
        )
        return True

    def _locked_target(self, radius: int) -> Entity | None:
        """The monster the bot is already fighting, while it is still valid.

        Focus fire: nothing steals a live target — not a closer monster, not
        another attacker, and not a momentary blocked line (the bot waits or
        walks around instead of switching). The lock breaks only when the
        target dies (the `st` death handler removes it), the client refuses
        to select it (`TARGET ... ERR`), or it leaves the search radius.
        """
        if self.fight_target_id is None:
            return None
        entity = self.engine.world.entities.get(self.fight_target_id)
        if entity is None or entity.kind != 3:
            return None
        if self._target_blocked(entity.entity_id):
            return None
        position = self._player_cell()
        if position is None:
            return None
        distance = max(
            abs(entity.x - position[0]), abs(entity.y - position[1])
        )
        if distance > radius:
            return None
        return entity

    def _engaged_target(self) -> Entity | None:
        """The live monster the bot is fighting, while it is within reach.

        Used to hold position: while this exists the bot takes no path leg
        and no loot walk until the monster dies. The radius is the normal
        attack search radius, so a monster that runs far away releases the
        bot; a monster merely stepping out of skill range does not.
        """
        if self.fight_target_id is None:
            return None
        entity = self.engine.world.entities.get(self.fight_target_id)
        if entity is None or entity.kind != 3:
            return None
        if self._target_blocked(entity.entity_id):
            return None
        position = self._player_cell()
        if position is None:
            return None
        radius = ATTACK_SEARCH_CELLS
        info = self.spells.get(self._basic_skill_vnum() or 0)
        if info is not None and info.range > 0:
            radius = max(radius, int(info.range))
        distance = max(
            abs(entity.x - position[0]), abs(entity.y - position[1])
        )
        return entity if distance <= radius else None

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
            if self._target_blocked(entity.entity_id):
                continue
            distance = max(abs(entity.x - px), abs(entity.y - py))
            if (
                distance <= radius
                and distance < best_distance
                and self._has_line_of_sight(entity)
            ):
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
            # Unowned drops are free to take. The 2026-11-18 server marks
            # them -1 (`drop 1303 513032 39 26 1 1 -1`; a `get` by char 7125
            # was accepted), older captures used 0. Other players' protected
            # drops stay out of reach.
            if drop.owner_id not in (-1, 0, char_id):
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
        skill = "0" if skill is None else str(skill)
        template = DEFAULT_SKILL_TEMPLATE
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
        follow_enabled = bool(
            self.follow_player_toggle is not None
            and self.follow_player_toggle.get_active()
        )
        follow_name = (
            self.follow_player_entry.get_text().strip()
            if follow_enabled and self.follow_player_entry is not None
            else ""
        )

        if world.dead:
            # Dead characters cannot attack or move; the server answers every
            # `u_s` with `cancel` and every walk is ignored.
            revive = bool(
                self.revive_toggle is not None
                and self.revive_toggle.get_active()
            )
            if not revive:
                if not self.dead_warned:
                    self.dead_warned = True
                    self.pending_target = None
                    self.pending_is_path = False
                    self.pending_loot_id = None
                    self.fight_target_id = None
                    self.recovering = False
                    self._stop_bot()
                    self._set_status(
                        "dead; revive is off — bot stopped "
                        "(revive manually, then Start)"
                    )
                return False
            if not self.dead_warned:
                self.dead_warned = True
                self.pending_target = None
                self.pending_is_path = False
                self.pending_loot_id = None
                self.fight_target_id = None
                self.recovering = False
                self.revive_sent_at = 0.0
                self._set_status(
                    "dead; waiting for the revival offer"
                )
            if world.revival_offered and self.revive_sent_at == 0.0:
                # The server offered `dlgi #revival` ("Continue here? You'll
                # need 10 Seeds of Power"). Answer it like the client does.
                self.revive_sent_at = now
                self._send_revive()
                self._set_status(
                    "revival offered; accepting (10 Seeds of Power)"
                )
            elif (
                self.revive_sent_at > 0.0
                and now - self.revive_sent_at > 5.0
            ):
                self._stop_bot()
                self._set_status(
                    "revive failed (10 Seeds of Power?); bot stopped"
                )
                return False
            return True
        self.dead_warned = False
        self.revive_sent_at = 0.0

        # Mirror mode keeps reading the followed character's publisher file
        # even while a walk is pending, so the next action is never stale.
        if follow_enabled and follow_name and self._mirror_follow_active():
            self._mirror_poll(follow_name, now)

        # A manual walk or a server correction moved the character while the
        # bot was driving: adopt the new cell and re-plan right away instead
        # of waiting for the old leg to time out (three timeouts used to stop
        # the bot, which is why Stop/Start felt necessary).
        if self.external_move:
            self.external_move = False
            moved_to = self.external_move_position
            self.external_move_position = None
            self.pending_target = None
            self.pending_is_path = False
            self.pending_loot_id = None
            self.rejected_walks = 0
            if now >= self.replan_at:
                self.replan_at = now + 0.5
                if self.waypoints:
                    self._replan_execution(moved_to)
                    self._set_status("walk interrupted; re-joining the route")

        # Match the server's hit/cancel replies to the cast they belong to.
        # A splash cast can report several hits; they all belong to the oldest
        # cast the server is answering. A lost cancel must not wedge the queue.
        while (
            self.pending_casts
            and now - self.pending_casts[0][0] > CAST_PENDING_TTL_S
        ):
            self.pending_casts.pop(0)
        if world.self_hit_count > self.hit_marks_seen:
            self.hit_marks_seen = world.self_hit_count
            if self.pending_casts:
                self.pending_casts[0][1] = True

        # A cancelled cast does not shorten the skill cooldown: the server
        # refuses an attack sent before the skill's own cooldown is over, so
        # an immediate retry would just be refused again.
        if world.cancel_count != self.last_cancel_count:
            self.last_cancel_count = world.cancel_count
            if now - self.last_attack_sent_at < 2.0:
                cast = (
                    self.pending_casts.pop(0)
                    if self.pending_casts
                    else None
                )
                if cast is None:
                    # Attached mid-cast: an unknown cancel must not drop a
                    # live target.
                    self.cancel_streak = 0
                    self.cancel_streak_target = None
                elif cast[1]:
                    # `su` landed, then the server ended the animation with
                    # `cancel`: normal end of the cast, not a refusal.
                    self.cancel_streak = 0
                    self.cancel_streak_target = None
                    if cast[2] is not None:
                        self.hit_target_at[cast[2]] = now
                        if len(self.hit_target_at) > 64:
                            cutoff = now - 60.0
                            self.hit_target_at = {
                                entity_id: when
                                for entity_id, when in
                                self.hit_target_at.items()
                                if when >= cutoff
                            }
                elif cast[2] is None or cast[2] != self.fight_target_id:
                    # A refusal of a cast aimed at a target the bot already
                    # left says nothing about the current fight; counting it
                    # would drop the new target for the old one's refusal.
                    self.cancel_streak = 0
                    self.cancel_streak_target = None
                else:
                    target = cast[2]
                    hit_at = self.hit_target_at.get(target)
                    recently_hit = (
                        hit_at is not None
                        and now - hit_at <= HIT_TARGET_GRACE_S
                    )
                    if recently_hit:
                        # The target has landed hits, so it is not a ghost.
                        # The refusal is pacing/state (skill cooldown, a late
                        # target selection, safe zone). Keep the focus and let
                        # the skill's own cooldown pace the next try.
                        self.cancel_streak = 0
                        self.cancel_streak_target = None
                        if now >= self.cancel_warn_at:
                            self.cancel_warn_at = now + 5.0
                            self._set_status(
                                "attack refused; keeping target "
                                "(it has been hit)"
                            )
                    else:
                        if target == self.cancel_streak_target:
                            self.cancel_streak += 1
                        else:
                            self.cancel_streak_target = target
                            self.cancel_streak = 1
                        if self.cancel_streak >= MAX_CANCEL_STREAK:
                            # Three casts in a row without a single `su`:
                            # the server does not see this monster (a
                            # client-only ghost from a kill while the bot was
                            # dead or out of range). It cannot be killed, so
                            # drop it and let the bot pick a live target. Any
                            # target that lands a hit resets the streak and
                            # is kept until it dies (strict focus).
                            world.entities.pop(target, None)
                            self.target_blocked[target] = (
                                now + TARGET_BLOCK_S
                            )
                            self._set_status(
                                f"target {target} refused "
                                f"{self.cancel_streak} attacks and never "
                                "landed a hit; dropping it"
                            )
                            self.fight_target_id = None
                            self.cancel_streak = 0
                            self.cancel_streak_target = None
                        else:
                            self._set_status(
                                "attack cancelled; retrying"
                            )
                    self.cancel_events.append(now)
                    self.cancel_events = [
                        when
                        for when in self.cancel_events
                        if now - when <= CANCEL_WINDOW_S
                    ]
                    if (
                        len(self.cancel_events) >= CANCEL_PAUSE_TRIGGER
                        and now >= self.combat_paused_until
                    ):
                        self.cancel_events.clear()
                        self.combat_pause_pos = self._player_cell()
                        self.combat_paused_until = now + CANCEL_PAUSE_S
                        self._set_status(
                            "attacks keep being refused (safe zone / "
                            "no line of sight); pausing combat"
                        )

        # -- recovery: sit below the configured HP/MP %, wait until full --
        # Follow mode never sits: the character would fall behind the player
        # it is supposed to trail (the Follow tooltip promises the same).
        recover_hp = (
            self.recover_toggle is not None
            and self.recover_toggle.get_active()
            and not follow_enabled
        )
        recover_mp = (
            self.recover_mp_toggle is not None
            and self.recover_mp_toggle.get_active()
            and not follow_enabled
        )
        hp_max = world.stats.hp_max
        mp_max = world.stats.mp_max
        hp_pct = 100.0 * world.stats.hp / hp_max if hp_max > 0 else None
        mp_pct = 100.0 * world.stats.mp / mp_max if mp_max > 0 else None
        hp_threshold = float(self.low_hp_pct.get_value())
        mp_threshold = float(self.low_mp_pct.get_value())
        hp_low = (
            recover_hp and hp_pct is not None and hp_pct < hp_threshold
        )
        mp_low = (
            recover_mp and mp_pct is not None and mp_pct < mp_threshold
        )
        # Recovery is pending as soon as a threshold is crossed: from then
        # on the bot must not start new fights or walk the route; it only
        # retaliates and finishes the current kill, then sits.
        self.recovery_pending = bool(hp_low or mp_low or self.recovering)
        if self.recovery_pending and (
            self.pending_is_path or self.pending_loot_id is not None
        ):
            # A threshold was crossed mid-walk: stop the route/loot leg at
            # once instead of finishing it first.
            if self.pending_loot_id is not None:
                self.pickup_ignored[self.pending_loot_id] = (
                    time.monotonic() + 15.0
                )
            self.pending_target = None
            self.pending_is_path = False
            self.pending_loot_id = None
            self.rejected_walks = 0
            self._set_status("recovery pending; stopping the walk to heal")
        if (recover_hp or recover_mp) and char_id is not None:
            if not self.recovering and self.fight_target_id is not None:
                if self.fight_target_id not in world.entities:
                    self.fight_target_id = None
            # Sitting under fire gets the character killed. A recent hit
            # forbids resting even when the attacker is not resolvable (it may
            # have been dropped from the world state or be out of sight); a
            # resolvable attacker within reach is fought back.
            attacker = (
                self._retaliation_target(int(self._attack_reach()))
                if not self._combat_paused(now)
                else None
            )
            if attacker is not None or self._under_attack():
                # Retaliation has priority over any walk in progress.
                if self.pending_is_path or self.pending_loot_id is not None:
                    if self.pending_loot_id is not None:
                        self.pickup_ignored[self.pending_loot_id] = (
                            time.monotonic() + 15.0
                        )
                    self.pending_target = None
                    self.pending_is_path = False
                    self.pending_loot_id = None
                if self.recovering:
                    self.recovering = False
                    self.recover_need_hp = False
                    self.recover_need_mp = False
                    # Without this, the start-recovery block below sees the
                    # still-low HP in the SAME tick and starts again, then
                    # the next tick aborts again: a log spam loop.
                    hp_low = mp_low = False
                    if world.resting is True and char_id is not None:
                        self._send(f"rest 1 1 {char_id}")
                    self._set_status(
                        "attacked while recovering; fighting back"
                        if attacker is not None
                        else "attacked while recovering; standing up"
                    )
                elif hp_low or mp_low:
                    hp_low = mp_low = False
            if not self.recovering and (hp_low or mp_low):
                if self._finish_kill_first():
                    # Keep fighting; the next tick after the kill starts
                    # recovery. Fall through to combat.
                    hp_low = mp_low = False
            if not self.recovering and (hp_low or mp_low):
                self.recovering = True
                self.recover_need_hp = hp_low
                self.recover_need_mp = mp_low
                self.pending_target = None
                self.pending_is_path = False
                self.pending_loot_id = None
                self.attack_cooldown_until = 0.0
                reasons = []
                if hp_low:
                    reasons.append(
                        f"HP {hp_pct:.0f}% below {hp_threshold:.0f}%"
                    )
                if mp_low:
                    reasons.append(
                        f"MP {mp_pct:.0f}% below {mp_threshold:.0f}%"
                    )
                self._set_status("; ".join(reasons) + "; recovering")
            if self.recovering:
                was_hp = self.recover_need_hp
                was_mp = self.recover_need_mp
                if self.recover_need_hp and (
                    hp_max <= 0 or world.stats.hp >= hp_max
                ):
                    self.recover_need_hp = False
                if self.recover_need_mp and (
                    mp_max <= 0 or world.stats.mp >= mp_max
                ):
                    self.recover_need_mp = False
                if not self.recover_need_hp and not self.recover_need_mp:
                    self.recovering = False
                    if world.resting is True:
                        self._send(f"rest 1 1 {char_id}")
                    names = "HP and MP" if was_hp and was_mp else (
                        "HP" if was_hp else "MP"
                    )
                    self._set_status(f"{names} full; resuming")
                    self._schedule_stats_redraw()
                    return True
                if world.resting is not True and now >= self.recover_rest_at:
                    self._send(f"rest 1 1 {char_id}")
                    self.recover_rest_at = now + REST_RETRY_S
                    self._set_status("sitting to recover")
                self._schedule_stats_redraw()
                return True
        elif self.recovering:
            self.recovering = False

        # Loot first: drops are picked up right away, also under attack. Only
        # a low-HP character (below the Recovery HP threshold) cancels a loot
        # walk and skips pickups until the attacker is gone; the combat block
        # runs this tick instead.
        under_attack = self._under_attack()
        defend_first = self._defend_before_loot(under_attack)
        if defend_first and self.pending_loot_id is not None:
            self.pending_target = None
            self.pending_loot_id = None
            self.pending_is_path = False
            self._set_status(
                "attacked with low HP; defending instead of picking up"
            )

        if self.pending_target is not None:
            # A seated client ignores MoveTo, so the pending leg would time
            # out. A stale POS can hide the sit state until the first MOVE is
            # ignored (the live 2026-11-08 failure), so check it here instead
            # of waiting for the timeout: stand first, then re-plan the leg.
            if (
                world.resting is True
                and now >= self.stand_sent_until
            ):
                standing_id = self._char_id()
                if standing_id is not None:
                    self._send(f"rest 1 1 {standing_id}")
                    self.stand_sent_until = now + REST_RETRY_S
                    self.pending_target = None
                    self.pending_is_path = False
                    self.pending_loot_id = None
                    self.rejected_walks = 0
                    self._set_status("sitting; standing up before walking")
                    return True
            # The client's own MoveTo sends a `walk` SEND packet; the engine
            # updates world.position from it. A server `tp`/`mv` is accepted
            # too, in case the server corrects the client.
            self._travel_request_position(now)
            actual = self._player_cell()
            loop_on = bool(
                self.loop_toggle is not None
                and self.loop_toggle.get_active()
            )
            has_next = self.pending_is_path and (
                self.path_index + 1 < len(self.bot_goals) or loop_on
            )
            remaining = None
            if actual is not None:
                remaining = max(
                    abs(actual[0] - self.pending_target[0]),
                    abs(actual[1] - self.pending_target[1]),
                )
            if (
                has_next
                and remaining is not None
                and remaining <= TRAVEL_HANDOFF_CELLS
                and now - self.pending_since >= TRAVEL_MIN_LEG_S
            ):
                # Hand the next leg to the client while the character is
                # still moving, so the drawn-path walk never stops at a leg
                # boundary (the same rule as click-to-walk).
                self.pending_target = None
                self.pending_loot_id = None
                self.rejected_walks = 0
                if self.pending_is_path:
                    self._advance_path()
                self.pending_is_path = False
                return True
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
                arrived = self.pending_target
                self._set_status(f"arrived at {arrived}")
                self.pending_target = None
                self.pending_loot_id = None
                self.rejected_walks = 0
                if self.pending_is_path:
                    self._advance_path()
                self.pending_is_path = False
            elif now > self.pending_until:
                self.rejected_walks += 1
                loot_id, self.pending_loot_id = self.pending_loot_id, None
                if loot_id is not None:
                    # This drop cannot be reached; stop trying for a while.
                    self.pickup_ignored[loot_id] = now + 10.0
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
                if self.pending_is_path:
                    self._advance_path()
                self.pending_is_path = False
            return True

        char_id = self._char_id()
        if world.resting is True and char_id is not None and now >= self.stand_sent_until:
            self._send(f"rest 1 1 {char_id}")
            self.stand_sent_until = now + REST_RETRY_S
            self._set_status("standing up")
            return True

        # Follow mode owns the tick: while it is on, the character only keeps
        # close to the named player (the player's own client does the rest).
        if follow_enabled:
            if not follow_name:
                self._set_status("follow is on; type the player name")
                return True
            return self._follow_step(follow_name, now)

        engaged = self._engaged_target()
        if (
            self.pickup_toggle is not None
            and self.pickup_toggle.get_active()
            and char_id is not None
            and now >= self.pickup_cooldown_until
        ):
            drop = self._nearest_drop(LOOT_APPROACH_CELLS)
            if drop is not None:
                position = self._player_cell()
                distance = None
                if position is not None:
                    distance = max(
                        abs(drop.x - position[0]), abs(drop.y - position[1])
                    )
                if distance is not None and distance > PICKUP_RANGE_CELLS:
                    # Never walk away from a live fight: the drop at the feet
                    # is still collected (one `get`, no movement), but a
                    # farther one waits until the target dies.
                    if (
                        defend_first
                        or engaged is not None
                        or self.recovery_pending
                    ):
                        drop = None
                    elif self._walk_to_loot(drop, now):
                        return True
                    else:
                        self.pickup_ignored[drop.drop_id] = now + 5.0
                elif self._send(f"get 1 {char_id} {drop.drop_id}"):
                    attempts = self.pickup_attempts.get(drop.drop_id, 0) + 1
                    self.pickup_attempts[drop.drop_id] = attempts
                    if attempts >= 3:
                        self.pickup_ignored[drop.drop_id] = now + 15.0
                    self.pickup_cooldown_until = now + PICKUP_COOLDOWN_S
                    self._set_status(
                        f"pickup {drop.item_vnum} x{drop.amount} "
                        f"({drop.drop_id})"
                    )
                if drop is not None:
                    return True

        auto_attack = (
            self.auto_attack_toggle is not None
            and self.auto_attack_toggle.get_active()
        )
        # Retaliate defends on its own: with Auto-attack off, a monster that
        # hits the character is still attacked back. Only the attacker is
        # targeted, never a passive neighbour.
        retaliate = (
            self.retaliate_toggle is not None
            and self.retaliate_toggle.get_active()
        )
        if (auto_attack or retaliate) and not self._combat_paused(now):
            radius = ATTACK_SEARCH_CELLS
            base_info = self.spells.get(self._basic_skill_vnum() or 0)
            if base_info is not None and base_info.range > 0:
                radius = max(radius, int(base_info.range))
            monster = None
            if retaliate:
                # Focus fire: a live target is kept until it dies. Retaliate
                # only chooses the first target (the monster that hit you)
                # when there is no lock; it never steals a live one, so two
                # alternating attackers cannot make the bot ping-pong.
                locked = self._locked_target(radius)
                if locked is not None:
                    monster = locked
                else:
                    monster = self._retaliation_target(radius)
            if monster is None and auto_attack:
                # Stay on the current target until it dies or becomes
                # invalid; only then pick the nearest monster.
                monster = self._locked_target(radius)
            if (
                monster is None
                and auto_attack
                and not self.recovery_pending
            ):
                # Do not start a new fight once a recovery threshold has
                # been crossed: retaliate and the current kill only.
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

            basic = 0
            basic_vnum = self._basic_skill_vnum()
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
            # A locked target behind cover is kept, not swapped: wait or walk
            # until the shot is clear. No attack ever goes through a wall.
            monster_sight = (
                monster is None or self._has_line_of_sight(monster)
            )
            if (
                rule is None
                and monster is not None
                and basic_in_range
                and monster_sight
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
                        if not self._ensure_target(monster.entity_id, now):
                            return True
                        packet = self._skill_packet(
                            monster.entity_id, skill=send_skill
                        )
                        self._send(packet)
                        self._note_attack(monster.entity_id, now)
                        self.fight_target_id = monster.entity_id
                        # The skill's own cast time and cooldown pace the next
                        # action; the Min delay spin is only a floor on top.
                        if basic_info is not None:
                            cooldown = max(
                                basic_info.cooldown,
                                basic_info.cast_time,
                            )
                        else:
                            # Unknown skill data: keep a safe 1 s floor.
                            cooldown = 1.0
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
                and (not basic_in_range or not monster_sight)
                and now >= self.attack_cooldown_until
                and not self.bot_goals
                and not self.recovery_pending
            ):
                # Stationary combat, like clicking a monster in game: walk to
                # it, then attack as soon as it is in range. An engaged
                # target that moved out of range/cover is followed too; the
                # bot only refuses to walk away from a target it can hit
                # (the combat block above returns in that case).
                if self._chase_step(monster, basic_range, now):
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

            if monster is not None and (rule is not None or basic_in_range):
                # Kill what is already in range before taking the next path
                # leg; the route resumes when no attackable monster is left.
                return True

        if self.recovery_pending:
            # A recovery threshold is crossed: stop the route here instead
            # of walking on. Retaliation and finishing the current kill are
            # handled above; the bot sits as soon as they are done.
            return True

        # No frozen "hold": a target that is in skill range and line is
        # already handled by the combat block above (it returns without
        # walking). Everything else — out of range, behind cover, or a
        # paused/refused fight — lets the path or the chase move the
        # character, otherwise the bot stands next to a monster it cannot
        # kill forever.

        if not self.bot_goals:
            # Stationary mode: combat/pickup/recovery/follow continue until
            # Stop.
            if (
                (self.follow_player_toggle is not None
                 and self.follow_player_toggle.get_active())
                or (self.auto_attack_toggle is not None
                 and self.auto_attack_toggle.get_active())
                or (self.pickup_toggle is not None
                    and self.pickup_toggle.get_active())
                or (self.recover_toggle is not None
                    and self.recover_toggle.get_active())
                or (self.recover_mp_toggle is not None
                    and self.recover_mp_toggle.get_active())
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
        if target in self._portal_cells():
            # The path was built before the portal was known (or before the
            # toggle came on). Skipping the leg is safer than retrying it.
            self._set_status(
                f"portal at {target} blocks this path leg; skipping it"
            )
            self._advance_path()
            return True
        current = self._player_cell()
        if current is not None and current == target:
            self._advance_path()
            return True

        # A raw waypoint (or a goal reached after a loop) can be far away.
        # The client/server accept only short hops, so walk toward it in
        # MAX_MOVE_LEG_CELLS-cell steps and only advance the route when the
        # goal itself is reached.
        goal_reached = True
        if (
            current is not None
            and max(
                abs(target[0] - current[0]), abs(target[1] - current[1])
            )
            > MAX_MOVE_LEG_CELLS
        ):
            if self.map_grid is None:
                self._set_status(
                    f"goal {target} is far and no map is loaded; skipping"
                )
                self._advance_path()
                return True
            path = astar(
                self.map_grid, current, target, self._portal_cells()
            )
            if path is None:
                self._set_status(
                    f"no walkable path to {target}; skipping this leg"
                )
                self._advance_path()
                return True
            goal_reached = False
            target = path[min(MAX_MOVE_LEG_CELLS - 1, len(path) - 1)]

        if now < self.walk_not_before:
            return True

        if self._client_move(target[0], target[1]):
            self.pending_target = target
            self.pending_is_path = goal_reached
            self.pending_loot_id = None
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
        self._client_generation += 1
        self._stop_bot()
        # Restore the client's normal speed before the link goes away. The
        # DLL also reverts when the host disconnects, but an explicit 0 makes
        # the shutdown deterministic.
        if self.client is not None and self.client.connected:
            try:
                self.client.send_raw("SPEED|0")
            except (ConnectionLost, ValueError):
                pass
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
