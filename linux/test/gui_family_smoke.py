"""Headless smoke test for the Family tab's first feature: member invite.

The live client sends the raw game command `%Familyinvite <name>`; the tab
must send exactly that, pick up the same command when it is typed in game, and
show the server's `infoi2 234 1 <name>` confirmation.

Run through scripts/dev-test-gui.sh (Xvfb).
"""

from __future__ import annotations

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
                b"RECV|cond 1 14881134 0 0 10\n"
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
    os.environ["NTPL_FAMILY_STATE"] = state.name
    server = FakeServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result: dict[str, object] = {"ok": False, "detail": "not checked"}
    deadline = time.time() + 6.0

    def family_index() -> int:
        for index in range(app.notebook.get_n_pages()):
            label = app.notebook.get_tab_label(
                app.notebook.get_nth_page(index)
            )
            if label.get_text() == "Family":
                return index
        return -1

    def phase1() -> bool:
        try:
            if "POS" not in server.received:
                if time.time() < deadline:
                    GLib.timeout_add(100, phase1)
                else:
                    result["detail"] = "GUI never attached"
                    app.quit()
                return False
            index = family_index()
            result["tab"] = index >= 0
            app.notebook.set_current_page(index)
            app.family_name_entry.set_text("Tester")
            app.family_invite_button.emit("clicked")
            GLib.timeout_add(400, phase2)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase1 exception: {exc!r}"
            app.quit()
        return False

    def phase2() -> bool:
        try:
            result["sent"] = "SEND|%Familyinvite Tester" in server.received
            result["pending"] = "waiting for the server" in (
                app.family_status.get_text()
            )
            server.send("RECV|infoi2 234 1 Tester\n")
            GLib.timeout_add(400, phase3)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase2 exception: {exc!r}"
            app.quit()
        return False

    def phase3() -> bool:
        try:
            result["confirmed"] = "invited Tester" in (
                app.family_status.get_text()
            )
            result["confirmed_color"] = app.family_status.has_css_class(
                "bazar-ok"
            )
            buffer = app.bot_log_buffer
            text = buffer.get_text(
                buffer.get_start_iter(), buffer.get_end_iter(), False
            )
            result["logged"] = "family invite confirmed: Tester" in text
            app.family_name_entry.set_text("ListOnly")
            app._on_family_add_to_list()
            result["manual_add"] = "listonly" in app._family_invited
            app._family_remove("ListOnly")
            result["manual_remove"] = "listonly" not in app._family_invited
            app.family_name_entry.set_text("Bad Name")
            app.family_invite_button.emit("clicked")
            GLib.timeout_add(300, phase4)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase3 exception: {exc!r}"
            app.quit()
        return False

    def phase4() -> bool:
        try:
            result["invalid_blocked"] = (
                len(invites(server.received)) == 1
                and "Enter one player name." in app.family_status.get_text()
            )
            # A command typed in game arrives as the client's own SEND packet;
            # the Family tab must show it and its confirmation too.
            server.send("SEND|%Familyinvite Manual\n")
            GLib.timeout_add(300, phase5)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase4 exception: {exc!r}"
            app.quit()
        return False

    def phase5() -> bool:
        try:
            result["manual_seen"] = "Invite sent to Manual" in (
                app.family_status.get_text()
            )
            server.send("RECV|infoi2 234 1 Manual\n")
            GLib.timeout_add(300, phase6)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase5 exception: {exc!r}"
            app.quit()
        return False

    def phase6() -> bool:
        try:
            result["manual_confirmed"] = "invited Manual" in (
                app.family_status.get_text()
            )
            app.family_name_entry.set_text("")
            app.family_invite_button.emit("clicked")
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"phase6 exception: {exc!r}"
        GLib.timeout_add(200, finish)
        return False

    def finish() -> bool:
        result["empty_blocked"] = (
            len(invites(server.received)) == 1
            and "Enter one player name." in app.family_status.get_text()
        )
        app.quit()
        return False

    GLib.timeout_add(300, phase1)
    app.run([])
    server.close()
    try:
        os.unlink(state.name)
    except OSError:
        pass
    os.environ.pop("NTPL_FAMILY_STATE", None)

    result["ok"] = all(
        result.get(key)
        for key in (
            "tab",
            "sent",
            "pending",
            "confirmed",
            "confirmed_color",
            "logged",
            "manual_add",
            "manual_remove",
            "invalid_blocked",
            "manual_seen",
            "manual_confirmed",
            "empty_blocked",
        )
    )
    result["detail"] = " ".join(
        f"{key}={result.get(key)}"
        for key in (
            "tab",
            "sent",
            "pending",
            "confirmed",
            "confirmed_color",
            "logged",
            "manual_add",
            "manual_remove",
            "invalid_blocked",
            "manual_seen",
            "manual_confirmed",
            "empty_blocked",
        )
    )
    print("FAMILY SMOKE:", "PASS" if result["ok"] else "FAIL", result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
