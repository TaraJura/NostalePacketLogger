"""Headless NosBazar relist price-change smoke test.

The seller tab reports our own listing (id 73411, vnum 2174, nothing sold),
so the relist must use the in-game **Change** action (``c_mod``) to lower
that listing's price instead of adding a new one with ``c_reg``. The fake
server answers a c_mod with a refreshed seller list, which the controller
takes as the confirmation.
"""

from __future__ import annotations

import os
import tempfile

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from gui_bazar_smoke import BazarServer
from ntpl.bazar import BazarItem, build_search_packet
from ntpl.gui import Application

OWN_ENTRY = "73411|14881134|2174|0|5|0|11496|1|1435|2|2|0"


def main() -> int:
    if not os.environ.get("NTPL_BAZAR_CONFIG"):
        handle = tempfile.NamedTemporaryFile(
            prefix="ntpl-bazar-", suffix=".json", delete=False
        )
        handle.close()
        os.environ["NTPL_BAZAR_CONFIG"] = handle.name
    server = BazarServer()
    server.seller_entries = [OWN_ENTRY]
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked"}

    state = {"started": False}

    def start_relist() -> bool:
        if state["started"]:
            return False
        if not any(
            entity.vnum == 793
            for entity in app.engine.world.entities.values()
        ):
            return True
        state["started"] = True
        try:
            item = BazarItem(
                "bubbl",
                build_search_packet([2174]),
                vnum=2174,
                inv_tab=2,
                amount=5,
                min_price=5000,
                nos_cost=0.5,
            )
            app.bazar.set_items([item], character="root1", refresh=30)
            app.bazar_character_entry.set_text("root1")
            app._bazar_start_relist()
            GLib.timeout_add(5000, check)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            lines = list(server.received)
            changes = [
                line for line in lines if line.startswith("SEND|c_mod ")
            ]
            registers = [
                line for line in lines if line.startswith("SEND|c_reg ")
            ]
            quote = app.bazar.results.get("bubbl")
            result["ok"] = (
                changes == ["SEND|c_mod 0 1 73411 2174 5 13797"]
                and not registers
                and quote is not None
                and quote.status == "changed"
            )
            result["detail"] = (
                f"changes={changes} registers={registers} "
                f"status={getattr(quote, 'status', None)} "
                f"detail={getattr(quote, 'detail', None)!r} "
                f"phase={app.bazar.phase} error={app.bazar.last_error!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1200, start_relist)
    app.run([])
    server.close()
    print(
        "BAZAR CHANGE SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
