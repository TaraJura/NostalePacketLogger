#!/usr/bin/env bash
# Regenerate the README/docs screenshots from the real GTK4 window.
#
#   ./linux/scripts/readme-shots.sh [output-dir] [mode]
#
# With no mode, all screenshots are regenerated. A single mode
# (map|packets|picker|spells|botlog|family|malicious|minigame|stats|bazar|
# resale|inventory|relist|items|config) regenerates just that image.
# Needs Xvfb, xdotool and ffmpeg. The window runs against a fake packet
# source with real map/monster data, so no game client or account is shown.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/../docs/assets}"
ONLY="${2:-all}"
mkdir -p "$OUT"

should() {
    [ "$ONLY" = "all" ] || [ "$ONLY" = "$1" ]
}

capture() {
    local mode="$1" out="$2" title="$3" wait="${4:-10}"
    xvfb-run -a -s "-screen 0 1500x950x24" bash -c '
        export GDK_BACKEND=x11
        PYTHONPATH="'"$ROOT"'/host" NTPL_SHOT_MODE="'"$mode"'" \
            python3 "'"$ROOT"'/test/gui_readme_shots.py" &
        app=$!
        sleep '"$wait"'
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

if should map; then
    capture map "$OUT/gui-map.png" "NosTale Packet Logger"
fi
if should packets; then
    capture packets "$OUT/gui-packets.png" "NosTale Packet Logger"
fi
if should picker; then
    capture picker "$OUT/gui-picker.png" "Select NosTale client"
fi
if should spells; then
    capture spells "$OUT/gui-spells.png" "NosTale Packet Logger"
fi
if should bazar; then
    # The monitor needs a moment to open Bibi Basar and finish the first
    # priced pass before the capture.
    capture bazar "$OUT/gui-bazar.png" "NosTale Packet Logger" 14
fi
if should inventory; then
    # Item.dat names load once, then the inline Inventory section is shown.
    capture inventory "$OUT/gui-inventory.png" "NosTale Packet Logger" 14
fi
if should relist; then
    # A demo item is selected in the Relist section with a floor.
    capture relist "$OUT/gui-relist.png" "NosTale Packet Logger" 14
fi
if should botlog; then
    capture botlog "$OUT/gui-botlog.png" "NosTale Packet Logger"
fi
if should family; then
    capture family "$OUT/gui-family.png" "NosTale Packet Logger" 12
fi
if should malicious; then
    capture malicious "$OUT/gui-malicious.png" "NosTale Packet Logger" 10
fi
if should minigame; then
    capture minigame "$OUT/gui-minigame.png" "NosTale Packet Logger" 10
fi
if should stats; then
    # The demo hits arrive ~4 s in, so the default 10 s window shows them.
    capture stats "$OUT/gui-stats.png" "NosTale Packet Logger" 12
fi
if should resale; then
    # The NPC-resale Monitor view needs the Bibi open and one priced pass.
    capture resale "$OUT/gui-bazar-resale.png" "NosTale Packet Logger" 14
fi
if should items; then
    capture items "$OUT/gui-bazar-items.png" "NosTale Packet Logger" 12
fi
if should config; then
    capture config "$OUT/gui-bazar-config.png" "NosTale Packet Logger" 12
fi
echo "screenshots written to $OUT"
