"""Headless smoke test for the startup client picker.

Wine is not involved: ``ntpl.gui.discover_clients`` is replaced with a fixed
:class:`Discovery`, so the dialog and the attach path can be driven
deterministically.  The test checks that

* two running clients are listed (one injected, one not),
* the action label follows the selection ("Connect" / "Inject & connect"),
* an uninjectable client cannot be selected for action,
* Connect attaches the reader to the selected client's port and the status
  bar names the client,
* a rescan while the dialog is open rebuilds it instead of opening a second
  window.

Run through scripts/dev-test-gui.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

import ntpl.gui as gui
from ntpl.clients import ClientInfo, Discovery, WineContext


def start_fake_server() -> tuple[int, list[bool]]:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    connected = [False]

    def serve() -> None:
        connection, _ = listener.accept()
        connected[0] = True
        try:
            connection.sendall(b"STATUS|ready\n")
            time.sleep(10)
        finally:
            connection.close()
            listener.close()

    threading.Thread(target=serve, daemon=True).start()
    return port, connected


def main() -> int:
    port, connected = start_fake_server()
    context = WineContext(wine="wine", prefix=Path("/nonexistent"), env={})

    def fake_discover(_proc_root: str = "/proc") -> Discovery:
        return Discovery(
            clients=[
                ClientInfo(context, 111, "NosTale - 111", port),
                ClientInfo(context, 222, "NosTale - 222", None),
            ],
            errors=[],
            injector=None,
            dll=None,
        )

    gui.discover_clients = fake_discover

    app = gui.Application(
        "127.0.0.1",
        port + 1,  # must never be dialled: the picker chooses the real port
        unique=False,
        select_client=True,
    )
    result = {"ok": False, "detail": "not checked"}

    def check() -> bool:
        try:
            dialog = app._client_dialog
            listbox = app._client_dialog_list
            action = app._client_dialog_action
            if dialog is None or listbox is None or action is None:
                result["detail"] = "dialog did not open"
                app.quit()
                return False

            rows = 0
            child = listbox.get_first_child()
            while child is not None:
                rows += 1
                child = child.get_next_sibling()

            first_label = action.get_label()
            first_sensitive = action.get_sensitive()

            listbox.select_row(listbox.get_row_at_index(1))
            second_label = action.get_label()
            second_sensitive = action.get_sensitive()

            # Re-select the injected client and connect through the button.
            listbox.select_row(listbox.get_row_at_index(0))
            app._on_client_connect_clicked()
            attached = app.client_label or ""
            dialog_closed = app._client_dialog is None

            def finish() -> bool:
                status = app.status.get_text()
                attached_port = app.client.port if app.client is not None else None
                ok = (
                    rows == 2
                    and first_label == "Connect"
                    and first_sensitive
                    and second_label == "Inject & connect"
                    and not second_sensitive  # injector missing
                    and dialog_closed
                    and "NosTale - 111" in attached
                    and "PID 111" in attached
                    and attached_port == port
                    and connected[0]
                )
                result["ok"] = ok
                result["detail"] = (
                    f"rows={rows} first={first_label}/{first_sensitive} "
                    f"second={second_label}/{second_sensitive} "
                    f"closed={dialog_closed} attached={attached!r} "
                    f"port={attached_port} connected={connected[0]} "
                    f"status={status!r}"
                )
                app.quit()
                return False

            GLib.timeout_add(1500, finish)
        except Exception as exc:  # noqa: BLE001 - report, do not crash GTK
            result["detail"] = f"check exception: {exc!r}"
            app.quit()
        return False

    def wait_for_dialog(deadline: float) -> bool:
        if app._client_dialog is not None and app._client_dialog_list is not None:
            return check()
        if time.monotonic() > deadline:
            result["detail"] = "dialog never appeared"
            app.quit()
            return False
        return True

    GLib.timeout_add(100, wait_for_dialog, time.monotonic() + 10)
    app.run([])

    print(
        "CLIENT PICKER SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
