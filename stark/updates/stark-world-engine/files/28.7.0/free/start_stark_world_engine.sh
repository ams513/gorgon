#!/usr/bin/env bash
# Stark World Engine - Linux launcher.  Run:  bash start_stark_world_engine.sh   (or double-click if your file
# manager runs scripts). Finds Python 3.10+ and starts studio_app.py, which opens your browser at
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
  echo "  Stark World Engine needs to be unzipped first: extract the .zip, then run this file"
  echo "  from inside the extracted folder."
  log "studio_app.py missing"
  exit 1
fi

PY=""
for c in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    PY="$(command -v "$c")"; break
  fi
done

if [ -z "$PY" ]; then
  log "no Python 3.10+ found"
  echo "  Stark World Engine runs on Python 3.10 or newer (free), which isn't installed yet."
  echo
  echo "  Install it with your package manager, for example:"
  echo "     Ubuntu / Debian / Mint:  sudo apt install python3"
  echo "     Fedora:                  sudo dnf install python3"
  echo "     Arch:                    sudo pacman -S python"
  echo "  or download it from https://www.python.org/downloads/"
  echo "  Then run this file again."
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
    2) exit 1 ;;
    3) echo "  Stark World Engine is already running, so this window is not needed."; exit 0 ;;
    4) exit 0 ;;
  esac
fi
echo "  Starting Stark World Engine... your browser will open in a moment."
echo "  Keep this window open while you create. Press Ctrl+C here to stop."
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
fi
exit 0
