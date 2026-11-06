#!/usr/bin/env bash
# End-to-end test without NosTale.
#
# Builds nothing: run `make all test-host` first. Creates a throwaway Wine
# prefix and:
#   1. runs HookStubTest.exe against the real hooks.S;
#   2. copies FakeGame.exe to NostaleClientX.exe and starts it with --no-load;
#   3. runs the real Injector.exe --all against it;
#   4. asks the real Python host to fetch the DLL's status over TCP.
#
# The pattern scan is expected to report NOT-FOUND: FakeGame is not the game.
# What this proves is the x86 stubs, the injector path, the DLL lifecycle, the
# TCP transport, and that the host and DLL agree on the protocol.
#
#   ./scripts/dev-test-wine.sh [seconds]
#
# Environment:
#   NTPL_WINE         wine binary (default: wine from PATH)
#   NTPL_TEST_PREFIX  reuse a prefix instead of a temporary one
#   NTPL_PORT         port for the test (default: 47899)
#   KEEP_PREFIX=1     do not delete the prefix afterwards

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WINE_BIN="${NTPL_WINE:-wine}"
PREFIX="${NTPL_TEST_PREFIX:-$(mktemp -d /tmp/ntpl-wine.XXXXXX)}"
PORT="${NTPL_PORT:-47899}"
RUN_SECONDS="${1:-20}"

export WINEPREFIX="$PREFIX"
export WINEDEBUG="${WINEDEBUG:--all}"
export WINEDLLOVERRIDES="${WINEDLLOVERRIDES:-mscoree=;mshtml=}"
export NTPL_PORT="$PORT"
export NTPL_LOG='C:\ntpl-test.log'

FAKE_PID=""

cleanup() {
    if [[ -n "$FAKE_PID" ]]; then
        kill "$FAKE_PID" 2>/dev/null || true
    fi
    WINEPREFIX="$PREFIX" "$WINE_BIN" wineserver -k >/dev/null 2>&1 || true
    WINEPREFIX="$PREFIX" "$WINE_BIN" wineserver -w >/dev/null 2>&1 || true
    if [[ "${KEEP_PREFIX:-0}" != "1" ]]; then
        rm -rf "$PREFIX"
    else
        echo "prefix kept: $PREFIX"
    fi
}
trap cleanup EXIT

echo "[1/6] prefix: $PREFIX"
"$WINE_BIN" wineboot -u >/dev/null 2>&1 || true

echo "[2/6] hook stub test (real hooks.S, no game)"
"$WINE_BIN" "$ROOT/build/HookStubTest.exe"

echo "[3/6] starting a target named NostaleClientX.exe for ${RUN_SECONDS}s"
TARGET_DIR="$PREFIX/drive_c/ntpl-test"
mkdir -p "$TARGET_DIR"
cp "$ROOT/build/FakeGame.exe" "$TARGET_DIR/NostaleClientX.exe"
"$WINE_BIN" "$TARGET_DIR/NostaleClientX.exe" --no-load --seconds "$RUN_SECONDS" &
FAKE_PID=$!
sleep 2

echo "[4/6] running the real Injector.exe --all"
DLL_WIN="$(WINEPREFIX="$PREFIX" winepath -w "$ROOT/build/PacketLogger.dll")"
"$WINE_BIN" "$ROOT/build/Injector.exe" --all --dll "$DLL_WIN"
sleep 4

echo "[5/6] ntpl status"
PYTHONPATH="$ROOT/host" python3 -m ntpl status --port "$PORT" --timeout 10

echo
echo "[6/6] injection path (expected to fail: FakeGame has no hooks)"
PYTHONPATH="$ROOT/host" python3 -m ntpl send --port "$PORT" "walk 1 2 3 4" || true

echo
echo "--- DLL log ---"
cat "$PREFIX/drive_c/ntpl-test.log" 2>/dev/null || echo "(no log file)"
