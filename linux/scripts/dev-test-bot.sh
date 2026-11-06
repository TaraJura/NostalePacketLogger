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

run() {
    xvfb-run -a env GDK_BACKEND=x11 \
        PYTHONPATH="$ROOT/host" \
        python3 "$ROOT/test/$1"
}

run gui_bot_smoke.py
run gui_bot_reject_smoke.py
run gui_recover_smoke.py
