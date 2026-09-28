#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MODE="${1:-run}"
APP="$ROOT/dist/macos/Neo-Tracker.app"
case "$MODE" in
  run|--build-only|--verify|--debug|--logs|--telemetry) ;;
  *) echo "Usage: $0 [--build-only|--verify|--debug|--logs|--telemetry]" >&2; exit 2 ;;
esac
# Do not terminate a user's unsaved project just to replace a bundle.
if pgrep -f "^$APP/Contents/MacOS/Neo-Tracker" >/dev/null; then
  echo "Close the build-directory app before rebuilding; your project was not interrupted." >&2
  exit 1
fi
cd "$ROOT"
"$PYTHON_BIN" -m PyInstaller --noconfirm --distpath dist/macos --workpath build/macos packaging/macos/Neo-Tracker.spec
codesign --verify --deep --strict "$APP"
case "$MODE" in
  --build-only) ;;
  --verify)
    "$APP/Contents/MacOS/Neo-Tracker" --self-test "$ROOT/build/macos/bundle-smoke.json"
    ;;
  --debug) lldb -- "$APP/Contents/MacOS/Neo-Tracker" ;;
  --logs|--telemetry)
    /usr/bin/open -n "$APP"
    /usr/bin/log stream --info --style compact --predicate 'process == "Neo-Tracker"'
    ;;
  run) /usr/bin/open -n "$APP" ;;
esac
