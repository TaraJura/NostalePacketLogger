#!/usr/bin/env bash
# Regenerate the README/docs screenshots from the real GTK4 window.
#
#   ./linux/scripts/readme-shots.sh [output-dir]
#
# Needs Xvfb, xdotool and ffmpeg. The window runs against a fake packet
# source with real map/monster data, so no game client or account is shown.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/../docs/assets}"
mkdir -p "$OUT"

capture() {
    local mode="$1" out="$2" title="$3"
    xvfb-run -a -s "-screen 0 1500x950x24" bash -c '
        export GDK_BACKEND=x11
        PYTHONPATH="'"$ROOT"'/host" NTPL_SHOT_MODE="'"$mode"'" \
            python3 "'"$ROOT"'/test/gui_readme_shots.py" &
        app=$!
        sleep 10
        window=$(xdotool search --onlyvisible --name "'"$title"'" 2>/dev/null | head -1)
        if [ -n "$window" ]; then
            eval "$(xdotool getwindowgeometry --shell "$window")"
            ffmpeg -loglevel error -y -f x11grab -draw_mouse 0 \
                -video_size "${WIDTH}x${HEIGHT}" \
                -i "${DISPLAY}+${X},${Y}" \
                -frames:v 1 "'"$out"'"
        else
            ffmpeg -loglevel error -y -f x11grab -draw_mouse 0 \
                -video_size 1280x818 -i "${DISPLAY}+0,0" -frames:v 1 "'"$out"'"
        fi
        kill "$app" 2>/dev/null || true
        wait "$app" 2>/dev/null || true
    '
}

capture map "$OUT/gui-map.png" "NosTale Packet Logger"
capture packets "$OUT/gui-packets.png" "NosTale Packet Logger"
capture picker "$OUT/gui-picker.png" "Select NosTale client"
capture spells "$OUT/gui-spells.png" "NosTale Packet Logger"
echo "screenshots written to $OUT"
