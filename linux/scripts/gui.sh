#!/usr/bin/env bash
# Launch the GTK4 UI for the NosTale packet engine.
#
#   ./linux/scripts/gui.sh
#   ./linux/scripts/gui.sh --host 127.0.0.1 --port 47821
#
# The injected DLL must already be running (linux/scripts/inject.sh). If it is
# not, the window opens with "not connected" and retries for about 10 seconds;
# inject and reopen the window afterwards.
#
# NTPL_PORT is read by both the GUI and the DLL, so exporting it once before
# starting the game and the GUI keeps them in sync.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Check the optional GTK4 stack before starting; the error would otherwise be
# a Python traceback inside the GUI import.
if ! python3 - <<'PY' >/dev/null 2>&1
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
PY
then
    echo "GTK4/PyGObject is missing." >&2
    echo "Install it with: sudo apt install python3-gi gir1.2-gtk-4.0" >&2
    exit 1
fi

export PYTHONPATH="$ROOT/host${PYTHONPATH:+:$PYTHONPATH}"

exec python3 -m ntpl gui "$@"
