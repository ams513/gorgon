#!/bin/sh
# Stark Quill launcher (macOS / Linux): needs python3 >= 3.10.
APP="$(cd "$(dirname "$0")" && pwd)"
cd "$APP" || exit 1
PY="$(command -v python3 || command -v python)"
[ -n "$PY" ] || { echo "Stark Quill needs Python 3.10+ (https://www.python.org/downloads/)"; exit 9; }
"$PY" -c 'import sys;sys.exit(sys.version_info<(3,10))' || { echo "Python 3.10+ required"; exit 9; }
exec "$PY" "$APP/auto_updater.py" launch --app-dir "$APP" -- "$APP/writer_app.py"