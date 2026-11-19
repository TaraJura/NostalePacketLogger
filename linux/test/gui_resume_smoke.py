"""Headless smoke test: Stop/Start resumes a route where the character is.

The route is a start point far to the north and two waypoints to the east and
south-east. Start must join the route from the character's position — never
walk to the stored route start — and after the bot is stopped and the
character is moved (as a player would), Start must continue at the nearest
leg instead of re-treading the first waypoint.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application

START = (18, 117)      # stored Draw-path start, must never be walked to
WAYPOINTS = [(30, 117), (28, 110), (24, 117)]
RESUME_AT = (29, 114)  # where the player "manually" moved the character


class ResumeServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.x, self.y = 25, 117
        self.connection = None
        self.stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.connection, _ = self.listener.accept()
        except OSError:
            return
        self.connection.sendall(
            b"STATUS|ready\n"
            b"RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
            b"RECV|stat 100 100 100 100 0 0\n"
            b"RECV|rest 1 14881134 0\n"
        )
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
                            f"STATUS|POS {self.x} {self.y} 14881134 0\n".encode()
                        )
                    except OSError:
                        return
                elif text.startswith("MOVE|"):
                    parts = text.split()
                    if len(parts) >= 2:
                        try:
                            self.x = int(parts[0][5:])
                            self.y = int(parts[1])
                        except ValueError:
                            continue
                        try:
                            self.connection.sendall(
                                f"SEND|walk {self.x} {self.y} 0 10\n".encode()
                            )
                        except OSError:
                            return

    def send(self, text: str) -> None:
        try:
            self.connection.sendall(text.encode())
        except OSError:
            pass

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


def moves_between(lines: list[str]) -> list[tuple[int, int]]:
    result = []
    for line in lines:
        if not line.startswith("MOVE|"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            try:
                result.append((int(parts[0][5:]), int(parts[1])))
            except ValueError:
                continue
    return result


def main() -> int:
    server = ResumeServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}
    phase = {"resume_index": None}

    def start() -> bool:
        try:
            # Regression: clicking the map on a route whose start point is
            # empty must add a waypoint, not wipe the drawn path.
            app.draw_path_toggle.set_active(True)
            app.path_start = None
            app.waypoints = [(30, 117)]
            app._handle_map_cell((29, 117))
            result["click_kept"] = app.waypoints == [(30, 117), (29, 117)]
            app.waypoints = []
            app.draw_path_toggle.set_active(False)

            app.path_start = START
            app.waypoints = list(WAYPOINTS)
            app.loop_toggle.set_active(False)
            app.auto_attack_toggle.set_active(False)
            app.retaliate_toggle.set_active(False)
            app.pickup_toggle.set_active(False)
            app.recover_toggle.set_active(False)
            app.recover_mp_toggle.set_active(False)
            app._start_bot()
            GLib.timeout_add(1200, manual_walk)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def manual_walk() -> bool:
        # The player clicks somewhere else in the game: the server reports
        # the character on a cell the bot did not command (`tp`). The bot
        # must re-plan and keep running without Stop/Start.
        server.x, server.y = RESUME_AT
        server.send("RECV|tp 1 14881134 29 114 0\n")
        GLib.timeout_add(700, interrupt)
        return False

    def interrupt() -> bool:
        try:
            result["auto_continued"] = app.bot_running
            log_text = ""
            if app.bot_log_buffer is not None:
                buffer = app.bot_log_buffer
                log_text = buffer.get_text(
                    buffer.get_start_iter(), buffer.get_end_iter(), False
                )
            result["interrupted"] = "walk interrupted" in log_text
            result["interrupt_count"] = log_text.count("walk interrupted")
            app._stop_bot()
            # The player takes over and walks somewhere else.
            server.x, server.y = RESUME_AT
            GLib.timeout_add(1200, resume)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"interrupt exception: {exc!r}"
            app.quit()
        return False

    def resume() -> bool:
        try:
            phase["resume_index"] = len(server.received)
            app._start_bot()
            GLib.timeout_add(4000, check)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"resume exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            resume_index = phase["resume_index"]
            before = moves_between(server.received[:resume_index])
            after = moves_between(server.received[resume_index:])
            no_start = all(move != START for move in before + after)
            # The route only goes east/north from the character; a walk to
            # the stored start (west) would show x < 24.
            first_ok = bool(before) and all(move[0] >= 24 for move in before)
            # Resume: continue to the second waypoint, not back to the first.
            first_after = after[0] if after else None
            if first_after is not None:
                to_second_before = max(
                    abs(RESUME_AT[0] - WAYPOINTS[1][0]),
                    abs(RESUME_AT[1] - WAYPOINTS[1][1]),
                )
                to_second_after = max(
                    abs(first_after[0] - WAYPOINTS[1][0]),
                    abs(first_after[1] - WAYPOINTS[1][1]),
                )
                to_first_before = max(
                    abs(RESUME_AT[0] - WAYPOINTS[0][0]),
                    abs(RESUME_AT[1] - WAYPOINTS[0][1]),
                )
                to_first_after = max(
                    abs(first_after[0] - WAYPOINTS[0][0]),
                    abs(first_after[1] - WAYPOINTS[0][1]),
                )
                continued = (
                    to_second_after < to_second_before
                    and to_first_after > to_first_before
                )
            else:
                continued = False
            click_kept = bool(result.get("click_kept"))
            auto_continued = bool(result.get("auto_continued"))
            interrupted = bool(result.get("interrupted"))
            interrupt_count = int(result.get("interrupt_count", 0))
            result["ok"] = (
                no_start and first_ok and bool(after) and continued
                and click_kept and auto_continued and interrupted
                and interrupt_count == 1
            )
            result["detail"] = (
                f"before={before} after={after} no_start={no_start} "
                f"first_ok={first_ok} continued={continued} "
                f"click_kept={click_kept} auto_continued={auto_continued} "
                f"interrupted={interrupted} count={interrupt_count} "
                f"status={app.status.get_text()!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    def start_when_map_loaded() -> bool:
        if app.map_grid is None:
            return True
        return start()

    GLib.timeout_add(1200, start_when_map_loaded)
    app.run([])
    server.close()
    print("RESUME SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
