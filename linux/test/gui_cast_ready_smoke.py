"""Headless smoke test: Cast speed chains casts at the server's pace.

The fake server teaches the client skill list (`ski`) and answers the bot's
self-cast Mana Shield (`u_s 3 1 <charId>`) with `su` and `sr 3`. Mana Shield
has a 60 s Skill.dat cooldown and the rule is set to 30 s, so without the hack
the bot must wait a minute for a second cast.

Three phases in one run:

1. Cast speed OFF: the first cast goes out, `sr 3` arrives, and no second
   cast follows within 1.5 s (the local 60 s gate holds).
2. Cast speed ON: the already-received `sr 3` is newer than the last cast,
   so the next tick casts again immediately (0.05 s in practice), proving
   the server's readiness replaces the local cooldown.
3. The server answers the second cast only after a 0.6 s cast time. The bot
   must not send a third cast during that window (no refused spam) and must
   send it immediately after the answer: the gap is the server's own cast
   time, the minimum possible.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading
import time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.botconfig import SpellRule
from ntpl.gui import Application

SELF_ID = 14881134
SKILL = 869          # Mana Shield: cast 1 s, cooldown 60 s, 300 MP, self
SKILL_SLOT = 3       # position in the fake `ski` list below
CAST_PACKET = f"SEND|u_s {SKILL_SLOT} 1 {SELF_ID}"
SERVER_CAST_TIME = 0.6


class FakeServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.connection = None
        self.stop = threading.Event()
        self.cast_count = 0
        self.answer_lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def _answer(self) -> None:
        if self.stop.is_set() or self.connection is None:
            return
        try:
            self.connection.sendall(
                (
                    f"RECV|su 1 {SELF_ID} 1 {SELF_ID} {SKILL} "
                    "600 24 4314 0 0 1 100 0 2 0 400 400\n"
                    f"RECV|sr {SKILL_SLOT}\n"
                ).encode()
            )
        except OSError:
            pass

    def _run(self) -> None:
        try:
            self.connection, _ = self.listener.accept()
        except OSError:
            return
        try:
            self.connection.sendall(
                b"STATUS|ready\n"
                b"RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
                b"RECV|stat 100 100 400 400 0 0\n"
                b"RECV|rest 1 14881134 0\n"
                b"RECV|ski 0 866 866 866 867 868 869 870 871 872 873 "
                b"874 875 876\n"
            )
        except OSError:
            return
        buffer = b""
        while not self.stop.is_set():
            try:
                chunk = self.connection.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                text = line.decode("utf-8", "replace")
                self.received.append(text)
                if text == "POS":
                    try:
                        self.connection.sendall(
                            f"STATUS|POS 25 117 {SELF_ID}\n".encode()
                        )
                    except OSError:
                        return
                elif text == CAST_PACKET:
                    with self.answer_lock:
                        self.cast_count += 1
                        number = self.cast_count
                    if number == 2:
                        # The second cast is answered only after the server's
                        # own cast time; the bot must wait for it.
                        threading.Timer(
                            SERVER_CAST_TIME, self._answer
                        ).start()
                    else:
                        self._answer()

    def close(self) -> None:
        self.stop.set()
        for sock in (self.connection, self.listener):
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass


def main() -> int:
    server = FakeServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}
    state: dict[str, float] = {}

    def casts() -> int:
        return sum(1 for line in server.received if line == CAST_PACKET)

    def cast_times() -> list[float]:
        # The fake server sees packets, not monotonic times; approximate the
        # send times at the moment they are observed by the test phases.
        return state.setdefault("cast_observed", [])

    def note_casts() -> None:
        observed = cast_times()
        while len(observed) < casts():
            observed.append(time.monotonic())

    def start_bot() -> bool:
        try:
            app.auto_attack_toggle.set_active(True)
            app.retaliate_toggle.set_active(False)
            app.recover_toggle.set_active(False)
            app.recover_mp_toggle.set_active(False)
            app.cast_boost_switch.set_active(False)
            app._add_spell_rule(
                SpellRule(
                    enabled=True,
                    spell=SKILL,
                    subject="always",
                    comparator="below",
                    threshold=50,
                    cooldown=30,
                    target="self",
                )
            )
            result["slot"] = app._skill_send_id(SKILL) == SKILL_SLOT
            app._start_bot()
        except Exception as exc:  # noqa: BLE001 - report, do not crash GTK
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
            return False
        GLib.timeout_add(100, wait_first)
        return False

    def wait_first() -> bool:
        note_casts()
        if casts() < 1:
            if time.monotonic() - state.get("started", time.monotonic()) > 4.0:
                result["detail"] = "bot never cast the spell"
                app.quit()
                return False
            return True
        state["off_checked_at"] = time.monotonic() + 1.5
        GLib.timeout_add(100, check_off_phase)
        return False

    def check_off_phase() -> bool:
        note_casts()
        if time.monotonic() < state["off_checked_at"]:
            return True
        result["off_casts"] = casts()
        result["off_one_only"] = result["off_casts"] == 1
        # Turn the hack on. The sr from the first cast is already in the
        # world state; the next tick must use it.
        state["switch_at"] = time.monotonic()
        app.cast_boost_switch.set_active(True)
        GLib.timeout_add(50, wait_second)
        return False

    def wait_second() -> bool:
        note_casts()
        if casts() >= 2:
            result["second_latency"] = (
                time.monotonic() - state["switch_at"]
            )
            result["second_fast"] = result["second_latency"] <= 1.5
            state["second_at"] = cast_times()[1]
            # The fake server answers the second cast only after its 0.6 s
            # cast time. Watch for the third cast and its timing.
            GLib.timeout_add(30, wait_third)
            return False
        if time.monotonic() - state["switch_at"] > 3.0:
            result["detail"] = (
                "no second cast after Cast speed ON, sr readiness was "
                f"not used; lines={server.received[-8:]}"
            )
            app.quit()
            return False
        return True

    def wait_third() -> bool:
        note_casts()
        if casts() >= 3:
            result["third_gap"] = cast_times()[2] - state["second_at"]
            # Never before the server's answer (0.6 s), and immediately after
            # it (no extra rule/cast delay).
            result["third_after_answer"] = (
                SERVER_CAST_TIME - 0.15 <= result["third_gap"] <= 1.3
            )
            result["no_zeroing"] = "CAST|1" not in server.received
            result["ok"] = bool(
                result.get("slot")
                and result.get("off_one_only")
                and result.get("second_fast")
                and result.get("third_after_answer")
                and result.get("no_zeroing")
            )
            observed = cast_times()
            result["detail"] = (
                f"slot={result.get('slot')} "
                f"off_casts={result.get('off_casts')} "
                f"second_latency={result.get('second_latency', -1):.2f}s "
                f"third_gap={result.get('third_gap', -1):.2f}s "
                f"no_zeroing={result.get('no_zeroing')} "
                f"casts={observed} lines={server.received[-8:]}"
            )
            app.quit()
            return False
        if time.monotonic() - state["second_at"] > 3.0:
            result["detail"] = (
                "no third cast after the server answered the second "
                f"(busy gate stuck); gap={time.monotonic() - state['second_at']:.2f}s "
                f"lines={server.received[-8:]}"
            )
            app.quit()
            return False
        return True

    def start_when_ready() -> bool:
        if not app.spells_loaded:
            return True
        state["started"] = time.monotonic()
        return start_bot()

    GLib.timeout_add(1500, start_when_ready)
    app.run([])
    server.close()

    if not result.get("ok"):
        result.setdefault(
            "detail",
            f"casts={casts()} lines={server.received[-8:]}",
        )
    print("CAST READY SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
