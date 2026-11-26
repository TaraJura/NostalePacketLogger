"""Headless smoke test for Family auto-invite.

The map has three players: one with no family, one in a family and one whose
family state was never sent. Auto-invite must pick only the first, send one
invite, remember it in the persisted list, and never retry it.

Run through scripts/dev-test-gui.sh (Xvfb).
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application


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
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.connection, _ = self.listener.accept()
        except OSError:
            return
        try:
            self.connection.sendall(
                b"STATUS|ready\n"
                b"RECV|at 14881134 20 25 117 0 0 0 0 0 0 0\n"
                b"RECV|in 1 NoFam - 5001 25 118 2 0 1 0 9 2 "
                b"-1.0.0.-1.-1.885.-1.-1.-1.-1 "
                b"100 100 0 -1 0 2 0 0 0 1000009 75 75 -1 - -2 0 0 0 0 81 "
                b"0 0|0|0 0 0 100 0 0\n"
                b"RECV|in 1 NoFairy - 5004 25 121 2 0 1 0 9 2 "
                b"-1.0.0.-1.-1.-1.-1.-1.-1.-1 "
                b"100 100 0 -1 0 2 0 0 0 1000009 75 75 -1 - -2 0 0 0 0 81 "
                b"0 0|0|0 0 0 100 0 0\n"
                b"RECV|in 1 FamGuy - 5002 25 119 2 0 1 0 9 2 -1.0 "
                b"100 100 0 -1 0 2 0 0 0 1000009 75 75 70.918 SomeFam 14 0 "
                b"0 0 0 85 1 0|0|0 0 0 100 10 0\n"
                b"RECV|in 1 UnknownFam - 5003 25 120 2 0 1 0 9 2\n"
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
                if text == "POS":
                    try:
                        self.connection.sendall(
                            b"STATUS|POS 25 117 14881134 0\n"
                        )
                    except OSError:
                        return
                elif text == "SEND|%Familyinvite NoFam":
                    try:
                        self.connection.sendall(
                            b"RECV|infoi2 234 1 NoFam\n"
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


def invites(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("SEND|%Familyinvite")]


def main() -> int:
    state = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    )
    state.close()
    log = tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="latin1"
    )
    log.close()
    os.environ["NTPL_FAMILY_STATE"] = state.name

    server = FakeServer()
    app = Application(
        "127.0.0.1", server.port, unique=False, log_file=log.name
    )
    result: dict[str, object] = {"ok": False, "detail": "not checked"}
    deadline = time.time() + 6.0

    def phase1() -> bool:
        try:
            if "POS" not in server.received:
                if time.time() < deadline:
                    GLib.timeout_add(100, phase1)
                else:
                    result["detail"] = "GUI never attached"
                    app.quit()
                return False
            app.family_invite_interval = 0.2
            app.family_confirm_timeout = 2.0
            app.family_only_fairy.set_active(True)
            app.family_auto_toggle.set_active(True)
            GLib.timeout_add(1200, phase2)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase1 exception: {exc!r}"
            app.quit()
        return False

    def phase2() -> bool:
        try:
            chosen = invites(server.received)
            result["one_invite"] = chosen == ["SEND|%Familyinvite NoFam"]
            result["no_family_player_skipped"] = not any(
                "FamGuy" in line for line in chosen
            )
            result["no_fairy_skipped"] = not any(
                "NoFairy" in line for line in chosen
            )
            result["unknown_family_skipped"] = not any(
                "UnknownFam" in line for line in chosen
            )
            result["remembered"] = (
                "nofam" in app._family_invited
                and app.family_invited_info.get_text()
                == "Invited players: 1"
            )
            result["list_visible"] = app.family_invited_scroll.get_visible()
            result["confirmed"] = "invited NoFam" in (
                app.family_status.get_text()
            )
            result["timer"] = "Next invite in" in (
                app.family_timer_label.get_text()
            )
            try:
                data = json.loads(
                    open(state.name, encoding="utf-8").read()
                )
            except (OSError, ValueError):
                data = None
            result["saved"] = (
                isinstance(data, dict)
                and data.get("invited") == ["NoFam"]
            )
            GLib.timeout_add(1200, phase3)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase2 exception: {exc!r}"
            app.quit()
        return False

    def phase3() -> bool:
        try:
            result["no_retry"] = (
                invites(server.received)
                == ["SEND|%Familyinvite NoFam"]
            )
            app.family_auto_toggle.set_active(False)
            result["stopped"] = app._family_auto_timer is None
            # A server cooldown rejection must not burn the name; it starts
            # the timer and leaves the name retryable.
            app._family_next_invite_at = 0.0
            app.family_name_entry.set_text("Blocked")
            app.family_invite_button.emit("clicked")
            GLib.timeout_add(300, phase4)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase3 exception: {exc!r}"
            app.quit()
        return False

    def phase4() -> bool:
        try:
            result["blocked_sent"] = (
                "SEND|%Familyinvite Blocked" in server.received
            )
            server.send("RECV|infoi 2359 0 4 3\n")
            GLib.timeout_add(300, phase5)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase4 exception: {exc!r}"
            app.quit()
        return False

    def phase5() -> bool:
        try:
            result["blocked_retryable"] = (
                "blocked" not in app._family_invited
            )
            result["cooldown_started"] = (
                app._family_cooldown_remaining() > 0
            )
            result["cooldown_status"] = "cooldown" in (
                app.family_status.get_text().lower()
            )
            # A silent target is skipped, not fatal: auto-invite must keep
            # running (the 2026-11-20 live bug stopped after every silent name).
            app.family_auto_toggle.set_active(True)
            app._family_next_invite_at = time.monotonic() + 100.0
            app.family_confirm_timeout = 1.0
            app._family_pending = ("Quiet", time.monotonic() - 10.0)
            app._family_auto_tick()
            result["no_reply_skipped"] = "quiet" in app._family_no_reply
            result["auto_continued"] = (
                app.family_auto_toggle.get_active()
            )
            app.family_auto_toggle.set_active(False)
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["detail"] = f"phase5 exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(300, phase1)
    app.run([])
    server.close()
    for path in (state.name, log.name):
        try:
            os.unlink(path)
        except OSError:
            pass
    os.environ.pop("NTPL_FAMILY_STATE", None)

    result["ok"] = all(
        result.get(key)
        for key in (
            "one_invite",
            "no_family_player_skipped",
            "no_fairy_skipped",
            "unknown_family_skipped",
            "remembered",
            "list_visible",
            "confirmed",
            "timer",
            "saved",
            "no_retry",
            "stopped",
            "blocked_sent",
            "blocked_retryable",
            "cooldown_started",
            "cooldown_status",
            "no_reply_skipped",
            "auto_continued",
        )
    )
    result["detail"] = " ".join(
        f"{key}={result.get(key)}"
        for key in (
            "one_invite",
            "no_family_player_skipped",
            "no_fairy_skipped",
            "unknown_family_skipped",
            "remembered",
            "list_visible",
            "confirmed",
            "timer",
            "saved",
            "no_retry",
            "stopped",
            "blocked_sent",
            "blocked_retryable",
            "cooldown_started",
            "cooldown_status",
            "no_reply_skipped",
            "auto_continued",
        )
    )
    print(
        "FAMILY AUTO SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
