#!/usr/bin/env bash
# Start the clientless (headless) NosTale client.
#
#   linux/scripts/headless.sh
#   linux/scripts/headless.sh --token-file ~/.config/ntpl/token
#   NTPL_EMAIL=... NTPL_PASSWORD=... linux/scripts/headless.sh
#
# The token file holds the hex Gameforge thin token (chmod 600).
# Every packet is printed and the password prompt is visible by default;
# extra arguments are passed through.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GAME_DIR="${NTPL_GAME_DIR:-$HOME/games/gameforge/prefix/drive_c/users/$USER/Desktop/nostake/Nostale}"

export PYTHONPATH="$ROOT/host${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m ntpl headless --game-dir "$GAME_DIR" \
    --print-packets --show-password "$@"
