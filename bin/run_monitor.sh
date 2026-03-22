#!/bin/bash
set -euo pipefail
LOCKFILE=/tmp/homelabmon.lock
ROOT="${HOMELABMON_ROOT:-${PI_MONITOR_ROOT:-/opt/homelabmon}}"
FLOCK_BIN="${FLOCK_BIN:-$(command -v flock || true)}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"

if [ -z "$FLOCK_BIN" ] || [ -z "$PYTHON_BIN" ]; then
  echo "ERROR: required runtime binaries not found (flock, python3)" >&2
  exit 1
fi

exec "$FLOCK_BIN" -n "$LOCKFILE" /bin/bash -c '
  set -a
  ROOT="'"$ROOT"'"
  HOMELABMON_ROOT="$ROOT"
  cd "$ROOT"
  if [ -f "$ROOT/config/monitor.env" ]; then
    . "$ROOT/config/monitor.env"
  fi
  set +a
  exec "'"$PYTHON_BIN"'" "$ROOT/bin/check_devices.py"
'
