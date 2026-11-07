#!/usr/bin/env bash
# Headless bot smoke test: fake game state, real map, real GUI bot loop.
#
#   ./scripts/dev-test-bot.sh
#
# Checks that the map loads and that the bot sends ncif + u_s (auto-attack)
# and walk (path loop) against a fake server. Needs Xvfb, not the game.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v xvfb-run >/dev/null 2>&1; then
    echo "xvfb-run is required: sudo apt install xvfb" >&2
    exit 1
fi

# The smokes must not read or write the developer's live session: the real
# DLL log can contain a different SP card (slot 0 -> a spell), a map id or
# portals, and learned portals must not land in ~/.config/ntpl/portals.json.
# A fresh empty log and portal file keep every run deterministic.
LOG="$(mktemp)"
PORTALS="$(mktemp)"
trap 'rm -f "$LOG" "$PORTALS"' EXIT

run() {
    xvfb-run -a env GDK_BACKEND=x11 \
        PYTHONPATH="$ROOT/host" \
        NTPL_LOG="$LOG" \
        NTPL_PORTALS="$PORTALS" \
        python3 "$ROOT/test/$1"
}

run gui_bot_smoke.py
run gui_bot_rules_smoke.py
run gui_spell_order_smoke.py
run gui_out_of_range_smoke.py
run gui_bot_reject_smoke.py
run gui_recover_smoke.py
