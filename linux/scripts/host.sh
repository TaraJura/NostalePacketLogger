#!/usr/bin/env bash
# Run the Python host from the source tree without installing it.
#
#   ./scripts/host.sh status
#   ./scripts/host.sh monitor
#   ./scripts/host.sh gui

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT/host${PYTHONPATH:+:$PYTHONPATH}"

exec python3 -m ntpl "$@"
