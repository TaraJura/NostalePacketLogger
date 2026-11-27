"""Headless smoke test for the Pet Trainer tamer bot mode.

The fake server spawns two Chickens (vnum 333) next to the character. The
tamer must target one, hit it with the card's basic attack (slot 0) until its
HP is at or below the configured limit, then use Tame from the chosen slot
(1), and repeat for the second chicken. The first cast is answered with the
server's `msgi 0 2614` (Taming successful) plus `ctl`/`sc_p` (the new pet),
the second with `msgi 0 1887` (max pet slots), which must stop the bot with a
clear message.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application

CHAR_ID = 14881134
CHICKEN_A = 3039
CHICKEN_B = 3040
PET_A = 1452945


class TameServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.connection = None
        self.stop = threading.Event()
        # Which chicken the next tame answer belongs to.
        self.tamed: set[int] = set()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.connection, _ = self.listener.accept()
            self.connection.sendall(
                (
                    "STATUS|ready\n"
                    f"RECV|at {CHAR_ID} 85 25 117 2 0 0 0 0 0 0\n"
                    "RECV|stat 1000 1000 500 500 0 0\n"
                    f"RECV|in 3 333 {CHICKEN_A} 26 117 2 100 100 0 0 0 "
                    "-1 0 0 -1 - 0 -1 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
                    f"RECV|st 3 {CHICKEN_A} 1 0 100 100 100 10 100 10 0\n"
                    f"RECV|in 3 333 {CHICKEN_B} 27 118 2 100 100 0 0 0 "
                    "-1 0 0 -1 - 0 -1 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
                    f"RECV|st 3 {CHICKEN_B} 1 0 100 100 100 10 100 10 0\n"
                    "RECV|p_clear\n"
                    "RECV|sc_p 0 806 822161 1 3000 2 0 0 80 158 60 24 110 "
                    "0 20 43 24 43 20 2 60 80 50 50 477 481\n"
                ).encode()
            )
        except OSError:
            return
        buffer = b""
        while not self.stop.is_set():
            try:
                chunk = self.connection.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                text = line.decode("utf-8", "replace")
                self.received.append(text)
                self._answer(text)

    def _answer(self, text: str) -> None:
        # TARGET|id is the DLL command, not a packet; the client would send
        # the `st` itself. The smoke answers the target selection directly so
        # the HP is known before the first Whip.
        if text.startswith("TARGET|"):
            try:
                entity_id = int(text.split("|", 1)[1])
            except ValueError:
                return
            self.send(
                f"RECV|st 3 {entity_id} 1 0 100 100 100 10 100 10 0\n"
            )
            return
        if not text.startswith("SEND|u_s "):
            return
        parts = text.split()
        try:
            slot = int(parts[1])
            entity_id = int(parts[3])
        except (IndexError, ValueError):
            return
        if slot == 0:
            # Whip: leave 20% HP — at or below the default 25% limit.
            self.send(
                f"RECV|st 3 {entity_id} 1 0 1 100 20 10 100 10 0\n"
            )
        elif slot == 1 and entity_id not in self.tamed:
            self.tamed.add(entity_id)
            if entity_id == CHICKEN_A:
                self.send(
                    f"RECV|ctl 2 {PET_A} 3 0\n"
                    "RECV|msgi 0 2614 0 0 0 0 0\n"
                    f"RECV|out 3 {entity_id}\n"
                    "RECV|p_clear\n"
                    f"RECV|sc_p 4 333 {PET_A} 1 3000 0 0 0 32 40 27 4\n"
                )
            else:
                self.send(
                    "RECV|msgi 0 1887 0 0 0 0 0\n"
                )

    def send(self, text: str) -> None:
        try:
            self.connection.sendall(text.encode())
        except OSError:
            pass

    def close(self) -> None:
        self.stop.set()
        for sock in (self.connection, self.listener):
            if sock is None:
                continue
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


def main() -> int:
    log = tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="latin1"
    )
    log.write("")  # never seed from the running client's own log
    log.close()
    server = TameServer()
    app = Application(
        "127.0.0.1", server.port, unique=False, log_file=log.name
    )
    result: dict[str, object] = {"ok": False, "detail": "not checked"}
    state = {"budget": 60}

    def u_s_packets() -> list[str]:
        return [
            line for line in server.received
            if line.startswith("SEND|u_s ")
        ]

    def wait_ready() -> bool:
        state["budget"] -= 1
        if app.engine.world.position is None:
            if state["budget"] <= 0:
                result["detail"] = "the position never reached the engine"
                app.quit()
                return False
            return True
        if app.client is None or not app.client.connected:
            return True
        if app.tame_toggle is None:
            result["detail"] = "the Pet tamer frame was not built"
            app.quit()
            return False
        if len(app.engine.world.entities) < 2:
            if state["budget"] <= 0:
                result["detail"] = "the chickens never spawned"
                app.quit()
                return False
            return True
        app.notebook.set_current_page(1)  # Map / Bot
        app.tame_toggle.set_active(True)
        # Test-only shortcut: the real Tame cooldown is 15 s.
        app._tame_cooldown = lambda slot: 1.0
        app._start_bot()
        GLib.timeout_add(1600, check_first)
        GLib.timeout_add(7000, check_final)
        return False

    def check_first() -> bool:
        try:
            packets = u_s_packets()
            result["first_packets"] = packets
            result["first"] = (
                "SEND|u_s 0 3 3039" in packets
                and "SEND|u_s 1 3 3039" in packets
                and app.tame_done >= 1
                and app.engine.world.pets.get(4) == 333
            )
        except Exception as exc:  # noqa: BLE001
            result["first"] = False
            result["detail"] = f"first exception: {exc!r}"
        return False

    def check_final() -> bool:
        try:
            packets = u_s_packets()
            status = app.status.get_text() if app.status is not None else ""
            result["packets"] = packets
            result["status"] = status
            result["pets"] = dict(app.engine.world.pets)
            result["done"] = app.tame_done
            result["running"] = app.bot_running
            result["final"] = (
                "SEND|u_s 0 3 3040" in packets
                and "SEND|u_s 1 3 3040" in packets
                and app.tame_done == 1
                and app.bot_running is False
                and "pet slots are full" in status
            )
            result["ok"] = bool(result.get("first") and result["final"])
            result["detail"] = (
                f"first={result.get('first')} "
                f"final={result['final']} "
                f"packets={packets} "
                f"pets={result['pets']} "
                f"done={result['done']} "
                f"running={result['running']} "
                f"status={status!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["detail"] = f"final exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(200, wait_ready)
    app.run([])
    server.close()
    try:
        os.unlink(log.name)
    except OSError:
        pass
    print(
        "TAME SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
