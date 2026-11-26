"""Generate the screenshots used by the README and docs.

Manual tool, not part of ``make gui-test``: it starts a fake packet source,
opens the real GTK4 window against a real map from NStcData, draws a route,
spawns a few monsters with their real names, and leaves the window on screen
for ``linux/scripts/readme-shots.sh`` to capture with ffmpeg/Xvfb.

    NTPL_SHOT_MODE=map|packets|picker|spells|botlog|family|malicious|
        minigame|stats|bazar|resale|relist|items|config \
        PYTHONPATH=linux/host python3 linux/test/gui_readme_shots.py

``map``, ``packets`` and ``spells`` show the Map/Bot tab, ``bazar`` and
``resale`` the two Monitor views, and the rest one section each, so every
tab and sub-tab of the app has a picture.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl import bazar as bazar_module
from ntpl.bazar import BazarItem, build_search_packet
from ntpl.clients import ClientInfo, Discovery, WineContext, client_lock_path
from ntpl.gui import Application
from ntpl.mapdata import astar, load_nstc

MODE = os.environ.get("NTPL_SHOT_MODE", "map")
#: Every mode that needs the bazaar tab, its demo catalog and a fake Bibi.
BAZAR_MODES = (
    "bazar",
    "resale",
    "relist",
    "items",
    "config",
)
CHAR_ID = 14881134
HP, HP_MAX = 2950, 3209
MP, MP_MAX = 3600, 3727

#: The demo catalog shown on the NosBazar tab. Prices are synthetic but the
#: vnums, ND costs and names are from the real NosMall catalog, so the profit
#: column exercises the same math as a live pass.
BAZAR_ITEMS = (
    ("Wings of Friendship", (2160, 2312, 10048), 0.5, 11111, "Trader"),
    ("Speaker", (2173, 10028), 0.5, 9800, "Merchant"),
    ("Speech Bubble", (2174, 10029), 0.5, 5999, "ShopMule"),
    ("Soulstone Blessing", (1362, 5195, 9075), 1.0, 37999, "Auctioneer"),
    ("Perfume", (1428,), 0.8, 5555, "Dealer"),
    ("Higher SP Protection Scroll", (1364, 9464), 5.0, 40000, "Collector"),
    ("Lower SP Protection Scroll", (1363, 5933, 9463), 5.0, 35000, "Broker"),
    (
        "Equipment Protection Scroll",
        (1218, 9458),
        20.0,
        177777,
        "Vendor",
    ),
    ("Golden Specialist Card Holder", (4240,), 100.0, 750000, "Reseller"),
    ("Ancelloan's Blessing", (1286, 1452, 9041), 5.0, 29000, "Bargain"),
    ("Tarot Card Game", (1904,), 10.0, 58900, "Market"),
    ("Blessing Amulet", (282, 8541), 50.0, 550000, "Courier"),
    (
        "Sealed Champion Blessing Amulet (Random)",
        (5735,),
        50.0,
        300000,
        "Trader",
    ),
)


#: NPC-resale demo catalog: items bought at an NPC and sold in the bazaar.
#: (name, vnum, listing amount, NPC gold price, cheapest listing, seller)
RESALE_ITEMS = (
    ("Pet Food", 2077, 5, 1500, 1900, "Trader"),
    ("Bell of Sweet Home", 2072, 5, 1200, 2500, "Merchant"),
    ("Fairy Booster", 1296, 1, 5000, 8200, "ShopMule"),
    ("Medicine Bottle", 1765, 5, 900, 1400, "Dealer"),
)

#: Realistic action lines for the Bot log screenshot (the tab mirrors the
#: status bar and the per-client file).
DEMO_LOG_LINES = (
    "NosTale [#1] · PID 1512 · connected to 127.0.0.1:47821",
    "ntpl 0.3.0 | hooks recv=ACTIVE send=ACTIVE skill=ACTIVE",
    "loaded map 2628 (170x150) · 19 NosBazar item(s)",
    "path ready · 23 cells · 3 waypoints",
    "auto-attack on · retaliate on · pickup on · recover 50/30",
    "TARGET|5001 (Mountain Turtle) · u_s 0 3 5001",
    "su 5001 · 1,817 damage · walking to (26,131)",
    "kill 5001 · pickup 4001 · back to the loop",
    "auto-recover: HP 32% below 50% · sitting",
    "auto-recover: HP 78% · standing, resuming the loop",
    "bazaar: NosBazar window is open (client shortcut)",
    "Speech Bubble: c_reg sent for 9,499 · waiting for rc_reg",
)


def demo_items():
    """The NosMall demo catalog as :class:`BazarItem` objects."""
    return [
        BazarItem(
            name=name,
            search_packet=build_search_packet(list(vnums)),
            vnum=vnums[0],
            amount=5,
            min_price=0,
            nos_cost=cost,
            enabled=True,
        )
        for name, vnums, cost, _price, _seller in BAZAR_ITEMS
    ]


def resale_items():
    """The NPC-resale demo catalog as :class:`BazarItem` objects."""
    return [
        BazarItem(
            name=name,
            search_packet=build_search_packet([vnum]),
            vnum=vnum,
            amount=amount,
            min_price=0,
            npc_cost=npc,
            enabled=True,
        )
        for name, vnum, amount, npc, _price, _seller in RESALE_ITEMS
    ]


def pick_scene():
    """A walkable start, a 20-40 cell route and three monster cells."""
    grids = load_nstc()
    for map_id in (20, 2, 85):
        grid = grids.get(map_id)
        if grid is None:
            continue
        cells = [
            (x, y)
            for y in range(grid.height)
            for x in range(grid.width)
            if grid.is_walkable(x, y)
        ]
        cells.sort(
            key=lambda cell: abs(cell[0] - grid.width // 2)
            + abs(cell[1] - grid.height // 2)
        )
        for start in cells[:80]:
            for dx, dy in (
                (42, 0),
                (0, 42),
                (-42, 0),
                (0, -42),
                (34, 26),
                (30, -30),
            ):
                goal = (start[0] + dx, start[1] + dy)
                if not (
                    0 <= goal[0] < grid.width
                    and 0 <= goal[1] < grid.height
                ):
                    continue
                if not grid.is_walkable(*goal):
                    continue
                path = astar(grid, start, goal)
                if path and len(path) >= 30:
                    return map_id, grid, start, path
    return None


def monster_line(vnum: int, entity: int, x: int, y: int) -> str:
    return (
        f"RECV|in 3 {vnum} {entity} {x} {y} 2 100 100 0 0 0 -1 0 0 -1 "
        "- 0 -1 0 0 0 0 0 0 0 0 0 0 0 1 0\n"
    )


def start_fake_client(scene) -> int:
    map_id, grid, start, path = scene
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    first, middle, last = path[len(path) // 4], path[len(path) // 2], path[-1]
    player = path[len(path) // 3]
    bazar_mode = MODE in BAZAR_MODES
    lines = [
        "STATUS|ready",
        "RECV|walk 1 2 3 4",
        f"RECV|c_map 0 {map_id} 1",
        f"RECV|at {CHAR_ID} {map_id} {start[0]} {start[1]} 2 0 13 1 -1 0 0",
        f"RECV|stat {HP} {HP_MAX} {MP} {MP_MAX} 0 0",
        "RECV|c_cond 0 0 1",
        monster_line(419, 5001, *first),
        monster_line(420, 5002, *middle),
        monster_line(421, 5003, *last),
        f"RECV|in 1 Mira - 900 {player[0]} {player[1]} 2 0 0 0",
        "RECV|ncif 3 5001",
        "SEND|u_s 0 3 5001",
        "RECV|su 1 14881134 3 5001 855 7 11 4001 0 0 0 0 1817 0 0 0 4140",
        f"RECV|mv 3 5003 {last[0]} {last[1]} 4",
        "RECV|get 5001 4001",
    ]
    if bazar_mode:
        lines.extend(
            [
                # Our own name fills the Monitor character field, and the
                # Bibi entity next to us drives the open sequence.
                f"RECV|in 1 BotMule - {CHAR_ID} {start[0]} {start[1]} 2 0 0 0",
                f"RECV|in 2 793 9584 {start[0] + 1} {start[1]} 3 100 100 "
                "460 0 0 -1 1 0 -1 - 0",
                "RECV|gold 100000 0",
            ]
        )
        if MODE in ("items", "relist"):
            # A real mix: normal stacks, equipment rows (quantity 0 and a
            # bound negative vnum) and the extra bags 6/7.
            lines.extend(
                [
                    "RECV|inv 1 0.1362.10 1.1007.52 2.1012.40 "
                    "3.2174.5 4.2282.11 5.1004.17",
                    "RECV|inv 2 0.2115.5 1.2307.27",
                    "RECV|inv 0 42.884.0.0.0.0.0 63.-4962.5.7.1.0.0",
                    "RECV|inv 6 0.905.0.5.0",
                ]
            )
        else:
            # A complete core inventory (bags 0/1/2) keeps the Items
            # summary from being marked partial in the demo shot.
            lines.extend(
                [
                    "RECV|inv 0 30.885.0.0.0.0.0",
                    "RECV|inv 1 7.2174.5",
                    "RECV|inv 2 0.2115.5",
                ]
            )
    if MODE == "family":
        # Invite candidates: two with no family (one with a fairy), one with
        # a family (not actionable) and one whose family is still unknown.
        lines.extend(
            [
                f"RECV|in 1 Kael - 901 {start[0] + 2} {start[1]} 2 0 0 0",
                f"RECV|in 1 Tova - 902 {start[0] - 2} {start[1]} 2 0 0 0",
                f"RECV|in 1 Bram - 903 {start[0]} {start[1] + 2} 2 0 0 0",
                "RECV|gidx 1 900 -1 -",
                "RECV|gidx 1 901 -1 -",
                "RECV|gidx 1 902 -1 -",
                "RECV|gidx 1 903 14.917 Koblizci",
                "RECV|pairy 1 900 0 2",
                "RECV|pairy 1 901 0 0",
            ]
        )
    late_lines: list[str] = []
    if MODE == "stats":
        # Two more players spawn with the scene; their hits arrive a few
        # seconds later so the default 10 s window still shows them at
        # capture time. Live `su` layout: HP % at 11, damage at 12, target
        # HP at 15/16.
        lines.extend(
            [
                f"RECV|in 1 BotMule - {CHAR_ID} {start[0]} {start[1]} 2 0 0 0",
                f"RECV|in 1 DpsDude - 1102 {start[0] + 2} {start[1]} 2 0 0 0",
                f"RECV|in 1 Healerz - 1103 {start[0] - 2} {start[1]} 2 0 0 0",
            ]
        )
        late_lines.extend(
            [
                "RECV|su 1 1102 3 5001 855 7 11 4001 0 0 1 62 41200 0 0 "
                "78000 100000",
                "RECV|su 1 1102 3 5001 855 7 11 4001 0 0 1 58 39800 0 0 "
                "38200 100000",
                "RECV|su 1 1103 3 5001 866 7 11 4001 0 0 1 66 17300 0 0 "
                "20900 100000",
                "RECV|su 1 1103 3 5001 866 7 11 4001 0 0 1 61 16900 0 0 "
                "4000 100000",
            ]
        )
    payload = ("\n".join(lines) + "\n").encode("latin-1")
    late_payload = ("\n".join(late_lines) + "\n").encode("latin-1")
    by_vnum = {
        vnum: (price, seller)
        for _name, vnums, _cost, price, seller in BAZAR_ITEMS
        for vnum in vnums
    }
    by_vnum.update(
        {
            vnum: (price, seller)
            for _name, vnum, _amount, _npc, price, seller in RESALE_ITEMS
        }
    )

    def bazar_response(text: str, connection: socket.socket) -> None:
        if text == "POS":
            connection.sendall(
                (f"STATUS|POS {start[0]} {start[1]} {CHAR_ID} 0\n").encode()
            )
            return
        if text.startswith("SEND|npc_req 2 9584"):
            connection.sendall(b"RECV|npc_req 2 9584 460\n")
            return
        if text.startswith("SEND|n_run 60 0 2 9584"):
            connection.sendall(b"RECV|wopen 32 2 642\n")
            return
        if not text.startswith("SEND|c_blist "):
            return
        parts = text.split()
        try:
            count = int(parts[9])
            wanted = [int(value) for value in parts[10 : 10 + count]]
        except (IndexError, ValueError):
            wanted = []
        for vnum in wanted:
            hit = by_vnum.get(vnum)
            if hit is None:
                continue
            price, seller = hit
            entry = (
                f"1001|2002|{seller}|{vnum}|5|0|{price}|120|"
                "0|0|0|0|0|0|^0"
            )
            connection.sendall(f"RECV|rc_blist 0 {entry}\n".encode())
            return
        connection.sendall(b"RECV|rc_blist 0\n")

    def serve() -> None:
        connection, _ = listener.accept()
        try:
            connection.sendall(payload)
            if not bazar_mode:
                if late_payload:
                    time.sleep(4.0)
                    connection.sendall(late_payload)
                time.sleep(120)
                return
            buffer = b""
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    bazar_response(
                        line.decode("utf-8", "replace"), connection
                    )
        except OSError:
            return
        finally:
            connection.close()
            listener.close()

    threading.Thread(target=serve, daemon=True).start()
    return port


def picker_discovery(port: int) -> Discovery:
    context = WineContext(wine="wine", prefix=Path("/nonexistent"), env={})
    runtime = Path(tempfile.mkdtemp(prefix="ntpl-shot-lock-"))
    os.environ["NTPL_RUNTIME_DIR"] = str(runtime)
    owned = client_lock_path(332)
    owned.parent.mkdir(parents=True, exist_ok=True)
    owned.write_text(f"{os.getppid()}\n", encoding="ascii")
    return Discovery(
        clients=[
            ClientInfo(context, 331, "NosTale - Seedle", port),
            ClientInfo(context, 332, "NosTale - Dander", port + 1),
            ClientInfo(context, 333, "NosTale - Chicken", None),
        ],
        errors=[],
        injector=Path("/nonexistent/Injector.exe"),
        dll=Path("/nonexistent/PacketLogger.dll"),
    )


def main() -> int:
    if MODE in BAZAR_MODES:
        # Never touch the developer's real bazar config or mirror bus.
        if not os.environ.get("NTPL_BAZAR_CONFIG"):
            handle = tempfile.NamedTemporaryFile(
                prefix="ntpl-shot-bazar-", suffix=".json", delete=False
            )
            handle.close()
            os.environ["NTPL_BAZAR_CONFIG"] = handle.name
        os.environ.setdefault(
            "NTPL_MIRROR_DIR", tempfile.mkdtemp(prefix="ntpl-shot-mirror-")
        )
        os.environ.setdefault(
            "NTPL_INVENTORY_DIR",
            tempfile.mkdtemp(prefix="ntpl-shot-inventory-"),
        )
        os.environ.setdefault(
            "NTPL_DB", tempfile.mktemp(prefix="ntpl-shot-db-")
        )
        # A full demo table should be on screen when the capture runs, not a
        # half-finished 2.5 s-paced pass.
        bazar_module.SEARCH_INTERVAL_S = 0.15
        bazar_module.RESPONSE_TIMEOUT_S = 2.0
    scene = pick_scene()
    if scene is None:
        print("no walkable scene found")
        return 1
    map_id, grid, start, path = scene
    port = start_fake_client(scene)
    app = Application("127.0.0.1", port, unique=False, select_client=False)
    waypoints = [path[-1]]
    scheduled = {
        "late": False,
        "bazar": False,
        "resale": False,
        "relist": False,
        "items": False,
        "config": False,
        "family": False,
        "malicious": False,
        "minigame": False,
        "botlog": False,
    }

    def start_bazar() -> bool:
        if app.window is None:
            return True
        if not any(
            entity.vnum == 793
            for entity in app.engine.world.entities.values()
        ):
            return True
        items = demo_items()
        app._bazar_set_items(items)
        app.bazar_character_entry.set_text("BotMule")
        app.bazar_refresh_spin.set_value(30)
        if not app.bazar_npc_entry.get_text().strip():
            app.bazar_npc_entry.set_text("9584")
        app._bazar_show_section("monitor")
        app._bazar_start_monitor()
        app._set_status(
            f"NosBazar monitor · {len(items)} priced items · "
            "cheapest listing per item"
        )
        return False

    def start_resale_shot() -> bool:
        if app.window is None:
            return True
        if not any(
            entity.vnum == 793
            for entity in app.engine.world.entities.values()
        ):
            return True
        items = resale_items()
        app._bazar_set_items(items)
        app.bazar_character_entry.set_text("BotMule")
        app.bazar_refresh_spin.set_value(30)
        if not app.bazar_npc_entry.get_text().strip():
            app.bazar_npc_entry.set_text("9584")
        app._bazar_show_section("monitor")
        if app.bazar_monitor_notebook is not None:
            app.bazar_monitor_notebook.set_current_page(1)
        app._bazar_start_monitor()
        app._set_status(
            "NPC resale · gold markup over the NPC price per item"
        )
        return False

    def start_items_shot() -> bool:
        if app.window is None:
            return True
        app._bazar_set_items(demo_items())
        app._bazar_show_section("items")
        # The catalog holds all 7,805 DB rows; filter to the demo item so
        # the selected row is on screen for the capture.
        if app._inventory_search is not None:
            app._inventory_search.set_text("Wings of Friendship")
        app._bazar_select_item("Wings of Friendship")
        form = app.bazar_form
        if form:
            form["tab"].set_selected(3)  # Etc
            form["amount"].set_value(5)
            form["min"].set_value(10000)
        app._set_status(
            "items · Wings of Friendship · Etc · amount 5 · floor 10,000 · "
            "Cost NosMall 0.5 ND"
        )
        return False

    def start_config_shot() -> bool:
        if app.window is None:
            return True
        app._bazar_set_items(demo_items())
        app.bazar_character_entry.set_text("BotMule")
        app.bazar_refresh_spin.set_value(30)
        if not app.bazar_npc_entry.get_text().strip():
            app.bazar_npc_entry.set_text("9584")
        app._bazar_show_section("config")
        app._set_status(
            "configuration · Bibi id, refresh interval and the "
            "per-client JSON file"
        )
        return False

    def start_botlog_shot() -> bool:
        if app.window is None:
            return True
        app._clear_bot_log()
        for line in DEMO_LOG_LINES:
            app._log_line(line)
        app.status.set_text(
            "bot log · every action with its timestamp, mirrored to "
            "~/.config/ntpl/bot-<pid>.log"
        )
        return False

    def start_family_shot() -> bool:
        if app.window is None:
            return True
        app._family_remember("Mira")
        app._family_render_players()
        app._set_status(
            "family · every player on the map with its family and fairy "
            "state, invites, and the persisted invited list"
        )
        return False

    def start_malicious_shot() -> bool:
        if app.window is None:
            return True
        app.speed_boost_spin.set_value(3)
        app.cast_boost_switch.set_active(True)
        app._set_status(
            "malicious · speed +3 applied · cast speed on (client-side)"
        )
        return False

    def start_minigame_shot() -> bool:
        if app.window is None:
            return True
        app.minigame_key_entry.set_text("y")
        app.minigame_interval_spin.set_value(120)
        app.minigame_focus_switch.set_active(True)
        app.minigame_radius_spin.set_value(12)
        app._set_status(
            "minigame · Sheep Farm · key y every 120 ms · focus players "
            "within 12 tiles"
        )
        return False

    def start_relist_shot() -> bool:
        if app.window is None or not app.engine.world.inventory:
            return True
        from ntpl.nosmall import load_item_info

        try:
            app.item_info = load_item_info()
        except Exception:  # noqa: BLE001 - shot works without names
            app.item_info = {}
        app.item_info_loaded = True
        items = [
            BazarItem(
                name=name,
                search_packet=build_search_packet(list(vnums)),
                vnum=vnums[0],
                inv_tab=2,
                amount=5,
                min_price=10000,
                nos_cost=cost,
                enabled=True,
            )
            for name, vnums, cost, _price, _seller in BAZAR_ITEMS
        ]
        app._bazar_set_items(items)
        app.bazar_character_entry.set_text("BotMule")
        app._bazar_select_relist_item("Wings of Friendship")
        form = app.bazar_relist_form
        form["tab"].set_selected(3)  # Etc
        form["min"].set_value(10000)
        app._bazar_relist_save()
        app._bazar_select_relist_item("Wings of Friendship")
        app._bazar_show_section("relist")
        app._set_status(
            "relist setup · Wings of Friendship · vnum 2160 · Etc · "
            "floor 10,000"
        )
        return False

    def scroll_spells() -> bool:
        sidebar = app.sidebar_scroll.get_child()
        ok, rect = app.spell_rules_box.compute_bounds(sidebar)
        adjustment = app.sidebar_scroll.get_vadjustment()
        if ok:
            adjustment.set_value(max(0.0, rect.origin.y - 40))
        else:
            adjustment.set_value(adjustment.get_upper() * 0.62)
        print(
            "spells scroll:",
            ok,
            rect.origin.y if ok else None,
            adjustment.get_upper(),
            adjustment.get_value(),
            file=sys.stderr,
        )
        return False

    def add_spells() -> bool:
        if not app.spells_loaded:
            return True
        added = 0
        for wanted in ("Fire Bolt", "Healing"):
            spell = next(
                (
                    spell
                    for spell in app.spells.values()
                    if spell.active and wanted.lower() in spell.name.lower()
                ),
                None,
            )
            if spell is not None:
                app._add_spell_from_info(spell)
                added += 1
        print("spells added:", added, file=sys.stderr)
        GLib.timeout_add(500, scroll_spells)
        return False

    def late_setup() -> bool:
        app.waypoints[:] = waypoints
        app.path_start = None
        app._rebuild_path()
        app._set_status(
            f"map {map_id} · {grid.width}x{grid.height} · "
            f"{len(app.path_cells)}-cell route · 3 monsters"
        )
        print(
            "shot scene:",
            map_id,
            "start",
            start,
            "path",
            len(app.path_cells),
            "waypoints",
            waypoints,
            file=sys.stderr,
        )

        def redraw() -> bool:
            app.map_area.queue_draw()
            return False

        GLib.timeout_add(400, redraw)
        if MODE == "spells":
            app._ensure_spells_loaded()
            GLib.timeout_add(200, add_spells)
        if MODE == "picker":
            app._show_client_dialog(picker_discovery(port))
        return False

    def arrange() -> bool:
        if MODE == "botlog":
            if app.window is None:
                return True
            app.window.set_default_size(1440, 900)
            app.notebook.set_current_page(2)
            if not scheduled["botlog"]:
                scheduled["botlog"] = True
                GLib.timeout_add(2600, start_botlog_shot)
            return False
        if MODE == "stats":
            app.notebook.set_current_page(7)
            if app.window is not None:
                app.window.set_default_size(1440, 900)
            app._set_status(
                "stats · player DPS meter · 5 s window · damage from su"
            )
            return False
        if MODE == "family":
            if app.window is None:
                return True
            app.window.set_default_size(1440, 900)
            app.notebook.set_current_page(4)
            if not scheduled["family"]:
                scheduled["family"] = True
                GLib.timeout_add(2000, start_family_shot)
            return False
        if MODE == "malicious":
            if app.window is None:
                return True
            app.window.set_default_size(1440, 900)
            app.notebook.set_current_page(5)
            if not scheduled["malicious"]:
                scheduled["malicious"] = True
                GLib.timeout_add(1500, start_malicious_shot)
            return False
        if MODE == "minigame":
            if app.window is None:
                return True
            app.window.set_default_size(1440, 900)
            app.notebook.set_current_page(6)
            if not scheduled["minigame"]:
                scheduled["minigame"] = True
                GLib.timeout_add(1500, start_minigame_shot)
            return False
        if MODE == "resale":
            if app.window is None:
                return True
            app.window.set_default_size(1440, 900)
            app.notebook.set_current_page(3)
            if not scheduled["resale"]:
                scheduled["resale"] = True
                GLib.timeout_add(1500, start_resale_shot)
            return False
        if MODE == "items":
            if app.window is None:
                return True
            app.window.set_default_size(1440, 900)
            app.notebook.set_current_page(3)
            if not scheduled["items"]:
                scheduled["items"] = True
                GLib.timeout_add(1500, start_items_shot)
            return False
        if MODE == "config":
            if app.window is None:
                return True
            app.window.set_default_size(1440, 900)
            app.notebook.set_current_page(3)
            if not scheduled["config"]:
                scheduled["config"] = True
                GLib.timeout_add(1500, start_config_shot)
            return False
        if MODE == "packets":
            app.notebook.set_current_page(0)
            if app.window is not None:
                app.window.set_default_size(1440, 900)
            return False
        if MODE == "bazar":
            if app.window is None:
                return True
            app.window.set_default_size(1440, 900)
            app.notebook.set_current_page(3)
            if not scheduled["bazar"]:
                scheduled["bazar"] = True
                GLib.timeout_add(1500, start_bazar)
            return False
        if MODE == "relist":
            if app.window is None:
                return True
            app.window.set_default_size(1440, 900)
            app.notebook.set_current_page(3)
            if not scheduled["relist"]:
                scheduled["relist"] = True
                GLib.timeout_add(1500, start_relist_shot)
            return False
        if app.map_grid is None or app.window is None:
            return True
        app.window.set_default_size(1440, 900)
        app.notebook.set_current_page(1)
        if not scheduled["late"]:
            scheduled["late"] = True
            # The map auto-detect and map-surface load run after the first
            # draw; apply the route once they have settled.
            GLib.timeout_add(2500, late_setup)
        return False

    GLib.timeout_add(1200, arrange)
    return int(app.run([]))


if __name__ == "__main__":
    raise SystemExit(main())
