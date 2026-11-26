"""Headless Stats tab smoke: the player DPS meter.

The fake server spawns two players, then broadcasts ``su`` hits for the self
character and for another player, plus a partner hit and a zero-damage buff
that must be ignored. The Stats page must list both players, sort by DPS,
resolve their names and reset cleanly.

Run through scripts/dev-test-bot.sh (Xvfb).
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

SELF_ID = 7001
GUY_ID = 1102


def _su(attacker: int, damage: int, attacker_type: int = 1) -> bytes:
    return (
        f"RECV|su {attacker_type} {attacker} 3 500 999 6 11 3801 "
        f"0 0 1 50 {damage} 0 0 4000 10000\n"
    ).encode()


def start_fake_server() -> int:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve() -> None:
        connection, _ = listener.accept()
        connection.sendall(
            b"STATUS|ready\n"
            b"RECV|at 7001 2628 50 50 0 0 0 0 0 0 0\n"
            b"RECV|in 1 Tester - 7001 50 50 0\n"
            b"RECV|in 1 DpsGuy - 1102 51 50 0\n"
            + _su(SELF_ID, 500)
            + _su(SELF_ID, 500)
            + _su(GUY_ID, 2000)
            # A partner (attacker type 2) and a buff (0 damage) must not
            # count toward the player's 2000.
            + _su(GUY_ID, 1000, attacker_type=2)
            + _su(GUY_ID, 0)
        )
        time.sleep(20)
        connection.close()
        listener.close()

    threading.Thread(target=serve, daemon=True).start()
    return port


def main() -> int:
    handle = tempfile.NamedTemporaryFile(
        prefix="ntpl-bazar-", suffix=".json", delete=False
    )
    handle.close()
    os.environ.setdefault("NTPL_BAZAR_CONFIG", handle.name)
    os.environ.setdefault(
        "NTPL_INVENTORY_DIR", tempfile.mkdtemp(prefix="ntpl-inventory-")
    )
    port = start_fake_server()
    app = Application("127.0.0.1", port, unique=False)
    result: dict[str, object] = {"ok": False, "detail": "not checked"}
    state = {"started": False, "budget": 50}

    def wait_ready() -> bool:
        state["budget"] -= 1
        world = app.engine.world
        if world.self_id != SELF_ID or not world.player_names:
            if state["budget"] <= 0:
                result["detail"] = "GUI never attached"
                app.quit()
                return False
            return True
        if state["started"]:
            return False
        state["started"] = True
        GLib.timeout_add(800, check)
        return False

    def check() -> bool:
        try:
            page = app.dps_page
            result["page"] = page is not None
            result["tab"] = (
                None
                if page is None
                else app.notebook.get_tab_label(page).get_text()
            )
            if page is not None:
                app.notebook.set_current_page(
                    app.notebook.page_num(page)
                )
            app._dps_render()
            rows = app.dps_last_rows
            by_id = {row.attacker_id: row for row in rows}
            tester = by_id.get(SELF_ID)
            guy = by_id.get(GUY_ID)
            result["rows"] = len(rows)
            result["order"] = [row.attacker_id for row in rows]
            result["names"] = [
                app._dps_player_name(row.attacker_id) for row in rows
            ]
            result["tester"] = (
                None
                if tester is None
                else (tester.damage, tester.hits, round(tester.dps))
            )
            result["guy"] = (
                None
                if guy is None
                else (guy.damage, guy.hits, round(guy.dps))
            )
            result["store"] = app.dps_store.get_n_items()
            result["info"] = app.dps_info.get_text()
            # Session window keeps the same totals but averages over the
            # elapsed session, so its DPS is at least the 10 s DPS.
            app.dps_window.set_selected(4)
            app._dps_render()
            session = {
                row.attacker_id: row for row in app.dps_last_rows
            }
            result["session_guy"] = (
                None
                if GUY_ID not in session
                else round(session[GUY_ID].dps)
            )
            app._dps_reset()
            result["reset_rows"] = len(app.dps_last_rows)
            result["reset_info"] = app.dps_info.get_text()
            result["ok"] = (
                result["page"] is True
                and result["tab"] == "Stats"
                and result["rows"] == 2
                and result["order"] == [GUY_ID, SELF_ID]
                and result["names"] == ["DpsGuy", "Tester"]
                and result["tester"] == (1000, 2, 100)
                and result["guy"] == (2000, 1, 200)
                and result["store"] == 2
                and "top DpsGuy" in str(result["info"])
                and result["session_guy"] is not None
                and result["session_guy"] >= 200
                and result["reset_rows"] == 0
                and result["reset_info"]
                == "No player damage in this window"
            )
            result["detail"] = (
                f"rows={result['rows']} order={result['order']} "
                f"names={result['names']} tester={result['tester']} "
                f"guy={result['guy']} info={result['info']!r} "
                f"session_guy={result['session_guy']} "
                f"reset={result['reset_rows']}"
            )
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(200, wait_ready)
    app.run([])
    print(
        "STATS SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
