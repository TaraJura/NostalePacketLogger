#!/usr/bin/env bash
# Headless GTK4 smoke test. Needs Xvfb; no game and no display required.
#
#   ./scripts/dev-test-gui.sh
#
# It starts a fake packet source, opens the real window against it, and checks
# that the reader thread, the GLib drain timer, the table model and the status
# bar all work together.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v xvfb-run >/dev/null 2>&1; then
    echo "xvfb-run is required: sudo apt install xvfb" >&2
    exit 1
fi

PORTALS="$(mktemp)"
BOTLOG="$(mktemp)"
trap 'rm -f "$PORTALS" "$BOTLOG"' EXIT

exec xvfb-run -a env GDK_BACKEND=x11 \
    PYTHONPATH="$ROOT/host" \
    NTPL_PORTALS="$PORTALS" \
    NTPL_BOT_LOG="$BOTLOG" \
    python3 "$ROOT/test/gui_smoke.py"
