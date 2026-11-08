"""Headless smoke for two windows on two clients.

A child process pretends to be another GUI attached to client 331. The new
window must skip that locked client, auto-attach the free client 332, and
release its own lock on shutdown; the child's lock must survive.

Run through scripts/dev-test-gui.sh (Xvfb).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

import ntpl.gui as gui
from ntpl.clients import (
    ClientInfo,
    Discovery,
    WineContext,
    client_lock_owner,
)


def start_fake_server() -> tuple[int, list[bool]]:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    connected = [False]

    def serve() -> None:
        try:
            connection, _ = listener.accept()
            connected[0] = True
            try:
                connection.sendall(b"STATUS|ready\n")
                time.sleep(15)
            finally:
                connection.close()
        except OSError:
            pass
        finally:
            listener.close()

    threading.Thread(target=serve, daemon=True).start()
    return port, connected


def main() -> int:
    runtime = tempfile.TemporaryDirectory()
    os.environ["NTPL_RUNTIME_DIR"] = runtime.name
    port_a, connected_a = start_fake_server()
    port_b, connected_b = start_fake_server()

    child_code = (
        "import time\n"
        "from ntpl.clients import acquire_client_lock\n"
        "assert acquire_client_lock(331)\n"
        "print('locked', flush=True)\n"
        "time.sleep(60)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        line = child.stdout.readline().strip() if child.stdout else ""
        if line != "locked":
            print(f"MULTI CLIENT SMOKE: FAIL child said {line!r}")
            return 1

        context = WineContext(wine="wine", prefix=Path("/nonexistent"), env={})

        def fake_discover(_proc_root: str = "/proc") -> Discovery:
            return Discovery(
                clients=[
                    ClientInfo(context, 331, "NosTale - 331", port_a),
                    ClientInfo(context, 332, "NosTale - 332", port_b),
                ],
                errors=[],
                injector=None,
                dll=None,
            )

        gui.discover_clients = fake_discover
        app = gui.Application(
            "127.0.0.1",
            port_a + 1,  # must never be dialled
            unique=False,
            select_client=True,
        )
        result = {"ok": False, "detail": "not checked"}
        deadline = time.monotonic() + 10

        def finish() -> bool:
            attached = app.attached_client
            attached_port = app.client.port if app.client is not None else None
            dialog_closed = app._client_dialog is None
            child_owner = client_lock_owner(331)
            own_owner = client_lock_owner(332)
            ok = (
                attached is not None
                and attached.winpid == 332
                and app.client_label is not None
                and "PID 332" in app.client_label
                and attached_port == port_b
                and connected_b[0]
                and not connected_a[0]
                and dialog_closed
                and child_owner == child.pid
                and own_owner == os.getpid()
            )
            result["ok"] = ok
            result["detail"] = (
                f"attached={app.client_label!r} port={attached_port} "
                f"connected_b={connected_b[0]} connected_a={connected_a[0]} "
                f"dialog_closed={dialog_closed} child_lock={child_owner} "
                f"own_lock={own_owner}"
            )
            app.quit()
            return False

        def poll() -> bool:
            if app.attached_client is None:
                if time.monotonic() > deadline:
                    result["detail"] = (
                        f"no client attached (dialog={app._client_dialog is not None})"
                    )
                    app.quit()
                    return False
                return True
            GLib.timeout_add(1500, finish)
            return False

        GLib.timeout_add(100, poll)
        app.run([])

        if result["ok"]:
            # The window must have released its own lock; the child's remains.
            own_after = client_lock_owner(332)
            child_after = client_lock_owner(331)
            if own_after is not None or child_after != child.pid:
                result["ok"] = False
                result["detail"] += (
                    f" after-shutdown own_lock={own_after} "
                    f"child_lock={child_after}"
                )
        print(
            "MULTI CLIENT SMOKE:",
            "PASS" if result["ok"] else "FAIL",
            result["detail"],
        )
        return 0 if result["ok"] else 1
    finally:
        child.kill()
        child.wait()
        runtime.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
