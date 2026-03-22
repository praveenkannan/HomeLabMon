#!/usr/bin/env bash
set -eu

DRY_RUN=0
TARGET=""

usage() {
  echo "Usage: $0 [--dry-run] [--to RELEASE_ID]" >&2
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
    --to)
      require_value "$@"
      shift
      TARGET="$1"
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
CURRENT_FILE="$RELEASE_DIR/current"
PREVIOUS_FILE="$RELEASE_DIR/previous"

if [ -n "$TARGET" ]; then
  validate_release_id "$TARGET"
  [ -f "$RELEASE_DIR/$TARGET.manifest" ] || {
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "DRY-RUN: target manifest not present yet: $RELEASE_DIR/$TARGET.manifest"
      exit 0
    fi
    echo "Missing target release manifest: $RELEASE_DIR/$TARGET.manifest" >&2
    exit 1
  }
else
  [ -f "$PREVIOUS_FILE" ] || {
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "DRY-RUN: no previous release recorded"
      exit 0
    fi
    echo "No previous release recorded" >&2
    exit 1
  }
  TARGET=$(cat "$PREVIOUS_FILE")
  validate_release_id "$TARGET"
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY-RUN: rollback target $TARGET"
  echo "DRY-RUN: update $CURRENT_FILE"
  exit 0
fi

CURRENT_VALUE=""
if [ -f "$CURRENT_FILE" ]; then
  CURRENT_VALUE=$(cat "$CURRENT_FILE")
fi
printf '%s\n' "$CURRENT_VALUE" > "$PREVIOUS_FILE"
printf '%s\n' "$TARGET" > "$CURRENT_FILE"

echo "OK: rollback placeholder switched current release to $TARGET"
