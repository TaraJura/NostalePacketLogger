"""Shared constants, row models and column helpers for the GTK4 window.

These are used by gui.py and by the per-page mixins. Keeping them here lets a
mixin import what it needs without importing the Application class, which
would be a cycle.
"""


import os
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GObject, Gtk, Pango

from .bazar import BazarItem
from .dpsmeter import DpsRow
from .protocol import Packet

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
#: How long a raid/party list (`rdlst`) counts as current. The server sends
#: it every ~10 s while in a raid; after leaving, it stops, so a stale list
#: must not keep the production minigame blocked forever.
MINIGAME_RAID_FRESH_S = 60.0
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


class DpsRowView(GObject.Object):
    """One player row in the Stats table (values pre-formatted)."""

    __gtype_name__ = "NtplDpsRow"

    def __init__(self, row: DpsRow, name: str, now: float) -> None:
        super().__init__()
        self.attacker_id = row.attacker_id
        self.name = name
        self.dps_text = f"{row.dps:,.0f}"
        self.damage_text = f"{row.damage:,}"
        self.hits_text = f"{row.hits:,}"
        self.share_text = f"{row.share * 100:.0f}%"
        self.last_text = f"{max(0.0, now - row.last_at):.1f}s"
        self.total_text = f"{row.total:,}"


class BazarItemRowView(GObject.Object):
    """One row of the Items catalog (all Item.dat rows + config extras).

    The ColumnView virtualizes the widgets; this object only carries the
    values a visible cell needs.
    """

    __gtype_name__ = "NtplBazarItemRow"

    def __init__(
        self,
        vnum: int | None,
        item: BazarItem | None,
        slot,
        display_name: str,
        location: str,
        cost_text: str,
        can_relist: bool,
        tooltip: str,
    ) -> None:
        super().__init__()
        self.vnum = vnum
        self.item = item
        self.slot = slot
        self.display_name = display_name
        self.location = location
        self.cost_text = cost_text
        self.can_relist = can_relist
        self.tooltip = tooltip
        self.monitor = bool(item is not None and item.enabled)
        self.relist = bool(item is not None and item.relist)


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
