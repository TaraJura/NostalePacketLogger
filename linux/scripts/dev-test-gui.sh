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
BAZAR="$(mktemp)"
FAMILY_STATE="$(mktemp)"
MIRROR="$(mktemp -d)"
trap 'rm -f "$PORTALS" "$BOTLOG" "$BAZAR" "$FAMILY_STATE"; rm -rf "$MIRROR"' EXIT

run_smoke() {
    xvfb-run -a env GDK_BACKEND=x11 \
        PYTHONPATH="$ROOT/host" \
        NTPL_PORTALS="$PORTALS" \
        NTPL_BOT_LOG="$BOTLOG" \
        NTPL_BAZAR_CONFIG="$BAZAR" \
        NTPL_FAMILY_STATE="$FAMILY_STATE" \
        NTPL_MIRROR_DIR="$MIRROR" \
        python3 "$ROOT/test/$1"
}

run_smoke gui_smoke.py
run_smoke gui_client_picker_smoke.py
run_smoke gui_multi_client_smoke.py
run_smoke gui_family_smoke.py
run_smoke gui_family_auto_smoke.py
