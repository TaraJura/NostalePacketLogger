"""Headless NosBazar relist smoke test.

Exercises the risky half of the feature: **Relist Start** is the only gate,
then the controller must undercut the fake cheapest listing by one gold and
send the exact ``c_reg`` packet.
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


def main() -> int:
    if not os.environ.get("NTPL_BAZAR_CONFIG"):
        handle = tempfile.NamedTemporaryFile(
            prefix="ntpl-bazar-", suffix=".json", delete=False
        )
        handle.close()
        os.environ["NTPL_BAZAR_CONFIG"] = handle.name
    server = BazarServer()
    app = Application("127.0.0.1", server.port, unique=False)
    result = {"ok": False, "detail": "not checked", "gate": False}

    state = {"started": False}

    def start_relist() -> bool:
        if state["started"]:
            return False
        # Wait until the fake server's `in 2 793 ...` reached the engine, so
        # the controller has a Bibi candidate on its first open attempt.
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
                relist=True,
            )
            app.bazar.set_items([item], character="root1", refresh=30)
            app.bazar_character_entry.set_text("root1")
            # Relist Start is the only gate; no extra checkbox to tick.
            app._bazar_start_relist()
            result["gate"] = app.bazar.running
            GLib.timeout_add(5000, check)
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"start exception: {exc!r}"
            app.quit()
        return False

    def check() -> bool:
        try:
            lines = list(server.received)
            registers = [
                line for line in lines if line.startswith("SEND|c_reg ")
            ]
            searches = [
                line for line in lines
                if line.startswith("SEND|c_blist ") and line.endswith(" 2174")
            ]
            quote = app.bazar.results.get("bubbl")
            result["ok"] = (
                result["gate"]
                and bool(searches)
                and registers
                == ["SEND|c_reg 0 2 7 9 4 1 0 5 13797 50 2 0"]
                and quote is not None
                and quote.status == "register-sent"
            )
            result["detail"] = (
                f"gate={result['gate']} searches={len(searches)} "
                f"registers={registers} status={getattr(quote, 'status', None)} "
                f"detail={getattr(quote, 'detail', None)!r} "
                f"phase={app.bazar.phase} "
                f"error={app.bazar.last_error!r} "
                f"npc={app.engine.world.bazar.npc_id} "
                f"bibi={[e.entity_id for e in app.engine.world.entities.values() if e.vnum == 793]}"
            )
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(1200, start_relist)
    app.run([])
    server.close()
    print("BAZAR RELIST SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
