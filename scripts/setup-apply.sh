#!/usr/bin/env bash
set -eu

DRY_RUN=0
ENV_FILE=""
CONFIG_FILE=""

usage() {
  echo "Usage: $0 [--dry-run] [--env-file PATH] [--config-file PATH]" >&2
}

require_value() {
  flag="$1"
  if [ "$#" -lt 2 ]; then
    usage
    exit 2
  fi
  case "$2" in
    --*)
      usage
      exit 2
      ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      ;;
    --env-file)
      require_value "$@"
      shift
      ENV_FILE="$1"
      ;;
    --config-file)
      require_value "$@"
      shift
      CONFIG_FILE="$1"
      ;;
    *)
      usage
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift
done

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "$ENV_FILE" ]; then
  ENV_FILE="$REPO_ROOT/config/env.example"
fi
if [ -z "$CONFIG_FILE" ]; then
  if [ -f "$REPO_ROOT/config/devices.local.json" ]; then
    CONFIG_FILE="$REPO_ROOT/config/devices.local.json"
  else
    CONFIG_FILE="$REPO_ROOT/config/devices.example.json"
  fi
fi

[ -r "$ENV_FILE" ] || {
  echo "ERROR: env file is not readable: $ENV_FILE" >&2
  exit 1
}
[ -r "$CONFIG_FILE" ] || {
  echo "ERROR: config file is not readable: $CONFIG_FILE" >&2
  exit 1
}

bash "$SCRIPT_DIR/setup-verify.sh" --dry-run >/dev/null

APPLY_DIR="$REPO_ROOT/state/runtime"
STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
MANIFEST="$APPLY_DIR/last-apply.txt"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY-RUN: verify source files"
  echo "DRY-RUN: use env file $ENV_FILE"
  echo "DRY-RUN: use config file $CONFIG_FILE"
  echo "DRY-RUN: write apply manifest $MANIFEST"
  exit 0
fi

mkdir -p "$APPLY_DIR"
cat > "$MANIFEST" <<MANIFEST
applied_at=$STAMP
env_file=$ENV_FILE
config_file=$CONFIG_FILE
secret_policy=runtime_only
MANIFEST

echo "OK: apply manifest written to $MANIFEST"
echo "INFO: no secrets were copied into the repository"
