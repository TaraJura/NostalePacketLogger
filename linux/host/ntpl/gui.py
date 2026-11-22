"""GTK4 front end: packet table + map/path/auto-attack controls.

The GTK loop and the socket reader never share a thread: the reader pushes into
a ``queue.Queue`` and a 50 ms GLib timeout drains it on the main loop. The bot
runs on the same GLib main loop and only sends packets through the client; it
never touches GTK from a worker thread.
"""

from __future__ import annotations

import json
import math
import os
import queue
from collections import deque
import threading
import time
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from . import __version__
from .bazar import (
    MONITOR_SOURCES,
    BazarController,
    BazarError,
    BazarItem,
    BazarQuote,
    build_search_packet,
    default_config_path as default_bazar_config_path,
    inventory_search_items,
    load_config as load_bazar_config,
    monitor_source,
    normalize_search_packet,
    save_config as save_bazar_config,
    search_vnums,
    tracked_vnums,
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
from .minigame import (
    MAX_INTERVAL_MS,
    MAX_RADIUS,
    MIN_INTERVAL_MS,
    MIN_RADIUS,
    MinigameConfig,
    load_config as load_minigame_config,
    nearest_player,
    nearby_players,
    normalize_key,
    save_config as save_minigame_config,
)
from .monsternames import load_monster_names, load_skill_descriptions
from .mirror import (
    KIND_MOVE,
    KIND_SPELL,
    MirrorReader,
    MirrorWriter,
)
from .nosmall import ItemInfo, load_item_info
from .spawnlog import (
    client_log_path,
    inventory_cache_path,
    load_bazaar_open,
    load_gates,
    load_inventory as load_log_inventory,
    load_inventory_cache,
    load_last_position,
    log_inventory_bags,
    load_player_metadata,
    load_player_spawns,
    load_portals as load_log_portals,
    load_skill_list,
    load_skill_slots,
    load_spawns,
    load_timespaces,
    log_was_truncated,
    save_inventory_cache,
)
from .spells import SpellInfo, load_spells
from .protocol import Kind, Packet, ProtocolError

#: nosapki.com's Time-Space stone icon (64x64 PNG), drawn on the map at a
#: `gp` destination in the 2xxx range. Missing assets fall back to the ring.
ASSET_DIR = Path(__file__).with_name("assets")
TIMESPACE_ICON = ASSET_DIR / "ts-1.png"

#: Inventory bags the NosBazar picker names. The live server also sends
#: equipment-style bags 6/7 (SP cards/costumes); they show as "Bag N".
BAG_LABELS = {0: "Equip", 1: "Main", 2: "Etc"}

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
#: Speed + has no small host cap. The DLL lifts the client's own 59 limit and
#: emulates values past the speed byte; two billion keeps the protocol value
#: inside a signed 32-bit int while leaving effectively no scroll limit.
SPEED_BOOST_MAX = 2000000000
#: A name lookup that found neither the local DB nor Item.dat is retried on
#: the next trigger after this cooldown, so ``ntpl db build`` takes effect
#: without a restart while a missing data source is not re-parsed on every
#: click.
ITEM_INFO_RETRY_S = 5.0
#: Family auto-invite: one invite at a time, wait for the server's
#: confirmation, and never retry a name (the list is persisted per client).
FAMILY_AUTO_INTERVAL_S = 2.5
FAMILY_CONFIRM_TIMEOUT_S = 4.0
FAMILY_COOLDOWN_S = 15.0
FAMILY_AUTO_TICK_MS = 250
#: Minigame key spam: one GLib tick drives both the key interval and the
#: (slower) player focus, so changing a spin does not recreate a timer.
MINIGAME_TICK_MS = 20
MINIGAME_FOCUS_INTERVAL_S = 0.5
#: Safety refresh for the selected player. The normal path re-selects as soon
#: as the client's own target (`ncif`) is no longer that player, so a sheep
#: cannot become the target of the next key press; this bound only re-asserts
#: a selection that the client never reported.
MINIGAME_FOCUS_REFRESH_S = 5.0
MINIGAME_RENDER_INTERVAL_S = 0.5
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
#: Cast speed manual assist: the last skill the player pressed is repeated
#: when its server cooldown ends (`sr`). It stops after this long without a
#: repeat, so it cannot keep casting for an idle player forever.
CAST_ASSIST_TTL_S = 120.0
#: Hard stop for one assist session, so a forgotten switch cannot keep
#: casting for hours (the player can press again to start a new session).
CAST_ASSIST_MAX_S = 300.0
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
        self.speed_boost_scale = None
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
        self.bazar = BazarController(
            self._bazar_send,
            self._bazar_log,
            item_label=self._bazar_item_label,
        )
        self.bazar_timer = None
        self.bazar_page = None
        self.bazar_config_path = default_bazar_config_path()
        self.bazar_config_label = None
        self.bazar_info = None
        self.bazar_status = None
        self.bazar_results_box = None
        # The Monitor section's third-level views: NosMall (ND/no cost) and
        # NPC resale (gold cost). Each view owns its table and Only priced
        # filter; the singular attributes above point at the active view for
        # older tests and the item-name refresh path.
        self.bazar_results_boxes: dict[str, Gtk.Box] = {}
        self.bazar_results_infos: dict[str, Gtk.Label] = {}
        self.bazar_only_priced_checks: dict[str, Gtk.CheckButton] = {}
        self.bazar_monitor_notebook = None
        self._bazar_monitor_sources = MONITOR_SOURCES
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
        # Second-level navbar: Inventory, Relist, Monitor, Items, Config.
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
        # Relist tracking controls: the "Only relisting" filter and the
        # cross-section buttons that add or remove an item from the set.
        self.bazar_relist_filter = None
        self.bazar_relist_add_items_button = None
        self.bazar_relist_add_inventory_button = None
        self.bazar_relist_remove_button = None
        self.bazar_relist_group_button = None
        self._bazar_render_version = -1
        self._bazar_character_autofilled = False
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
        window = Gtk.ApplicationWindow(
            application=self,
            title=f"NosTale Packet Logger {__version__}",
        )
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
        self.packet_page = packet_page
        map_page = self._build_map_page()
        log_page = self._build_log_page()
        bazar_page = self._build_bazar_page()
        family_page = self._build_family_page()
        self.family_page = family_page
        malicious_page = self._build_malicious_page()
        minigame_page = self._build_minigame_page()

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
            self._bazar_render_relist_items()
            self._update_bazar_inventory_info()
            self._bazar_update_relist_info()
            if self._inventory_list is not None:
                self._inventory_schedule_render()
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
        self._bazar_render_relist_items()
        self._bazar_update_relist_info()
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
        self._bazar_update_relist_info()

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
        self._bazar_update_relist_info()

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

    # -- NosBazar inventory --------------------------------------------

    def _world_inventory_slots(self) -> list[InventorySlot]:
        """Every known slot, ordered by bag and slot index.

        The engine keeps all bags it received, including the equipment bags
        (``0`` Equip and the ``6``/``7`` extras); the inventory picker and the
        summary need them, not only the Main/Etc bags relisting uses.
        """
        world = self.engine.world
        return [
            slot
            for bag in sorted(world.inventory)
            for _index, slot in sorted(world.inventory[bag].items())
        ]

    def _inventory_slots(self) -> list[InventorySlot]:
        """Live slots, backfilled from the client log when none arrived."""
        slots = self._world_inventory_slots()
        if not slots:
            self._bazar_seed_from_log()
            slots = self._world_inventory_slots()
        return slots

    @staticmethod
    def _inventory_distinct(slots: list[InventorySlot]) -> set[int]:
        return {abs(slot.vnum) for slot in slots if slot.vnum != 0}

    def _update_bazar_inventory_info(self) -> None:
        label = self.bazar_inventory_info
        if label is None:
            return
        slots = self._world_inventory_slots()
        distinct = self._inventory_distinct(slots)
        tracked = tracked_vnums(self.bazar.config.items)
        tracked_count = sum(1 for vnum in distinct if vnum in tracked)
        if slots:
            text = (
                f"{len(slots)} slot(s) · {len(distinct)} item(s) · "
                f"{tracked_count} tracked"
            )
            if self._inventory_cache_note and self._inventory_core_seen:
                text += " · cached"
            if not self._inventory_core_seen:
                text += " · partial (relog for all)"
            label.set_text(text)
        else:
            label.set_text(
                "DLL log truncated before the inventory · relog with this "
                "window open"
                if self._inventory_log_truncated
                else "Inventory not received · relog with this window open"
            )
        if self.bazar_price_all_button is not None:
            self.bazar_price_all_button.set_sensitive(bool(slots))

    def _maybe_load_inventory_cache(self) -> bool:
        """Use this character's last complete inventory when nothing is live."""
        if (
            not self._inventory_seeded
            or self._inventory_cache_loaded
            or self._inventory_core_seen
        ):
            return False
        char_id = self.engine.world.self_id
        if not char_id:
            return False
        self._inventory_cache_loaded = True
        try:
            cached = load_inventory_cache(
                inventory_cache_path(char_id), char_id
            )
        except Exception:  # noqa: BLE001 - the cache is optional
            cached = None
        if cached is None or not cached[0]:
            return False
        bags, saved_at = cached
        # A full bag dump from this session is newer than the snapshot; keep
        # it whole. A bag known only from ivn updates is merged slot by slot,
        # so the snapshot fills the slots the host never saw.
        merged = {bag: dict(slots) for bag, slots in bags.items()}
        for bag, live_slots in self.engine.world.inventory.items():
            if bag in self._inventory_full_bags:
                merged[bag] = dict(live_slots)
            else:
                merged.setdefault(bag, {}).update(live_slots)
        self.engine.world.inventory = merged
        self._inventory_cache_dirty = False
        self._inventory_full_bags.clear()
        self._inventory_core_seen = True
        self._inventory_cache_note = (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(saved_at))
            if saved_at > 0
            else "unknown time"
        )
        self._bazar_log(
            f"inventory: cached snapshot from "
            f"{self._inventory_cache_note}"
        )
        self._update_bazar_inventory_info()
        if self._inventory_list is not None:
            self._inventory_schedule_render()
        return True

    def _save_inventory_cache(self, force: bool = False) -> None:
        if not self._inventory_core_seen:
            return
        if not force and not self._inventory_cache_dirty:
            return
        char_id = self.engine.world.self_id
        if not char_id or not self._world_inventory_slots():
            return
        try:
            save_inventory_cache(
                self.engine.world.inventory,
                char_id,
                inventory_cache_path(char_id),
            )
        except Exception as exc:  # noqa: BLE001 - never break the GUI
            self._bazar_log(f"inventory cache: {exc}")
            return
        self._inventory_cache_dirty = False

    def _schedule_inventory_cache_save(self) -> None:
        if self._inventory_cache_source is not None or self._closing:
            return
        self._inventory_cache_source = GLib.timeout_add(
            2000, self._inventory_cache_flush
        )

    def _inventory_cache_flush(self) -> bool:
        self._inventory_cache_source = None
        self._save_inventory_cache()
        return False

    def _ensure_item_info_loaded(self, after=None) -> None:
        """Resolve item names once, off the GTK loop, then run ``after``.

        A completed lookup with no data retries on a later page show or
        Inventory section switch after ``ITEM_INFO_RETRY_S``. A non-empty
        lookup is otherwise final; after rebuilding a stale database use the
        Configuration section's **Reload names** button
        (:meth:`_bazar_reload_names`), which drops the latch explicitly.
        """
        if self.item_info_loaded and self.item_info:
            if after is not None:
                after()
            return
        if self._item_info_loading:
            if after is not None:
                self._item_info_callbacks.append(after)
            return
        if self.item_info_loaded:
            now = time.monotonic()
            if now < self._item_info_retry_at:
                if after is not None:
                    after()
                return
            self.item_info_loaded = False
        if after is not None:
            self._item_info_callbacks.append(after)
        self._item_info_loading = True
        self._item_info_retry_at = time.monotonic() + ITEM_INFO_RETRY_S

        def work() -> None:
            info: dict[int, ItemInfo] = {}
            # The local SQLite DB is already built and resolves all item
            # names in milliseconds. Fall back to Item.dat only when the DB
            # is missing or empty.
            try:
                from . import database

                if database.DEFAULT_DB_PATH.is_file():
                    connection = database.connect()
                    try:
                        info = database.item_info(connection)
                    finally:
                        connection.close()
            except Exception:  # noqa: BLE001 - names are optional
                info = {}
            if not info:
                try:
                    info = load_item_info()
                except Exception:  # noqa: BLE001 - names are optional
                    info = {}
            GLib.idle_add(self._finish_item_info_load, info)

        threading.Thread(target=work, daemon=True).start()

    def _bazar_reload_names(self) -> None:
        """Drop the name-cache latch and look item names up again.

        A non-empty lookup is normally final, so a database rebuilt after the
        first load (e.g. one that was missing a vnum) would otherwise need a
        GUI restart. This is the explicit retry for that case.
        """
        if self._item_info_loading:
            self._bazar_log("item names are still loading")
            return
        self.item_info_loaded = False
        self._item_info_retry_at = 0.0
        self._bazar_log("reloading item names…")
        self._ensure_item_info_loaded()

    def _finish_item_info_load(self, info: dict[int, ItemInfo]) -> bool:
        if self._closing:
            return False
        had_names = bool(self.item_info)
        if info:
            self.item_info = info
        elif not self.item_info:
            self.item_info = {}
        # A failed explicit reload keeps the names already on screen.
        self.item_info_loaded = True
        self._item_info_loading = False
        if not info and had_names:
            self._bazar_log(
                "item names: reload found no source; keeping the names "
                "already shown"
            )
        elif not self.item_info:
            self._bazar_log(
                "inventory: item names unavailable; set NTPL_NSGTD or run: "
                "PYTHONPATH=linux/host python3 -m ntpl db build"
            )
        callbacks = self._item_info_callbacks
        self._item_info_callbacks = []
        if self._inventory_list is not None:
            self._inventory_render()
        # The Monitor table and both item lists show the real item names;
        # replace the shortcuts as soon as the lookup finishes. The lists are
        # refreshed in place: rebuilding them would re-select the current row
        # and overwrite unsaved editor values (and reset scroll positions).
        if self.bazar_results_box is not None:
            self._bazar_render_results()
        self._bazar_refresh_item_labels()
        self._update_bazar_inventory_info()
        for callback in callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001 - never break the load
                pass
        return False

    def _item_name_for(self, vnum: int) -> str:
        info = self.item_info.get(abs(vnum))
        if info is not None and info.name:
            return info.name
        return f"item {abs(vnum)}"

    def _bazar_item_label(self, item: BazarItem) -> str:
        """The real item name for a configured bazaar item.

        ``BazarItem.name`` is the owner's shortcut ("bubbl", "Wings of f");
        the Monitor table and every read-only list show the Item.dat name of
        the item's vnum instead.  The exact item vnum wins over the search
        group; a group falls back through its vnums until one resolves.  The
        configured label is only the last resort, so no row is ever blank.
        """
        candidates: list[int] = []
        if item.vnum:
            candidates.append(abs(int(item.vnum)))
        for vnum in item.vnums:
            value = abs(int(vnum))
            if value and value not in candidates:
                candidates.append(value)
        placeholder = ""
        for vnum in candidates:
            info = getattr(self, "item_info", {}).get(vnum)
            if info is None or not info.name:
                continue
            if info.name != f"item {vnum}":
                return info.name
            placeholder = placeholder or info.name
        return placeholder or item.name

    @staticmethod
    def _bazar_item_tooltip(item: BazarItem) -> str:
        """Configured label and search vnums, for a row's hover text."""
        parts = [f"Configured as: {item.name}"]
        vnums = item.vnums
        if vnums:
            parts.append("Search vnums: " + " ".join(str(v) for v in vnums))
        elif item.vnum:
            parts.append(f"Item vnum: {item.vnum}")
        return "\n".join(parts)

    def _item_npc_price(self, vnum: int) -> int:
        info = self.item_info.get(abs(vnum))
        return 0 if info is None else max(0, info.npc_price)

    def _open_inventory_picker(self) -> None:
        """Show the inline inventory section and refresh it."""
        self._bazar_show_section("inventory")
        self._ensure_item_info_loaded()
        if self._inventory_list is not None:
            self._inventory_render()

    def _inventory_reload(self) -> None:
        """Read the log again, refreshing slots missed before the attach."""
        self._bazar_seed_from_log()
        self._update_bazar_inventory_info()
        self._inventory_render()

    def _inventory_render(self) -> None:
        if self._inventory_list is None:
            return
        adjustment = None
        scroll_position = 0.0
        if self._inventory_scroll is not None:
            adjustment = self._inventory_scroll.get_vadjustment()
            scroll_position = adjustment.get_value()
        child = self._inventory_list.get_first_child()
        while child is not None:
            self._inventory_list.remove(child)
            child = self._inventory_list.get_first_child()

        slots = self._inventory_slots()
        keys = {(slot.bag, slot.index) for slot in slots}
        self._inventory_checks &= keys
        needle = ""
        if self._inventory_search is not None:
            needle = self._inventory_search.get_text().strip().casefold()
        matches: list[InventorySlot] = []
        for slot in slots:
            name = self._item_name_for(slot.vnum)
            haystack = (
                f"{name.casefold()} {slot.vnum} {abs(slot.vnum)} "
                f"{slot.bag} {slot.index}"
            )
            if needle and needle not in haystack:
                continue
            matches.append(slot)
        self._inventory_matches = matches

        distinct = self._inventory_distinct(slots)
        tracked = tracked_vnums(self.bazar.config.items)
        tracked_count = sum(1 for vnum in distinct if vnum in tracked)
        costs = self._nosmall_costs()
        if self._inventory_status is not None:
            if not slots:
                text = (
                    "The DLL log was truncated before the login inventory; "
                    "the server sends it only at login. Relog or change "
                    "channel with this window open; the scan fills "
                    "automatically."
                    if self._inventory_log_truncated
                    else "The server sends the full inventory only at "
                    "login. Relog or change channel with this window open; "
                    "the scan fills automatically."
                )
            else:
                text = (
                    f"{len(slots)} slot(s) · {len(distinct)} item(s) · "
                    f"{tracked_count} tracked · {len(matches)} shown"
                )
                if (
                    self._inventory_cache_note
                    and self._inventory_core_seen
                ):
                    text += f" · cached {self._inventory_cache_note}"
                if not self._inventory_core_seen:
                    text += (
                        " · partial inventory: the login dump is missing; "
                        "relog with this window open for the full list"
                    )
            if self._item_info_loading:
                text = "Loading item names… · " + text
            self._inventory_status.set_text(text)

        for slot in matches:
            name = self._item_name_for(slot.vnum)
            detail = (
                f"{BAG_LABELS.get(slot.bag, f'Bag {slot.bag}')} "
                f"{slot.index} · vnum {slot.vnum} · qty {slot.quantity}"
            )
            if slot.fields:
                detail += " · fields " + ".".join(slot.fields)
            npc_price = self._item_npc_price(slot.vnum)
            if npc_price > 0:
                detail += f" · NPC {npc_price:,} g"
            cost = costs.get(abs(slot.vnum))
            if cost:
                detail += f" · {cost:g} ND"

            content = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )
            labels = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=1
            )
            name_label = Gtk.Label(label=name)
            name_label.set_xalign(0)
            name_label.set_hexpand(True)
            name_label.set_wrap(True)
            name_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            name_label.set_max_width_chars(52)
            right = Gtk.GestureClick()
            right.set_button(3)

            def on_right(
                _gesture, _n_press, _x, _y, label=name_label, text=name
            ) -> None:
                self._bazar_copy_menu(label, text)

            right.connect("pressed", on_right)
            name_label.add_controller(right)
            detail_label = Gtk.Label(label=detail)
            detail_label.set_xalign(0)
            detail_label.add_css_class("dim-label")
            detail_label.set_ellipsize(Pango.EllipsizeMode.END)
            detail_label.set_max_width_chars(64)
            labels.append(name_label)
            labels.append(detail_label)
            content.append(labels)
            if abs(slot.vnum) in tracked:
                badge = Gtk.Label(label="tracked")
                badge.add_css_class("bazar-ok")
                content.append(badge)

            check = Gtk.CheckButton()
            check.set_hexpand(True)
            check.set_child(content)
            check.set_active((slot.bag, slot.index) in self._inventory_checks)
            check.connect(
                "toggled", self._inventory_toggled, slot.bag, slot.index
            )
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            row.set_child(check)
            self._inventory_list.append(row)

        self._inventory_update_add_button()
        if self._inventory_add_all_button is not None:
            self._inventory_add_all_button.set_sensitive(bool(slots))
        if adjustment is not None:
            GLib.idle_add(
                self._inventory_restore_scroll, adjustment, scroll_position
            )

    @staticmethod
    def _inventory_restore_scroll(
        adjustment: Gtk.Adjustment, value: float
    ) -> bool:
        top = adjustment.get_upper() - adjustment.get_page_size()
        adjustment.set_value(min(max(value, 0.0), max(0.0, top)))
        return False

    def _inventory_toggled(
        self, check: Gtk.CheckButton, bag: int, index: int
    ) -> None:
        key = (bag, index)
        if check.get_active():
            self._inventory_checks.add(key)
        else:
            self._inventory_checks.discard(key)
        self._inventory_update_add_button()

    def _inventory_update_add_button(self) -> None:
        if self._inventory_add_button is None:
            return
        count = len(self._inventory_checked_slots())
        self._inventory_add_button.set_label(f"Add selected ({count})")
        self._inventory_add_button.set_sensitive(count > 0)
        if self._inventory_setup_button is not None:
            self._inventory_setup_button.set_sensitive(count == 1)
            self._inventory_setup_button.set_label("Set up relist…")

    def _inventory_checked_slots(self) -> list[InventorySlot]:
        if not self._inventory_checks:
            return []
        return [
            slot
            for slot in self._inventory_slots()
            if (slot.bag, slot.index) in self._inventory_checks
        ]

    def _inventory_select_shown(self) -> None:
        for slot in self._inventory_matches:
            self._inventory_checks.add((slot.bag, slot.index))
        self._inventory_render()

    def _inventory_clear_selection(self) -> None:
        self._inventory_checks.clear()
        self._inventory_render()

    def _inventory_schedule_render(self) -> None:
        if self._inventory_render_pending:
            return
        self._inventory_render_pending = True
        GLib.idle_add(self._inventory_idle_render)

    def _inventory_idle_render(self) -> bool:
        self._inventory_render_pending = False
        if not self._closing and self._inventory_list is not None:
            self._inventory_render()
        return False

    def _inventory_add(
        self, slots: list[InventorySlot], start: bool = False
    ) -> list[BazarItem]:
        """Add the distinct items in ``slots`` to the bazaar item list.

        Returns the newly created items (empty when everything was already
        tracked); the Relist setup uses that to select the right row.
        """
        if not slots:
            self._bazar_log("inventory: no slots to add")
            return []
        if not self.item_info_loaded:
            if self._inventory_status is not None:
                self._inventory_status.set_text("Loading item names…")
            self._ensure_item_info_loaded(
                lambda: self._inventory_add(slots, start)
            )
            return []
        existing = list(self.bazar.config.items)
        names = {
            vnum: info.name
            for vnum, info in self.item_info.items()
            if info.name
        }
        npc_prices = {
            vnum: info.npc_price
            for vnum, info in self.item_info.items()
            if info.npc_price > 0
        }
        plan = inventory_search_items(
            slots,
            names,
            existing,
            self._nosmall_costs(),
            npc_prices,
        )
        created = list(plan.items)
        if plan.items:
            self._bazar_set_items(existing + plan.items)
            self._bazar_save_config(quiet=True)
        self._bazar_log(
            f"inventory: {len(plan.items)} item(s) added · "
            f"{plan.tracked} already tracked"
        )
        self._update_bazar_inventory_info()
        if self._inventory_list is not None:
            self._inventory_render()
        if self._inventory_status is not None:
            # After the re-render: the render writes the live summary and
            # would otherwise hide the add result.
            self._inventory_status.set_text(
                f"Added {len(plan.items)} · {plan.tracked} already tracked · "
                f"{len(self.bazar.config.items)} configured"
            )
        if start:
            self._bazar_start_monitor()
        return created

    def _inventory_set_up_relist(self) -> None:
        """Add one checked inventory slot and open its relist settings."""
        slots = self._inventory_checked_slots()
        if len(slots) != 1:
            self._bazar_log(
                "select exactly one inventory item to set up relisting"
            )
            return
        slot = slots[0]
        if slot.vnum <= 0:
            self._bazar_log(
                "bound/limited item (negative vnum) cannot be relisted"
            )
            return
        if slot.bag not in (0, 1, 2):
            self._inventory_add([slot])
            self._bazar_log(
                f"bag {slot.bag} is not a relistable tab; item added as "
                "monitor-only"
            )
            self._bazar_show_section("items")
            return
        if not self.item_info_loaded:
            self._ensure_item_info_loaded(self._inventory_set_up_relist)
            return
        created = self._inventory_add([slot])
        item = (
            created[0]
            if created
            else self._bazar_item_by_vnum(abs(slot.vnum))
        )
        if item is None:
            self._bazar_log("could not add the item to the bazaar list")
            return
        vnum = abs(slot.vnum)
        quantity = int(getattr(slot, "amount", slot.quantity) or 0)
        amount = min(1000, max(1, quantity))
        changed = False
        # Set up relist is an explicit "put this in the relist set" action;
        # a disabled item gets switched back on, otherwise the pass would
        # never search it.
        if not item.relist:
            item.relist = True
            changed = True
        if not item.enabled:
            item.enabled = True
            changed = True
        if item.vnum != vnum:
            item.vnum = vnum
            changed = True
        if item.inv_tab != slot.bag:
            item.inv_tab = slot.bag
            changed = True
        if item.amount != amount:
            item.amount = amount
            changed = True
        if changed:
            self._bazar_set_items(list(self.bazar.config.items))
            self._bazar_save_config(quiet=True)
        self._bazar_render_relist_items()
        self._bazar_select_relist_item(item.name)
        self._bazar_show_section("relist")
        self._bazar_update_relist_info()
        if item.min_price <= 0:
            self._bazar_log(
                f"{self._bazar_item_label(item)}: set a positive Min price, "
                "then Relist Start"
            )
            if self.bazar_relist_form:
                self.bazar_relist_form["min"].grab_focus()
        else:
            self._bazar_log(
                f"{self._bazar_item_label(item)}: relist settings ready "
                f"(floor {item.min_price:,})"
            )

    def _bazar_item_by_vnum(self, vnum: int) -> BazarItem | None:
        target = abs(int(vnum))
        for item in self.bazar.config.items:
            if target in tracked_vnums([item]):
                return item
        return None

    def _bazar_item_by_name(self, name: str | None) -> BazarItem | None:
        if not name:
            return None
        for item in self.bazar.config.items:
            if item.name == name:
                return item
        return None

    def _bazar_copy_label_for(self, name: str | None) -> str:
        """The name the item row's right-click menu copies."""
        item = self._bazar_item_by_name(name)
        return (name or "") if item is None else self._bazar_item_label(item)

    def _bazar_price_inventory(self) -> None:
        """Add every inventory item and start the monitor."""
        slots = self._inventory_slots()
        if not slots:
            self._bazar_log(
                "inventory is empty; relog or open the inventory once"
            )
            self._update_bazar_inventory_info()
            return
        self._inventory_add(slots, start=True)

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
        if packet.opcode in ("inv", "ivn"):
            if packet.opcode == "inv":
                try:
                    bag = int(packet.args[0])
                except (IndexError, ValueError):
                    bag = None
                if bag is not None:
                    self._inventory_full_bags.add(bag)
                # Only the login burst (bags 0, 1 and 2) makes the whole
                # inventory live; one updated bag must not hide that the
                # rest came from the cached snapshot.
                if {0, 1, 2} <= self._inventory_full_bags:
                    self._inventory_core_seen = True
                    self._inventory_cache_note = ""
            self._inventory_cache_dirty = True
            self._schedule_inventory_cache_save()
            self._update_bazar_inventory_info()
            if self._inventory_list is not None:
                self._inventory_schedule_render()
        if packet.opcode in (
            "rc_blist", "c_blist", "c_slist", "npc_req", "n_run", "in",
            "wopen", "inv", "ivn",
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
        self._bazar_update_relist_info()

    def _bazar_refresh_item_labels(self) -> None:
        """Update the item lists' names in place after the name lookup.

        Rebuilding the lists (``_bazar_render_items`` /
        ``_bazar_render_relist_items``) would re-select the remembered row,
        emit ``row-selected`` and overwrite unsaved editor values, and it
        would reset the lists' scroll positions. Updating only the labels
        and tooltips keeps the editors and scroll state intact.
        """
        items = {item.name: item for item in self.bazar.config.items}
        for listbox in (self.bazar_items_list, self.bazar_relist_list):
            if listbox is None:
                continue
            child = listbox.get_first_child()
            while child is not None:
                item = items.get(getattr(child, "item_name", None))
                label = getattr(child, "name_label", None)
                if item is not None and label is not None:
                    label.set_text(self._bazar_item_label(item))
                    label.set_tooltip_text(self._bazar_item_tooltip(item))
                child = child.get_next_sibling()
        item = items.get(self.bazar_relist_selected)
        if item is not None and self.bazar_relist_editor_label is not None:
            searches = " ".join(str(v) for v in item.vnums) or "no search"
            self.bazar_relist_editor_label.set_text(
                f"{self._bazar_item_label(item)} · search {searches}"
            )

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
            label_text = self._bazar_item_label(item)
            name = Gtk.Label(label=label_text)
            name.set_xalign(0)
            name.set_hexpand(True)
            name.set_tooltip_text(self._bazar_item_tooltip(item))
            row.name_label = name  # type: ignore[attr-defined]
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
                _gesture, _n_press, _x, _y, row=row, key=item.name
            ) -> None:
                # Resolve at click time: a late name load updates the label
                # in place, so a captured string would still copy the
                # configured shortcut.
                self.bazar_selected_name = key
                self.bazar_items_list.select_row(row)
                self._bazar_copy_menu(row, self._bazar_copy_label_for(key))

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

    # -- Relist section -------------------------------------------------

    def _bazar_render_relist_items(self) -> None:
        if self.bazar_relist_list is None:
            return
        # Ticking a row used to rebuild this list, which threw the scroll
        # position back to the top. Every render now keeps it (the same
        # pattern as the Inventory list).
        adjustment = None
        scroll_position = 0.0
        if self.bazar_relist_scroll is not None:
            adjustment = self.bazar_relist_scroll.get_vadjustment()
            scroll_position = adjustment.get_value()
        selected = self.bazar_relist_selected
        child = self.bazar_relist_list.get_first_child()
        while child is not None:
            self.bazar_relist_list.remove(child)
            child = self.bazar_relist_list.get_first_child()
        items = list(self.bazar.config.items)
        if self.bazar_relist_filter is not None and (
            self.bazar_relist_filter.get_active()
        ):
            items = [item for item in items if item.relist]
        if not items:
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            row.set_selectable(False)
            if self.bazar.config.items:
                text = (
                    "No item is in the relist set yet. Tick a row here, or "
                    "use Add from Items / Add from Inventory."
                )
            else:
                text = "No items yet. Add them in Inventory or Items."
            empty = Gtk.Label(label=text)
            empty.set_xalign(0)
            empty.set_wrap(True)
            empty.add_css_class("dim-label")
            row.set_child(empty)
            self.bazar_relist_list.append(row)
            self.bazar_relist_selected = None
            self._bazar_relist_item_selected(self.bazar_relist_list, None)
            self._bazar_update_relist_info()
            if adjustment is not None:
                GLib.idle_add(
                    self._inventory_restore_scroll,
                    adjustment,
                    scroll_position,
                )
            return
        for item in items:
            row = Gtk.ListBoxRow()
            row.item_name = item.name  # type: ignore[attr-defined]
            box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=1
            )
            name = Gtk.Label(label=self._bazar_item_label(item))
            name.set_xalign(0)
            name.set_hexpand(True)
            name.set_tooltip_text(self._bazar_item_tooltip(item))
            row.name_label = name  # type: ignore[attr-defined]
            name.set_wrap(True)
            name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            name.set_max_width_chars(36)
            detail = Gtk.Label(label=self._bazar_relist_detail(item))
            detail.set_xalign(0)
            detail.add_css_class("dim-label")
            detail.set_wrap(True)
            detail.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            detail.set_max_width_chars(44)
            box.append(name)
            box.append(detail)
            badge = Gtk.Label(label=self._bazar_relist_badge(item)[0])
            badge.add_css_class(self._bazar_relist_badge(item)[1])
            badge.set_valign(Gtk.Align.START)
            # Kept on the row so a tick can update it in place instead of
            # rebuilding the list under the user's cursor.
            row.detail_label = detail  # type: ignore[attr-defined]
            row.badge_label = badge  # type: ignore[attr-defined]
            # The tick is the item's membership in the relist set; the
            # badge explains why a ticked item still cannot send c_reg.
            track = Gtk.CheckButton()
            track.set_active(bool(item.relist))
            track.set_valign(Gtk.Align.START)
            track.set_tooltip_text(
                "Track this item for relisting. Untick to keep watching "
                "its price without changing the listing; the floor is kept."
            )
            handler = track.connect(
                "toggled",
                lambda button, name=item.name: (
                    self._bazar_relist_tracked_toggled(
                        name, button.get_active()
                    )
                ),
            )
            row.relist_check = track  # type: ignore[attr-defined]
            row.relist_handler = handler  # type: ignore[attr-defined]
            content = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )
            content.set_margin_end(10)
            content.append(track)
            content.append(box)
            content.append(badge)
            row.set_child(content)
            self.bazar_relist_list.append(row)
        if selected:
            self._bazar_select_relist_item(selected)
        else:
            self._bazar_relist_item_selected(self.bazar_relist_list, None)
        self._bazar_update_relist_info()
        if adjustment is not None:
            GLib.idle_add(
                self._inventory_restore_scroll, adjustment, scroll_position
            )

    @staticmethod
    def _bazar_relist_detail(item: BazarItem) -> str:
        parts = []
        if item.vnum:
            parts.append(f"vnum {item.vnum}")
        if item.inv_tab in (0, 1, 2):
            tab = {0: "Equip", 1: "Main", 2: "Etc"}[item.inv_tab]
            parts.append(tab)
        parts.append(f"amount {item.amount}")
        parts.append(f"min {item.min_price:,}")
        if not item.search_packet:
            parts.append("no search packet")
        if not item.enabled:
            parts.append("disabled")
        return " · ".join(parts)

    @staticmethod
    def _bazar_relist_ready(item: BazarItem) -> bool:
        return bool(
            item.relist
            and item.enabled
            and item.search_packet
            and item.vnum
            and item.vnum > 0
            and item.inv_tab in (0, 1, 2)
            and item.min_price > 0
        )

    def _bazar_relist_badge(self, item: BazarItem) -> tuple[str, str]:
        if not item.relist:
            return "not relisting", "dim-label"
        if not item.enabled:
            return "disabled", "dim-label"
        if not item.search_packet:
            return "no search", "bazar-warn"
        if self._bazar_relist_ready(item):
            return "ready", "bazar-ok"
        if item.vnum and item.inv_tab in (0, 1, 2):
            return "needs floor", "bazar-warn"
        return "needs vnum/tab", "bazar-warn"

    # -- relist tracking -------------------------------------------------

    def _bazar_relist_tracked_toggled(self, name: str, tracked: bool) -> None:
        """A row tick: add to or remove from the relist set immediately."""
        item = self._bazar_relist_set_tracked(name, tracked)
        if item is None:
            return
        # Update the row in place; a rebuild here was what threw a scrolled
        # list back to the top.
        self._bazar_relist_refresh_item(item)

    def _bazar_relist_row_by_name(self, name: str):
        if self.bazar_relist_list is None:
            return None
        child = self.bazar_relist_list.get_first_child()
        while child is not None:
            if getattr(child, "item_name", None) == name:
                return child
            child = child.get_next_sibling()
        return None

    def _bazar_relist_refresh_item(self, item: BazarItem) -> None:
        """Rewrite one row's badge/detail without rebuilding the list.

        A rebuild is only needed when the active filter now hides or shows
        the row; that render keeps the scroll position anyway.
        """
        row = self._bazar_relist_row_by_name(item.name)
        if row is not None:
            badge = getattr(row, "badge_label", None)
            if badge is not None:
                for css in ("bazar-ok", "bazar-warn", "dim-label"):
                    badge.remove_css_class(css)
                text, css = self._bazar_relist_badge(item)
                badge.set_text(text)
                badge.add_css_class(css)
            detail = getattr(row, "detail_label", None)
            if detail is not None:
                detail.set_text(self._bazar_relist_detail(item))
            # A button-driven change (Remove from relisting) must move the
            # row's tick too; block the handler so it is not a user action.
            check = getattr(row, "relist_check", None)
            if check is not None and check.get_active() != bool(item.relist):
                handler = getattr(row, "relist_handler", None)
                if handler is not None:
                    check.handler_block(handler)
                check.set_active(bool(item.relist))
                if handler is not None:
                    check.handler_unblock(handler)
        self._bazar_update_relist_info()
        filtered = (
            self.bazar_relist_filter is not None
            and self.bazar_relist_filter.get_active()
        )
        if row is None or (filtered and not item.relist):
            self._bazar_render_relist_items()

    def _bazar_relist_set_tracked(
        self, name: str | None, tracked: bool
    ) -> BazarItem | None:
        """Put one configured item in or out of the relist set.

        Removing keeps the item configured, monitored and its floor intact,
        so re-adding restores the previous setup. Adding re-enables an item
        that was switched off entirely, because tracking it is pointless
        while the Monitor skips it.
        """
        item = self._bazar_item_by_name(name)
        if item is None:
            return None
        if bool(item.relist) == bool(tracked) and (
            not tracked or item.enabled
        ):
            # Re-rendering can emit toggled for the state just set; only a
            # real change is worth a config write and a log line.
            return item
        changed = False
        if item.relist != bool(tracked):
            item.relist = bool(tracked)
            changed = True
        if tracked and not item.enabled:
            item.enabled = True
            changed = True
        if not changed:
            return item
        self._bazar_save_config(quiet=True)
        self._bazar_update_relist_info()
        label = self._bazar_item_label(item)
        if tracked:
            if self._bazar_relist_ready(item):
                self._bazar_log(
                    f"{label}: added to relisting (floor "
                    f"{item.min_price:,})"
                )
            else:
                self._bazar_log(
                    f"{label}: added to relisting; "
                    f"{self._bazar_relist_hint_text(item)}"
                )
        else:
            self._bazar_log(
                f"{label}: removed from relisting (still monitored; "
                "floor kept)"
            )
        return item

    def _bazar_relist_set_tracked_many(
        self, names: list[str], tracked: bool
    ) -> int:
        """Apply one relist action to several items with a single save."""
        changed = 0
        for name in names:
            item = self._bazar_item_by_name(name)
            if item is None:
                continue
            if bool(item.relist) == bool(tracked) and (
                not tracked or item.enabled
            ):
                continue
            item.relist = bool(tracked)
            if tracked:
                item.enabled = True
            changed += 1
        if not changed:
            return 0
        self._bazar_save_config(quiet=True)
        self._bazar_render_relist_items()
        self._bazar_log(
            f"{changed} item(s) "
            + ("added to relisting" if tracked else "removed from relisting")
        )
        return changed

    def _bazar_relist_shown_names(self) -> list[str]:
        if self.bazar_relist_list is None:
            return []
        names: list[str] = []
        child = self.bazar_relist_list.get_first_child()
        while child is not None:
            name = getattr(child, "item_name", None)
            if name:
                names.append(name)
            child = child.get_next_sibling()
        return names

    def _bazar_relist_selected_names(self) -> list[str]:
        if self.bazar_relist_list is None:
            return []
        return [
            name
            for row in self.bazar_relist_list.get_selected_rows()
            if (name := getattr(row, "item_name", None))
        ]

    def _bazar_relist_select_shown(self) -> None:
        if self.bazar_relist_list is None:
            return
        select_all = getattr(self.bazar_relist_list, "select_all", None)
        if select_all is not None:
            select_all()
            return
        child = self.bazar_relist_list.get_first_child()
        while child is not None:
            self.bazar_relist_list.select_row(child)
            child = child.get_next_sibling()

    def _bazar_relist_unselect_all(self) -> None:
        if self.bazar_relist_list is not None:
            self.bazar_relist_list.unselect_all()

    def _bazar_relist_set_all_shown(self, tracked: bool) -> None:
        names = self._bazar_relist_shown_names()
        if not names:
            self._bazar_log("the relist list is empty")
            return
        if not self._bazar_relist_set_tracked_many(names, tracked):
            self._bazar_log(
                "nothing changed: every shown item is already "
                + ("relisting" if tracked else "out of relisting")
            )

    def _bazar_relist_set_selected(self, tracked: bool) -> None:
        names = self._bazar_relist_selected_names()
        if not names:
            self._bazar_log(
                "select one or more rows in the relist list first "
                "(Ctrl+click for more)"
            )
            return
        if not self._bazar_relist_set_tracked_many(names, tracked):
            self._bazar_log("nothing changed for the selected rows")

    def _bazar_relist_remove_selected(self) -> None:
        name = self.bazar_relist_selected
        if not name:
            self._bazar_log("select an item in the relist list first")
            return
        item = self._bazar_relist_set_tracked(name, False)
        if item is not None:
            self._bazar_relist_refresh_item(item)

    def _bazar_relist_add_from_items(self) -> list[BazarItem]:
        """Move the Items section's selection into the relist set."""
        item = self._bazar_item_by_name(self.bazar_selected_name)
        if item is None:
            self._bazar_log("select an item in the Items section first")
            return []
        if item.relist and item.enabled:
            self._bazar_log(
                f"{self._bazar_item_label(item)}: already in relisting"
            )
        else:
            self._bazar_relist_set_tracked(item.name, True)
        self._bazar_render_relist_items()
        self._bazar_select_relist_item(item.name)
        return [item]

    def _bazar_relist_add_from_inventory(self) -> list[BazarItem]:
        """Add the Inventory section's ticked rows to the relist set."""
        slots = self._inventory_checked_slots()
        if not slots:
            self._bazar_log(
                "tick one or more inventory rows in the Inventory section "
                "first"
            )
            return []
        valid = [
            slot
            for slot in slots
            if slot.vnum > 0 and slot.bag in (0, 1, 2)
        ]
        skipped = len(slots) - len(valid)
        if skipped:
            self._bazar_log(
                f"{skipped} slot(s) cannot be relisted (bound vnum or "
                "bag 6/7); skipped"
            )
        if not valid:
            return []
        if not self.item_info_loaded:
            # Adding resolves Item.dat names; retry once they arrive.
            self._ensure_item_info_loaded(
                self._bazar_relist_add_from_inventory
            )
            return []
        created = self._inventory_add(valid)
        added: list[BazarItem] = []
        changed = bool(created)
        for slot in valid:
            item = self._bazar_item_by_vnum(abs(slot.vnum))
            if item is None:
                continue
            vnum = abs(slot.vnum)
            quantity = int(getattr(slot, "amount", slot.quantity) or 0)
            amount = min(1000, max(1, quantity))
            for attribute, value in (
                ("relist", True),
                ("enabled", True),
                ("vnum", vnum),
                ("inv_tab", slot.bag),
                ("amount", amount),
            ):
                if getattr(item, attribute) != value:
                    setattr(item, attribute, value)
                    changed = True
            added.append(item)
        if changed:
            self._bazar_save_config(quiet=True)
        self._bazar_render_items()
        self._bazar_render_relist_items()
        self._bazar_update_relist_info()
        if not added:
            return []
        self._bazar_select_relist_item(added[0].name)
        floors = [item for item in added if item.min_price <= 0]
        text = f"{len(added)} item(s) added to relisting"
        if floors:
            text += f"; {len(floors)} still need a Min price"
        self._bazar_log(text)
        return added

    @staticmethod
    def _bazar_relist_hint_text(item: BazarItem) -> str:
        if not item.search_packet:
            return "no search packet; add one in Items"
        if item.vnum is None or item.vnum <= 0:
            return "set the item vnum"
        if item.inv_tab not in (0, 1, 2):
            return "choose Equip, Main or Etc"
        if item.min_price <= 0:
            return "set a positive Min price to enable relisting"
        if item.vnum not in item.vnums:
            return (
                "item vnum is not in the search group; edit Search vnums "
                "in Items or use the exact inventory vnum"
            )
        return f"ready · floor {item.min_price:,} per unit"

    def _bazar_relist_item_selected(
        self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None
    ) -> None:
        if row is None:
            # In multiple-selection mode this signal also fires when one of
            # several rows is deselected; keep editing a row that remains
            # selected instead of disabling the whole editor.
            remaining = (
                listbox.get_selected_rows() if listbox is not None else []
            )
            if remaining:
                row = remaining[0]
            else:
                self.bazar_relist_selected = None
                if self.bazar_relist_editor_label is not None:
                    self.bazar_relist_editor_label.set_text(
                        "No item selected"
                    )
                if self.bazar_relist_hint is not None:
                    self.bazar_relist_hint.set_text("")
                self._bazar_set_relist_form_sensitive(False)
                return
        name = getattr(row, "item_name", None)
        if not name:
            return
        item = next(
            (
                existing
                for existing in self.bazar.config.items
                if existing.name == name
            ),
            None,
        )
        if item is None:
            return
        self.bazar_relist_selected = item.name
        self._bazar_relist_form_from_item(item)

    def _bazar_relist_form_from_item(self, item: BazarItem) -> None:
        form = self.bazar_relist_form
        if not form:
            return
        form["vnum"].set_text(
            "" if item.vnum is None else str(item.vnum)
        )
        form["tab"].set_selected(
            0 if item.inv_tab is None else item.inv_tab + 1
        )
        form["amount"].set_value(item.amount)
        form["min"].set_value(item.min_price)
        searches = " ".join(str(vnum) for vnum in item.vnums) or "no search"
        if self.bazar_relist_editor_label is not None:
            self.bazar_relist_editor_label.set_text(
                f"{self._bazar_item_label(item)} · search {searches}"
            )
        self._bazar_set_relist_form_sensitive(True)
        if self.bazar_relist_hint is not None:
            self.bazar_relist_hint.set_text(self._bazar_relist_hint_text(item))

    def _bazar_set_relist_form_sensitive(self, enabled: bool) -> None:
        for widget in self.bazar_relist_form.values():
            widget.set_sensitive(bool(enabled))
        if self.bazar_relist_save_button is not None:
            self.bazar_relist_save_button.set_sensitive(bool(enabled))
        if self.bazar_relist_open_button is not None:
            self.bazar_relist_open_button.set_sensitive(bool(enabled))

    def _bazar_select_relist_item(self, name: str | None) -> None:
        if self.bazar_relist_list is None:
            return
        if not name:
            self.bazar_relist_list.unselect_all()
            self._bazar_relist_item_selected(self.bazar_relist_list, None)
            return
        child = self.bazar_relist_list.get_first_child()
        while child is not None:
            if getattr(child, "item_name", None) == name:
                # A programmatic selection is single, even though the list
                # itself supports Ctrl+click groups.
                self.bazar_relist_list.unselect_all()
                self.bazar_relist_list.select_row(child)
                return
            child = child.get_next_sibling()
        self.bazar_relist_selected = None
        self.bazar_relist_list.unselect_all()
        self._bazar_relist_item_selected(self.bazar_relist_list, None)

    def _bazar_relist_save(self) -> None:
        name = self.bazar_relist_selected
        item = next(
            (
                existing
                for existing in self.bazar.config.items
                if existing.name == name
            ),
            None,
        )
        if item is None:
            self._bazar_log("select an item to save its relist settings")
            return
        form = self.bazar_relist_form
        if not form:
            return
        text = form["vnum"].get_text().strip()
        vnum = None
        if text:
            if not text.isdigit() or int(text) <= 0:
                self._bazar_log("item vnum must be a positive number")
                return
            vnum = int(text)
        tab_index = form["tab"].get_selected()
        inv_tab = None if tab_index == 0 else tab_index - 1
        amount = int(form["amount"].get_value())
        minimum = int(form["min"].get_value())
        item.vnum = vnum
        item.inv_tab = inv_tab
        item.amount = amount
        item.min_price = minimum
        if vnum is not None and item.search_packet:
            current = item.vnums
            if len(current) == 1 and current[0] != vnum:
                # A single-vnum search follows the relist target; a group or
                # raw packet is deliberately left alone.
                item.search_packet = build_search_packet([vnum])
        self._bazar_set_items(list(self.bazar.config.items))
        self._bazar_save_config(quiet=True)
        self._bazar_render_relist_items()
        self._bazar_select_relist_item(item.name)
        self._bazar_update_relist_info()
        if minimum <= 0:
            self._bazar_log(
                f"{self._bazar_item_label(item)}: set a positive Min price "
                "to enable relisting"
            )
        elif not item.relist:
            self._bazar_log(
                f"{self._bazar_item_label(item)}: relist settings saved "
                f"(floor {minimum:,} per unit); tick Relist to include it "
                "in the relist set"
            )
        else:
            self._bazar_log(
                f"{self._bazar_item_label(item)}: relist settings saved "
                f"(floor {minimum:,} per unit)"
            )

    def _bazar_open_full_editor(self) -> None:
        name = self.bazar_relist_selected
        self._bazar_show_section("items")
        if name:
            self._bazar_select_item(name)

    def _bazar_select_item(self, name: str) -> None:
        if self.bazar_items_list is None:
            return
        child = self.bazar_items_list.get_first_child()
        while child is not None:
            if getattr(child, "item_name", None) == name:
                self.bazar_items_list.select_row(child)
                return
            child = child.get_next_sibling()

    def _bazar_update_relist_info(self) -> None:
        label = self.bazar_relist_info
        if label is None:
            return
        character = ""
        if self.bazar_character_entry is not None:
            character = self.bazar_character_entry.get_text().strip()
        if not character:
            character = self.bazar.config.character.strip()
        npc = ""
        if self.bazar_npc_entry is not None:
            npc = self.bazar_npc_entry.get_text().strip()
        if not npc and self.engine.world.bazar.npc_id:
            npc = str(self.engine.world.bazar.npc_id)
        if not npc:
            npc = next(
                (
                    str(entity.entity_id)
                    for entity in self.engine.world.entities.values()
                    if getattr(entity, "vnum", 0) == 793
                ),
                "",
            )
        tracked = sum(
            1
            for item in self.bazar.config.items
            if item.relist and item.enabled
        )
        ready = sum(
            1
            for item in self.bazar.config.items
            if self._bazar_relist_ready(item)
        )
        parts = [
            f"character {character}" if character else "character missing",
            f"Bibi {npc}" if npc else "Bibi id missing",
            f"{tracked} relisting",
            f"{ready} ready",
        ]
        label.set_text(" · ".join(parts))

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
        form["enabled"].set_active(item.enabled)
        form["relist"].set_active(item.relist)
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
        form["enabled"].set_active(True)
        form["relist"].set_active(False)
        form["cost_type"].set_selected(0)
        form["cost"].set_value(0)
        form["unk1"].set_value(9)
        form["unk2"].set_value(4)
        form["durability"].set_value(1)
        form["medal"].set_value(2)
        form["name"].grab_focus()

    def _bazar_delete_item(self) -> None:
        name = self.bazar_selected_name
        if not name:
            self._bazar_log("select an item to delete")
            return
        item = self._bazar_item_by_name(name)
        label = self._bazar_item_label(item) if item is not None else name
        items = [
            entry for entry in self.bazar.config.items if entry.name != name
        ]
        self.bazar_selected_name = None
        self._bazar_set_items(items)
        self._bazar_new_item()
        self._bazar_save_config(quiet=True)
        # Last: _bazar_set_items stops a running pass and logs the stop, which
        # would otherwise replace this confirmation in the status bar.
        self._bazar_log(f"deleted {label}")

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
        # NPC resale: an Item.dat price is the usual gold cost. Fill it when
        # the user leaves the cost at 0, so adding Pet Food or Bell of Sweet
        # Home needs only the vnum and Cost = NPC gold.
        if cost_index == 2 and cost <= 0:
            lookup = vnum
            if lookup is None and search_packet:
                candidates = search_vnums(search_packet)
                if len(candidates) == 1:
                    lookup = candidates[0]
            if lookup is not None:
                info = self.item_info.get(abs(lookup))
                if info is not None and info.npc_price > 0:
                    cost = float(info.npc_price)
                    form["cost"].set_value(cost)
                    self._bazar_log(
                        f"NPC price for vnum {lookup} is "
                        f"{info.npc_price:,} g (Item.dat)"
                    )
                else:
                    self._bazar_log(
                        f"NPC price unknown for vnum {lookup}; enter it "
                        "by hand or wait for item names to load"
                    )
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
            enabled=bool(form["enabled"].get_active()),
            relist=bool(form["relist"].get_active()),
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
        self._bazar_render_relist_items()
        self._update_bazar_inventory_info()
        self._bazar_update_relist_info()
        if self._inventory_list is not None:
            self._inventory_schedule_render()

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
        search.set_hexpand(True)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        top.append(search)
        top.append(
            self._help_button(
                "NosMall catalog",
                f"{importable} importable NosMall item(s) with a known ND "
                "cost. Items without an ND cost cannot show a profit and are "
                "not listed; pets, fairies and partner supplies are excluded. "
                "To offer another item, add it with New and set its Cost "
                "first, or maintain the curated price map, then reopen this "
                "window.",
            )
        )
        box.append(top)
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
            "Add every matching group; a full pass is paced at 2.5 s."
        )
        self._nosmall_add_all_button.connect(
            "clicked", lambda *_: self._nosmall_add_all()
        )
        buttons.append(self._nosmall_add_all_button)
        prune = Gtk.Button(label="Remove unpriced & pets")
        prune.set_tooltip_text(
            "Remove unpriced items and pets. Click twice to confirm."
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
                self._nosmall_status.set_text("Nothing to remove.")
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
        if not self.bazar_results_boxes:
            return
        self._bazar_render_version = self.bazar.version
        quotes = self.bazar.sorted_quotes()
        for source in self._bazar_monitor_sources:
            self._bazar_render_monitor_view(source, quotes)

    def _bazar_render_monitor_view(
        self, source: str, quotes: list[BazarQuote]
    ) -> None:
        box = self.bazar_results_boxes.get(source)
        info = self.bazar_results_infos.get(source)
        if box is None:
            return
        child = box.get_first_child()
        while child is not None:
            box.remove(child)
            child = box.get_first_child()

        items = [
            quote
            for quote in quotes
            if monitor_source(quote.item) == source
        ]
        if not items:
            if info is not None:
                info.set_text("No items")
            empty = Gtk.Label(
                label=(
                    "No NPC-resale items yet."
                    if source == "npc"
                    else "No NosMall items yet."
                )
            )
            empty.set_xalign(0)
            empty.add_css_class("dim-label")
            box.append(empty)
            return

        check = self.bazar_only_priced_checks.get(source)
        only_priced = check is not None and check.get_active()
        if only_priced:
            visible = [quote for quote in items if quote.price is not None]
        else:
            visible = list(items)
        hidden = len(items) - len(visible)
        if info is not None:
            text = "Most profitable first"
            if only_priced:
                text += f" · {len(visible)} priced"
            if hidden:
                text += f" · {hidden} hidden"
            info.set_text(text)

        if not visible:
            empty = Gtk.Label(label="No priced items yet.")
            empty.set_xalign(0)
            empty.add_css_class("dim-label")
            box.append(empty)
            return

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(3)
        grid.set_margin_end(14)
        grid.set_margin_top(2)
        headers = (
            "Item",
            "Cost",
            "NB Price",
            "Markup" if source == "npc" else "Profit",
            "Seller",
            "Status",
        )
        aligns = (0.0, 0.0, 1.0, 1.0, 0.0, 0.0)
        for column, (title, align) in enumerate(zip(headers, aligns)):
            label = Gtk.Label()
            label.set_markup(f"<b>{title}</b>")
            label.set_xalign(align)
            grid.attach(label, column, 0, 1, 1)

        # Thin lines between the rows: without them the eye cannot tell which
        # Cost/ND or NB Price cell belongs to which item in a long list. The
        # header gets its own line, then each data row is followed by one
        # except the last (no trailing line).
        columns = len(headers)

        def separator(row: int) -> None:
            line = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            line.add_css_class("bazar-sep")
            line.set_hexpand(True)
            line.set_size_request(-1, 1)
            line.set_margin_top(3)
            line.set_margin_bottom(3)
            grid.attach(line, 0, row, columns, 1)

        separator(1)
        for row_index, quote in enumerate(visible):
            grid_row = 2 + row_index * 2
            price = "n/a" if quote.price is None else f"{quote.price:,}"
            seller = quote.seller.replace("�", "").strip()
            cells = (
                # The real Item.dat name, not the configured shortcut.
                self._bazar_item_label(quote.item),
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
                tooltip = []
                if column == 0:
                    # Keep the configured shortcut reachable without showing
                    # it as the item name.
                    tooltip.append(self._bazar_item_tooltip(quote.item))
                if quote.listing is not None:
                    raw = " | ".join(quote.listing.raw_fields)
                    if raw:
                        tooltip.append(raw)
                if tooltip:
                    label.set_tooltip_text("\n".join(tooltip))
                grid.attach(label, column, grid_row, 1, 1)
            if row_index < len(visible) - 1:
                separator(grid_row + 1)
        box.append(grid)

    def _bazar_seed_from_log(self) -> None:
        """Learn the Bibi id/option and inventory from this client's DLL log.

        A GUI attaches after login: the client's earlier manual bazaar opening
        and the login inventory dump are still in the log, not in the live
        packet stream.  The entity id and the inventory slots are needed
        before the first search/relist.
        """
        path = self.log_file
        self._inventory_log_truncated = log_was_truncated(path)
        if not path:
            self._inventory_seeded = True
            self._maybe_load_inventory_cache()
            self._save_inventory_cache()
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
            full_bags = log_inventory_bags(path)
            self._inventory_full_bags.update(full_bags)
            if {0, 1, 2} <= full_bags:
                # The log contains the complete login inventory (full inv
                # lines, not just ivn updates), so this is safe to cache
                # even when the live stream missed it.
                self._inventory_core_seen = True
            count = 0
            filled = 0
            for bag, slots in bags.items():
                # A full `inv <bag>` line is authoritative. A bag seen only
                # in `ivn` updates is partial: it may fill a bag the attach
                # missed, but must never replace existing live/cached slots
                # (a truncated log can hold just one update for a bag).
                if bag in full_bags or not world.inventory.get(bag):
                    world.inventory[bag] = dict(slots)
                    count += len(slots)
                    filled += 1
            if filled:
                self._inventory_cache_dirty = True
                self._bazar_log(
                    f"inventory: {count} slot(s) in {filled} bag(s) "
                    f"seeded from the log"
                )
        if gold is not None:
            world.gold = gold
        self._inventory_seeded = True
        self._maybe_load_inventory_cache()
        self._save_inventory_cache()
        self._bazar_sync_from_world()
        self._update_bazar_inventory_info()
        if self._inventory_list is not None:
            self._inventory_schedule_render()

    def _bazar_reset_for_attach(self, winpid: int) -> None:
        """Reset the tab together with the engine when another client attaches."""
        self._bazar_stop("client changed")
        self.bazar_selected_name = None
        self._bazar_config_loaded = False
        self._inventory_seeded = False
        self._inventory_cache_loaded = False
        self._inventory_cache_note = ""
        self._inventory_cache_dirty = False
        self._inventory_log_truncated = False
        self._inventory_full_bags.clear()
        self._inventory_core_seen = False
        if self._inventory_cache_source is not None:
            GLib.source_remove(self._inventory_cache_source)
            self._inventory_cache_source = None
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
        self._update_bazar_inventory_info()
        if self._inventory_list is not None:
            self._inventory_checks.clear()
            self._inventory_schedule_render()

    def _build_bazar_page(self):
        """NosBazar monitor, inventory scan and relisting.

        The page is a second-level notebook so each workflow owns a full
        section instead of squeezing every control into one sidebar.
        """
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.set_margin_top(6)
        page.set_margin_start(8)
        page.set_margin_end(8)
        page.set_margin_bottom(6)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        open_button = Gtk.Button(label="Open bazaar")
        open_button.set_tooltip_text(
            "Replay the Bibi Basar dialog through the client's send path."
        )
        open_button.connect("clicked", lambda *_: self._bazar_open())
        toolbar.append(open_button)
        self.bazar_info = Gtk.Label(label="NosBazar idle")
        self.bazar_info.set_xalign(0)
        self.bazar_info.set_hexpand(True)
        toolbar.append(self.bazar_info)
        toolbar.append(
            self._help_button(
                "NosBazar",
                "Section tabs: Inventory scans every owned slot and can set "
                "up relisting; Relist sets the floor, character and starts "
                "registrations; Monitor searches and shows prices/profit, "
                "with third-level NosMall (ND cost, gold per ND) and NPC "
                "resale (NPC gold cost, such as Pet Food or Bell of Sweet "
                "Home; gold markup) views; Items edits the searches and the "
                "Cost type that chooses the monitor; Configuration has the "
                "Bibi id, refresh and config file. Open bazaar replays the "
                "Bibi Basar dialog through the client's own send path. "
                "Relist changes real listings and spends the fee.",
            )
        )
        page.append(toolbar)

        sections = Gtk.Notebook()
        sections.set_vexpand(True)
        sections.set_scrollable(True)
        self.bazar_section_notebook = sections
        pages = (
            ("inventory", "Inventory", self._build_bazar_inventory_section()),
            ("relist", "Relist", self._build_bazar_relist_section()),
            ("monitor", "Monitor", self._build_bazar_monitor_section()),
            ("items", "Items", self._build_bazar_items_section()),
            ("config", "Configuration", self._build_bazar_config_section()),
        )
        for key, title, child in pages:
            index = sections.append_page(child, Gtk.Label(label=title))
            self._bazar_section_index[key] = index
        sections.connect("switch-page", self._bazar_section_switched)
        page.append(sections)
        page.connect("map", self._bazar_page_mapped)

        self.bazar_status = Gtk.Label(label="NosBazar idle")
        self.bazar_status.set_xalign(0)
        self.bazar_status.set_wrap(True)
        self.bazar_status.add_css_class("dim-label")
        page.append(self.bazar_status)

        self.bazar_page = page
        self._bazar_render_items()
        self._bazar_render_results()
        self._bazar_render_relist_items()
        self._update_bazar_inventory_info()
        if self._inventory_list is not None:
            self._inventory_render()
        self._bazar_update_info()
        self._bazar_update_relist_info()
        self._bazar_show_section("inventory")
        return page

    def _bazar_show_section(self, key: str) -> None:
        if self.bazar_section_notebook is None:
            return
        index = self._bazar_section_index.get(key)
        if index is not None:
            self.bazar_section_notebook.set_current_page(index)

    def _bazar_page_mapped(self, *_args) -> None:
        """Load item names when the NosBazar page becomes visible."""
        self._ensure_item_info_loaded()
        if self._inventory_list is not None:
            self._inventory_render()

    def _bazar_section_switched(
        self, _notebook: Gtk.Notebook, _page: Gtk.Widget, index: int
    ) -> None:
        if index != self._bazar_section_index.get("inventory"):
            return
        # Do not parse names at startup while the page is hidden; the map
        # signal handles the first real display.
        if self.bazar_page is not None and not self.bazar_page.get_mapped():
            return
        self._ensure_item_info_loaded()
        if self._inventory_list is not None:
            self._inventory_render()

    def _build_bazar_inventory_section(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.set_margin_top(6)
        page.set_margin_bottom(6)
        page.set_margin_start(6)
        page.set_margin_end(6)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._inventory_search = Gtk.SearchEntry()
        self._inventory_search.set_placeholder_text(
            "search inventory by item name or vnum"
        )
        self._inventory_search.set_hexpand(True)
        self._inventory_search.connect(
            "search-changed", lambda *_: self._inventory_render()
        )
        top.append(self._inventory_search)
        rescan = Gtk.Button(label="Rescan")
        rescan.set_tooltip_text(
            "Read the live inventory and the client log again."
        )
        rescan.connect("clicked", lambda *_: self._inventory_reload())
        top.append(rescan)
        self.bazar_price_all_button = Gtk.Button(label="Price all")
        self.bazar_price_all_button.set_tooltip_text(
            "Add every inventory item and start the monitor."
        )
        self.bazar_price_all_button.connect(
            "clicked", lambda *_: self._bazar_price_inventory()
        )
        top.append(self.bazar_price_all_button)
        content.append(top)

        self._inventory_status = Gtk.Label(label="")
        self._inventory_status.set_xalign(0)
        self._inventory_status.set_wrap(True)
        self._inventory_status.add_css_class("dim-label")
        content.append(self._inventory_status)
        # One live line for the summary and the filter result.
        self.bazar_inventory_info = self._inventory_status

        self._inventory_list = Gtk.ListBox()
        self._inventory_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self._inventory_list)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        self._inventory_scroll = scrolled
        content.append(scrolled)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        select_all = Gtk.Button(label="Select shown")
        select_all.connect(
            "clicked", lambda *_: self._inventory_select_shown()
        )
        buttons.append(select_all)
        clear = Gtk.Button(label="Clear")
        clear.connect(
            "clicked", lambda *_: self._inventory_clear_selection()
        )
        buttons.append(clear)
        self._inventory_add_button = Gtk.Button(label="Add selected (0)")
        self._inventory_add_button.add_css_class("suggested-action")
        self._inventory_add_button.set_sensitive(False)
        self._inventory_add_button.connect(
            "clicked",
            lambda *_: self._inventory_add(self._inventory_checked_slots()),
        )
        buttons.append(self._inventory_add_button)
        self._inventory_add_all_button = Gtk.Button(label="Add all")
        self._inventory_add_all_button.set_tooltip_text(
            "Add every inventory item without starting the monitor."
        )
        self._inventory_add_all_button.connect(
            "clicked",
            lambda *_: self._inventory_add(self._inventory_slots()),
        )
        buttons.append(self._inventory_add_all_button)
        self._inventory_setup_button = Gtk.Button(
            label="Set up relist…"
        )
        self._inventory_setup_button.set_tooltip_text(
            "Add exactly one checked item and open its relist setup."
        )
        self._inventory_setup_button.set_sensitive(False)
        self._inventory_setup_button.connect(
            "clicked", lambda *_: self._inventory_set_up_relist()
        )
        buttons.append(self._inventory_setup_button)
        content.append(buttons)

        inventory_frame = self._frame(
            "Inventory scan",
            content,
            help_text=(
                "Every slot the client sent in inv/ivn, including the "
                "equipment bags (Equip, Bag 6, Bag 7). Each row shows the "
                "Item.dat name, bag and slot, vnum, quantity, equipment "
                "fields, NPC price and known ND cost. Equipment rows "
                "report quantity 0 and count as one item. Negative vnums "
                "are limited/bound ids and cannot be relisted; their "
                "template is still searched. Add selected adds one "
                "bazaar item per distinct item; Set up relist adds the "
                "checked item and opens its relist settings; Add all adds "
                "every inventory item; Price all adds every item and "
                "starts Monitor. The server sends the inventory only at "
                "login: if it is "
                "missing or marked partial, relog or change channel with "
                "this window open. The last complete inventory is cached "
                "per character, so a later attach survives log "
                "truncation."
            ),
        )
        inventory_frame.set_vexpand(True)
        page.append(inventory_frame)
        return page

    def _build_bazar_relist_section(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.set_margin_top(6)
        page.set_margin_bottom(6)
        page.set_margin_start(6)
        page.set_margin_end(6)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        self.bazar_live_relist = Gtk.CheckButton(
            label="Allow live re-listing (changes your listings)"
        )
        self.bazar_live_relist.set_active(False)
        self.bazar_live_relist.set_tooltip_text(
            "Required for Relist Start; changes your prices and spends the fee."
        )
        content.append(self.bazar_live_relist)

        self.bazar_character_entry = Gtk.Entry()
        self.bazar_character_entry.set_placeholder_text("your character")
        self.bazar_character_entry.set_max_length(40)
        self.bazar_character_entry.set_width_chars(18)
        self.bazar_character_entry.set_tooltip_text(
            "Your character, to recognise your own listing."
        )
        self.bazar_character_entry.connect(
            "changed", lambda *_: self._bazar_update_relist_info()
        )
        content.append(
            self._field_row(
                Gtk.Label(label="Character"),
                self.bazar_character_entry,
            )
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
                    "Monitor keeps watching it. Add from Items takes the "
                    "selection in the Items section; Add from Inventory "
                    "takes the rows ticked in the Inventory section and "
                    "fills vnum, tab and amount from the slot. Remove from "
                    "relisting (or the row tick) keeps the item and its "
                    "floor, only out of the relist set. Only relisting "
                    "hides the rest of the list. Group actions… covers "
                    "every row shown (relist / stop relisting) or the rows "
                    "clicked with Ctrl (Relist selected / Stop relisting "
                    "selected), plus Select all shown and Unselect all. "
                    "Select an item, set the "
                    "exact Item vnum, the inventory Tab and the Amount, "
                    "then a positive Min price (the per-unit floor). Tick "
                    "Allow live re-listing and fill Character with your own "
                    "name so the host never undercuts you. Relist Start "
                    "searches only the relist set and sends one c_reg per "
                    "eligible item per pass at cheapest minus one gold, "
                    "but never below the floor. The server sends no "
                    "acknowledgement, so the status says register sent "
                    "(no ack); verify the listing in the bazaar. Items "
                    "without a vnum, tab or positive floor are skipped."
                ),
        )
        relist_frame.set_vexpand(True)
        page.append(relist_frame)
        return page

    def _build_bazar_relist_tracking(self) -> Gtk.Widget:
        """Add/remove/filter controls under the relist item list."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        add_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        self.bazar_relist_add_items_button = Gtk.Button(
            label="Add from Items"
        )
        self.bazar_relist_add_items_button.set_tooltip_text(
            "Add the item selected in the Items section to the relist set."
        )
        self.bazar_relist_add_items_button.connect(
            "clicked", lambda *_: self._bazar_relist_add_from_items()
        )
        add_row.append(self.bazar_relist_add_items_button)
        self.bazar_relist_add_inventory_button = Gtk.Button(
            label="Add from Inventory"
        )
        self.bazar_relist_add_inventory_button.set_tooltip_text(
            "Add the rows ticked in the Inventory section to the relist "
            "set, with their vnum, tab and stack amount."
        )
        self.bazar_relist_add_inventory_button.connect(
            "clicked", lambda *_: self._bazar_relist_add_from_inventory()
        )
        add_row.append(self.bazar_relist_add_inventory_button)
        box.append(add_row)
        manage_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        self.bazar_relist_remove_button = Gtk.Button(
            label="Remove from relisting"
        )
        self.bazar_relist_remove_button.set_tooltip_text(
            "Stop changing the selected listing; the item stays monitored "
            "and keeps its floor."
        )
        self.bazar_relist_remove_button.connect(
            "clicked", lambda *_: self._bazar_relist_remove_selected()
        )
        manage_row.append(self.bazar_relist_remove_button)
        self.bazar_relist_filter = Gtk.CheckButton(label="Only relisting")
        self.bazar_relist_filter.set_tooltip_text(
            "Hide items that are not in the relist set."
        )
        self.bazar_relist_filter.connect(
            "toggled", lambda *_: self._bazar_render_relist_items()
        )
        manage_row.append(self.bazar_relist_filter)
        box.append(manage_row)
        self.bazar_relist_group_button = (
            self._build_bazar_relist_group_button()
        )
        box.append(self.bazar_relist_group_button)
        return box

    def _build_bazar_relist_group_button(self) -> Gtk.MenuButton:
        """Bulk actions for the rows shown or the Ctrl+clicked rows."""
        button = Gtk.MenuButton(label="Group actions…")
        button.set_tooltip_text(
            "Bulk actions: select or unselect rows, or put every shown row "
            "or the Ctrl+clicked rows in or out of relisting at once."
        )
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        entries = (
            ("Select all shown", self._bazar_relist_select_shown),
            ("Unselect all", self._bazar_relist_unselect_all),
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
        )
        for entry in entries:
            if entry is None:
                box.append(Gtk.Separator())
                continue
            label, callback = entry
            item_button = Gtk.Button(label=label)
            item_button.add_css_class("flat")
            item_button.set_halign(Gtk.Align.FILL)
            item_button.connect(
                "clicked",
                lambda _button, cb=callback: (popover.popdown(), cb()),
            )
            box.append(item_button)
        popover.set_child(box)
        button.set_popover(popover)
        return button

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
        content.append(monitors)
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
                    "a current answer; each tab has its own filter. Add and "
                    "edit searches in Items, and choose the monitor with its "
                    "Cost type. A pass where every item times out retries "
                    "after the refresh interval."
                ),
        )
        monitor_frame.set_vexpand(True)
        page.append(monitor_frame)
        return page

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
                "One Monitor Start searches this list together with NosMall."
            )
        else:
            help_title = "NosMall monitor"
            help_text = (
                "Items whose Cost type is NosMall ND, plus items whose cost "
                "is still unknown (price discovery). Profit is the listing "
                "price in gold per ND spent. Import catalog groups with "
                "Import NosMall… in Items and set their ND cost. One Monitor "
                "Start searches this list together with NPC resale."
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

    def _bazar_monitor_switched(
        self, _notebook, _page, index: int
    ) -> None:
        if index < 0 or index >= len(self._bazar_monitor_sources):
            return
        source = self._bazar_monitor_sources[index]
        self.bazar_results_box = self.bazar_results_boxes.get(source)
        self.bazar_results_info = self.bazar_results_infos.get(source)
        self.bazar_only_priced = self.bazar_only_priced_checks.get(source)

    def _build_bazar_items_section(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.set_margin_top(6)
        page.set_margin_bottom(6)
        page.set_margin_start(6)
        page.set_margin_end(6)
        body = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        body.set_vexpand(True)
        body.set_wide_handle(True)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        left.set_size_request(400, -1)
        left.set_vexpand(True)
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
        items_scroll.set_vexpand(True)
        items_box.append(items_scroll)
        item_buttons = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        import_item = Gtk.Button(label="Import NosMall…")
        import_item.set_tooltip_text(
            "Add a NosMall group with its c_blist search packet."
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
        items_frame = self._frame(
            "Items",
            items_box,
            help_text=(
                "The items Monitor searches and Relist offers. Rows show "
                "the real item name for their vnum; hover one for its "
                "configured shortcut label and search vnums. Add items you "
                "already own in Inventory; Import NosMall adds a whole "
                "catalog group with a ready c_blist packet; New and Delete "
                "edit this list. Select a row to edit its label, search, "
                "relist vnum/tab/amount, cost and tracking. Tracking has "
                "two switches: Monitor searches the item, Relist puts it in "
                "the relist set that Relist Start may reprice. The Cost "
                "type chooses the "
                "Monitor view: NosMall ND → NosMall; NPC gold → NPC resale "
                "(the Item.dat price fills in when left at 0); No cost → "
                "NosMall as price discovery."
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
                    "Refresh is the wait between passes. The character name "
                    "used to recognise your own listing is set in Relist."
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

        name_entry = entry("name", "shortcut label", 18)
        name_entry.set_tooltip_text(
            "Your label for this entry; the Monitor and item lists show "
            "the real item name for its vnum."
        )
        copy_name = Gtk.Button(label="Copy")
        copy_name.set_tooltip_text(
            "Copy the configured label."
        )
        copy_name.connect(
            "clicked",
            lambda *_: self._copy_text(
                name_entry.get_text(), "configured label"
            ),
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
            "A captured c_blist overrides Search vnums."
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
        enabled = Gtk.CheckButton(label="Monitor")
        enabled.set_active(True)
        enabled.set_tooltip_text(
            "Monitor searches this item. Off keeps its saved settings but "
            "skips every pass."
        )
        form["enabled"] = enabled
        relist = Gtk.CheckButton(label="Relist")
        relist.set_active(False)
        relist.set_tooltip_text(
            "Include this item in the relist set (Relist Start searches and "
            "may change its listing; needs vnum, tab and a positive floor)."
        )
        form["relist"] = relist
        box.append(
            self._field_row(
                Gtk.Label(label="Tracking"), enabled, relist
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
                "Scroll the spin, drag the bar below it or type any value: "
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
        # No small host-side cap: the wheel and the scroll bar below move the
        # value up as far as the DLL can emulate it. The client's own 59 limit
        # is raised by the DLL.
        speed_adjustment = Gtk.Adjustment(
            value=0,
            lower=0,
            upper=SPEED_BOOST_MAX,
            step_increment=1,
            page_increment=1000000,
            # 1% of the trough, so the scroll bar's thumb stays draggable.
            page_size=SPEED_BOOST_MAX // 100,
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

        # A full-range scroll bar for the same adjustment: dragging it crosses
        # the old million cap (and the byte limit) quickly, while the spin
        # stays the precise input.
        self.speed_boost_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=speed_adjustment,
        )
        self.speed_boost_scale.set_draw_value(False)
        self.speed_boost_scale.set_hexpand(True)
        self.speed_boost_scale.set_tooltip_text(
            "Drag to scroll Speed + far above the old cap; the spin takes an "
            "exact value."
        )
        page.append(self.speed_boost_scale)

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

    # -- minigame -------------------------------------------------------

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
                else:
                    self._set_status(message.payload)
            elif message.kind is Kind.QUIT:
                self._set_status("disconnected")
            else:
                packet = Packet.from_message(message)
                self.engine.feed(packet)
                self._track_packet(packet)
                # Cast speed without the bot: repeat the skill the player
                # pressed when its server cooldown ends.
                self._cast_assist_packet(packet)
                self._bazar_on_packet(packet)
                self._family_on_packet(packet)
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

    def _send(self, text: str) -> bool:
        if self.client is None or not self.client.connected:
            self._set_status("not connected")
            return False
        if text.startswith("u_s "):
            # The DLL echoes our `SEND|u_s` twice (command + send hook); the
            # Cast speed assist must not treat those as key presses.
            parts = text.split(" ", 2)
            try:
                slot = int(parts[1])
            except (IndexError, ValueError):
                slot = 0
            now = time.monotonic()
            self._host_skill_sends.append((slot, now, text))
            self._host_skill_sends.append((slot, now, text))
            del self._host_skill_sends[:-16]
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
        self.assist_packet = None
        self.assist_slot = None
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
        self.assist_packet = None
        self.assist_slot = None
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
        scale = self._cast_speed_scale()
        cooldown = (
            max(info.cooldown, info.cast_time * scale)
            if info is not None
            else 1.0
        )
        self.attack_cooldown_until = now + cooldown
        cast_pause = (info.cast_time * scale) if info is not None else 0.0
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
            # The basic attack is always `u_s 0`. Cast speed above 100% lets
            # it go at the server's own `sr 0` readiness instead of waiting
            # out the longer local timer.
            basic_ready = self._server_skill_ready(
                0, self.last_attack_sent_at
            )
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
                and (now >= self.attack_cooldown_until or basic_ready)
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
                        # Cast speed shortens only the client-side cast
                        # pause: the server's cooldown stays as the floor,
                        # and `sr 0` is the authoritative ready moment.
                        scale = self._cast_speed_scale()
                        if basic_info is not None:
                            cooldown = max(
                                basic_info.cooldown,
                                basic_info.cast_time * scale,
                            )
                        else:
                            # Unknown skill data: keep a safe 1 s floor.
                            cooldown = 1.0
                        self.attack_cooldown_until = now + cooldown
                        # Do not walk inside the skill animation: the server
                        # drops the walk and the bot would skip the path.
                        cast_pause = (
                            basic_info.cast_time * scale
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
                and (now >= self.attack_cooldown_until or basic_ready)
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
