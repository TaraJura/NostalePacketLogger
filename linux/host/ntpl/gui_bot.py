"""The bot loop, follow, mirror and packet tracking.

Extracted from gui.py; every method still runs on the Application
instance, so it uses self state exactly like the core methods.
"""


from .client import ConnectionLost
from .engine import Entity
from .engine import GroundItem
from .guicommon import ATTACK_SEARCH_CELLS
from .guicommon import BOT_INTERVAL_MS
from .guicommon import CANCEL_PAUSE_S
from .guicommon import CANCEL_PAUSE_TRIGGER
from .guicommon import CANCEL_WINDOW_S
from .guicommon import CAST_PENDING_TTL_S
from .guicommon import DEFAULT_SKILL_TEMPLATE
from .guicommon import FINISH_KILL_GRACE_S
from .guicommon import FOLLOW_LOOT_RADIUS
from .guicommon import FOLLOW_STOP_CELLS
from .guicommon import FOLLOW_TARGET_TTL_S
from .guicommon import HIT_TARGET_GRACE_S
from .guicommon import LOOT_APPROACH_CELLS
from .guicommon import MAX_CANCEL_STREAK
from .guicommon import MAX_MOVE_LEG_CELLS
from .guicommon import MAX_REJECTED_WALKS
from .guicommon import MIRROR_BUS_GRACE_S
from .guicommon import MIRROR_CAST_MIN_INTERVAL_S
from .guicommon import MIRROR_CAST_QUEUE_MAX
from .guicommon import MIRROR_CAST_TTL_S
from .guicommon import MIRROR_DEDUPE_S
from .guicommon import MIRROR_MOVE_TTL_S
from .guicommon import MOVE_PREFIX
from .guicommon import PICKUP_COOLDOWN_S
from .guicommon import PICKUP_RANGE_CELLS
from .guicommon import REST_RETRY_S
from .guicommon import TARGET_BLOCK_S
from .guicommon import TARGET_PREFIX
from .guicommon import TRAVEL_HANDOFF_CELLS
from .guicommon import TRAVEL_MIN_LEG_S
from .mapdata import astar
from .mirror import KIND_MOVE
from .mirror import KIND_SPELL
from .mirror import MirrorWriter
from .protocol import Kind
from .protocol import Packet
from gi.repository import GLib
from pathlib import Path
import math
import os
import time

class BotPageMixin:
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
