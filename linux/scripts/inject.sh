#!/usr/bin/env bash
# Inject PacketLogger.dll into every running NosTale client in a Wine prefix.
#
#   ./scripts/inject.sh [path/to/PacketLogger.dll]
#
# The client must already be running. Injection uses Windows PIDs inside the
# prefix, so no mapping to Linux PIDs is needed.
#
# Environment:
#   NTPL_WINE        wine binary (default: wine from PATH)
#   NTPL_WINEPREFIX  prefix to use (default: $WINEPREFIX or ~/.wine-nostale)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WINE_BIN="${NTPL_WINE:-wine}"
PREFIX="${NTPL_WINEPREFIX:-${WINEPREFIX:-$HOME/.wine-nostale}}"
DLL="${1:-$ROOT/build/PacketLogger.dll}"

if [[ ! -f "$ROOT/build/Injector.exe" ]]; then
    echo "missing $ROOT/build/Injector.exe" >&2
    echo "build it with: make -C \"$ROOT\" all" >&2
    exit 1
fi

if [[ ! -f "$DLL" ]]; then
    echo "missing DLL: $DLL" >&2
    exit 1
fi

if [[ ! -d "$PREFIX" ]]; then
    echo "no Wine prefix at $PREFIX" >&2
    echo "create it with: WINEPREFIX=\"$PREFIX\" wineboot -u" >&2
    exit 1
fi

export WINEPREFIX="$PREFIX"
export WINEDEBUG="${WINEDEBUG:--all}"

DLL_WIN="$(WINEPREFIX="$PREFIX" winepath -w "$DLL")"

exec "$WINE_BIN" "$ROOT/build/Injector.exe" --all --dll "$DLL_WIN"
