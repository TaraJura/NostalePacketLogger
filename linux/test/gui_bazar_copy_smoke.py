"""Headless smoke test for copying a NosBazar item name.

The NosBazar items list, the results table and the item editor expose the
name through the app action ``copy-item-name`` / a Copy button.  This smoke
activates the action against a real GTK clipboard and reads the text back.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib

from ntpl.gui import Application

ITEM_NAME = "Wings of Friendship"


def start_fake_server() -> int:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve() -> None:
        try:
            connection, _ = listener.accept()
        except OSError:
            return
        connection.sendall(b"STATUS|ready\n")
        time.sleep(12)
        connection.close()
        listener.close()

    threading.Thread(target=serve, daemon=True).start()
    return port


def main() -> int:
    port = start_fake_server()
    app = Application("127.0.0.1", port, unique=False)
    result = {"ok": False, "detail": "not checked"}
    state = {"done": False}

    def timeout() -> bool:
        if not state["done"]:
            state["done"] = True
            result["detail"] = "clipboard read timed out"
            app.quit()
        return False

    def check() -> bool:
        display = Gdk.Display.get_default()
        clipboard = display.get_clipboard() if display is not None else None
        action = app.lookup_action("copy-item-name")
        if clipboard is None or action is None:
            result["detail"] = (
                f"clipboard={clipboard is not None} "
                f"action={action is not None}"
            )
            app.quit()
            return False
        try:
            app.activate_action(
                "copy-item-name", GLib.Variant("s", ITEM_NAME)
            )
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"action exception: {exc!r}"
            app.quit()
            return False

        def done(source, res) -> None:
            if state["done"]:
                return
            state["done"] = True
            try:
                text = source.read_text_finish(res)
            except Exception as exc:  # noqa: BLE001
                text = f"<error {exc!r}>"
            result["ok"] = text == ITEM_NAME
            result["detail"] = (
                f"action=True clipboard={text!r} "
                f"status={app.status.get_text()!r}"
            )
            app.quit()

        clipboard.read_text_async(None, done)
        GLib.timeout_add(3000, timeout)
        return False

    GLib.timeout_add(900, check)
    app.run([])
    print("BAZAR COPY SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
