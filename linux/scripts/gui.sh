#!/usr/bin/env bash
# Launch the GTK4 UI for the NosTale packet engine.
#
#   ./linux/scripts/gui.sh
#   ./linux/scripts/gui.sh --no-client-picker     # straight to --host/--port
#
# At startup the window lists the running NosTale clients (via
# Injector.exe --list) and attaches to the chosen one; a client without the
# logger is injected from that dialog. One already-injected client is attached
# without asking. The injector (build/Injector.exe) and DLL must be built with
# `make -C linux all`.
#
# The window is non-unique: run this script again for another game client.
# Windows coordinate through per-client attach locks, so a client attached by
# one window is disabled in the others' picker; each window keeps its own bot
# state, map and window title, and loads its own bot config.
#
# NTPL_PORT is read by both the GUI and the DLL, so exporting it once before
# starting the game and the GUI keeps them in sync; a second client
# automatically moves to the next free port, and the GUI learns it from the
# DLL's C:\ntpl-<pid>.port file.

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
