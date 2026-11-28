#!/bin/bash
# Stark World Engine - macOS launcher. Double-click it (first time: right-click > Open > Open, because it is
# not from the App Store). Finds Python 3.10+ and starts studio_app.py, which opens your browser at
# http://127.0.0.1:<port>/ by itself. Writes launcher.log next to this file.
# Optional: GS_NO_BROWSER=1 to skip opening the browser.
cd -- "$(dirname -- "$0")" || exit 1
LOG="$PWD/launcher.log"
log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG" 2>/dev/null; }

log "==== launcher start ($PWD)"
echo
echo "  ==============================================="
echo "             S T A R K   W O R L D   E N G I N E"
echo "  ==============================================="
echo

if [ ! -f studio_app.py ]; then
  echo "  Stark World Engine needs to be unzipped first: double-click the .zip, then double-click"
  echo "  start_stark_world_engine.command inside the folder it makes."
  log "studio_app.py missing"
  exit 1
fi

ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; }

PY=""
# python.org, Homebrew (Apple silicon + Intel) and PATH installs first.
for c in /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 \
         "$(command -v python3.14 2>/dev/null)" "$(command -v python3.13 2>/dev/null)" \
         "$(command -v python3.12 2>/dev/null)" "$(command -v python3.11 2>/dev/null)" \
         "$(command -v python3.10 2>/dev/null)"; do
  if [ -n "$c" ] && [ -x "$c" ] && ok "$c"; then PY="$c"; break; fi
done
# /usr/bin/python3 is only a stub until Apple's Command Line Tools are installed; running the stub pops
# an install dialog, so only use it when the tools are really there.
if [ -z "$PY" ]; then
  c="$(command -v python3 2>/dev/null)"
  if [ -n "$c" ] && { [ "$c" != "/usr/bin/python3" ] || xcode-select -p >/dev/null 2>&1; } && ok "$c"; then PY="$c"; fi
fi

if [ -z "$PY" ]; then
  log "no Python 3.10+ found"
  echo "  Stark World Engine runs on Python 3.10 or newer (free), which isn't installed on this Mac yet."
  echo
  echo "  1. Open https://www.python.org/downloads/ and click the big yellow Download button."
  echo "  2. Open the downloaded file and click through the installer."
  echo "  3. Double-click start_stark_world_engine.command again."
  echo
  open "https://www.python.org/downloads/" >/dev/null 2>&1
  read -r -p "  (python.org is open in your browser. Press Return to close this window.) " _
  exit 1
fi

V="$("$PY" -c 'import sys; print(sys.version.split()[0])')"
log "using $PY (Python $V)"
echo "  Found Python $V."
echo
# Setup doctor: 0 start, 2 blocker shown, 3 already running (browser opened), 4 copied + started elsewhere.
# Any other code means the doctor itself broke: start anyway.
if [ -f doctor.py ]; then
  echo "  Checking your setup..."
  "$PY" -u doctor.py --launch
  DRC=$?
  log "doctor exit code $DRC (details in doctor.log)"
  echo
  case "$DRC" in
    2) read -r -p "  Press Return to close this window. " _; exit 1 ;;
    3) echo "  Stark World Engine is already running, so this window is not needed."; exit 0 ;;
    4) exit 0 ;;
  esac
fi
echo "  Starting Stark World Engine... your browser will open in a moment."
echo "  Keep this window open while you create. Press Ctrl+C or close it to stop."
echo
ARGS=""
[ "${GS_NO_BROWSER:-}" = "1" ] && ARGS="--no-browser"
[ -f stark_update/updater.py ] && "$PY" -u stark_update/updater.py boot-check 2>>"$LOG"
"$PY" -u studio_app.py $ARGS 2>>"$LOG"
RC=$?
log "studio_app.py exited with code $RC"
if [ "$RC" -ne 0 ] && [ "$RC" -ne 130 ]; then
  echo
  echo "  The studio stopped unexpectedly. The last lines of launcher.log say:"
  tail -n 8 "$LOG" | sed 's/^/     /'
  read -r -p "  Press Return to close this window. " _
fi
exit 0
