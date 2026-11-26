"""Headless smoke test for the NosMall importer filters.

Checks the two rules the user asked for:

* the picker lists only groups with a known ND cost (no unpriced rows);
* pets, fairies and partner supplies are excluded from the picker and from
  **Add all matching**;
* **Remove unpriced & pets** deletes existing unpriced/pet rows after the
  two-click confirmation and keeps priced ordinary items.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import socket
import threading
import time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.bazar import BazarItem, build_search_packet
from ntpl.gui import Application
from ntpl.nosmall import is_pet_group


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

    def check() -> bool:
        try:
            app._open_nosmall_picker()
            matches = list(app._nosmall_matches)
            costs = app._nosmall_costs()
            priced = all(
                app._nosmall_group_cost(group, costs) is not None
                for group in matches
            )
            pets = [group.name for group in matches if is_pet_group(group)]
            # A group with an obvious missing price must not appear.
            fairy = [
                group for group in matches
                if "Fairy Experience" in group.name
            ]
            expected_cost = all(
                app._nosmall_group_cost(group, costs) is not None
                for group in matches
            )
            # Seed the item list with one keeper and two rows the cleanup
            # must delete (no ND cost / a pet).
            good = BazarItem(
                name="Soulstone Blessing",
                search_packet=build_search_packet([1362, 5195, 9075]),
                vnum=1362,
                nos_cost=1.0,
                enabled=True,
            )
            unpriced = BazarItem(
                name="Unpriced Thing",
                search_packet=build_search_packet([999999]),
                vnum=999999,
                nos_cost=None,
                enabled=True,
            )
            pet = BazarItem(
                name="Fairy Booster",
                search_packet=build_search_packet([1296, 5194, 9074]),
                vnum=1296,
                nos_cost=5.0,
                enabled=True,
            )
            app._bazar_set_items([good, unpriced, pet])
            app._nosmall_prune()  # arm
            armed = app._nosmall_prune_armed_at > 0.0
            app._nosmall_prune()  # apply
            kept = list(app.bazar.config.items)
            # Add all matching must only add priced, non-pet groups.
            app._nosmall_add_all()
            added = list(app.bazar.config.items)
            added_priced = all(
                item.nos_cost is not None and item.nos_cost > 0
                for item in added
            )
            added_names = " ".join(item.name for item in added).lower()
            no_pets = not any(
                word in added_names
                for word in ("pet food", "fairy booster", "partner medicine")
            )
            # Importing the catalog is a monitoring action: no imported
            # item may join Relist Start's set without a Relist tick.
            imported_only = [item for item in added if item is not good]
            no_relist = all(item.relist is False for item in imported_only)
            result["ok"] = bool(
                matches
                and priced
                and expected_cost
                and not pets
                and not fairy
                and armed
                and kept == [good]
                and added_priced
                and no_pets
                and no_relist
                and len(added) >= len(matches) - 1
            )
            result["detail"] = (
                f"matches={len(matches)} priced={priced} "
                f"pets={pets[:3]} fairy={fairy} armed={armed} "
                f"kept={[i.name for i in kept]} "
                f"added={len(added)} added_priced={added_priced} "
                f"no_relist={no_relist} "
                f"no_pets={no_pets} status={app._nosmall_status.get_text()!r}"
            )
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["detail"] = f"check exception: {exc!r}"
        app.quit()
        return False

    def start_when_ready() -> bool:
        if app.window is None:
            return True
        GLib.timeout_add(600, check)
        return False

    GLib.timeout_add(700, start_when_ready)
    app.run([])
    print("NOSMALL IMPORT SMOKE:", "PASS" if result["ok"] else "FAIL",
          result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
