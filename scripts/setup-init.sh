#!/usr/bin/env bash
set -eu

DRY_RUN=0
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --dry-run)
      DRY_RUN=1
      ;;
    --force)
      FORCE=1
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

mkdir_if_needed() {
  target="$1"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY-RUN: mkdir -p $target"
  else
    mkdir -p "$target"
    echo "OK: ensured $target"
  fi
}

ensure_copy_hint() {
  src="$1"
  dest="$2"
  if [ -e "$dest" ] && [ "$FORCE" -ne 1 ]; then
    echo "OK: existing file kept $dest"
    return
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY-RUN: cp $src $dest"
  else
    cp "$src" "$dest"
    echo "OK: wrote $dest from $src"
  fi
}

mkdir_if_needed "$REPO_ROOT/bin"
mkdir_if_needed "$REPO_ROOT/www"
mkdir_if_needed "$REPO_ROOT/config"
mkdir_if_needed "$REPO_ROOT/state"
mkdir_if_needed "$REPO_ROOT/state/releases"
mkdir_if_needed "$REPO_ROOT/scripts"
mkdir_if_needed "$REPO_ROOT/docs/contracts"
mkdir_if_needed "$REPO_ROOT/tests"

ensure_copy_hint "$REPO_ROOT/config/devices.example.json" "$REPO_ROOT/config/devices.local.json"

echo "INFO: runtime secrets must remain outside the repo"
echo "INFO: recommended secret env file path: /etc/homelabmon/monitor.env"
