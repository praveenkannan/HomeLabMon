#!/usr/bin/env bash
set -eu

DRY_RUN=0
VERSION=""

usage() {
  echo "Usage: $0 [--dry-run] [--version RELEASE_ID]" >&2
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

validate_release_id() {
  value="$1"
  case "$value" in
    ""|*/*|*..*)
      echo "ERROR: invalid release identifier: $value" >&2
      exit 1
      ;;
  esac
  case "$value" in
    *[!A-Za-z0-9._-]*)
      echo "ERROR: invalid release identifier: $value" >&2
      exit 1
      ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      ;;
    --version)
      require_value "$@"
      shift
      VERSION="$1"
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
RELEASE_DIR="$REPO_ROOT/state/releases"
STAMP=$(date -u +%Y%m%d%H%M%S)
RELEASE_ID=${VERSION:-release-$STAMP}
validate_release_id "$RELEASE_ID"
MANIFEST="$RELEASE_DIR/$RELEASE_ID.manifest"
CURRENT_FILE="$REPO_ROOT/state/releases/current"
PREVIOUS_FILE="$REPO_ROOT/state/releases/previous"

bash "$SCRIPT_DIR/setup-verify.sh" --dry-run >/dev/null

if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY-RUN: verify repository skeleton"
  echo "DRY-RUN: create release manifest $MANIFEST"
  echo "DRY-RUN: update $CURRENT_FILE and $PREVIOUS_FILE"
  exit 0
fi

mkdir -p "$RELEASE_DIR"
CURRENT_VALUE=""
if [ -f "$CURRENT_FILE" ]; then
  CURRENT_VALUE=$(cat "$CURRENT_FILE")
  printf '%s\n' "$CURRENT_VALUE" > "$PREVIOUS_FILE"
fi

cat > "$MANIFEST" <<MANIFEST
release_id=$RELEASE_ID
created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
source_root=$REPO_ROOT
previous_release=$CURRENT_VALUE
MANIFEST

printf '%s\n' "$RELEASE_ID" > "$CURRENT_FILE"

echo "OK: deployed release placeholder $RELEASE_ID"
echo "INFO: manifest written to $MANIFEST"
