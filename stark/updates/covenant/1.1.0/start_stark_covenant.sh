#!/bin/sh
# Stark Covenant launcher (macOS / Linux): needs python3 >= 3.10 and OpenSSL libcrypto for encryption.
APP="$(cd "$(dirname "$0")" && pwd)"
cd "$APP" || exit 1
PY="$(command -v python3 || command -v python)"
[ -n "$PY" ] || { echo "Stark Covenant needs Python 3.10+ (https://www.python.org/downloads/)"; exit 9; }
"$PY" -c 'import sys;sys.exit(sys.version_info<(3,10))' || { echo "Python 3.10+ required"; exit 9; }
if [ -f "$APP/auto_updater.py" ]; then
  exec "$PY" "$APP/auto_updater.py" launch --app-dir "$APP" -- "$APP/clinic_app.py"
fi
exec "$PY" "$APP/clinic_app.py"