#!/usr/bin/env bash
set -eu

DRY_RUN=0
if [ "$#" -gt 1 ]; then
  echo "Usage: $0 [--dry-run]" >&2
  exit 1
fi
if [ "$#" -eq 1 ]; then
  if [ "$1" = "--dry-run" ]; then
    DRY_RUN=1
  else
    echo "Unknown argument: $1" >&2
    exit 1
  fi
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

fail() {
  echo "ERROR: $1" >&2
  exit 1
}

require_dir() {
  [ -d "$1" ] || fail "missing directory: $1"
}

require_file() {
  [ -f "$1" ] || fail "missing file: $1"
}

require_readable() {
  [ -r "$1" ] || fail "file is not readable: $1"
}

validate_json() {
  path="$1"
  python3 - "$path" <<'PY' || fail "invalid JSON: $path"
import json
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        json.load(handle)
except Exception as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)
PY
}

require_dir "$REPO_ROOT/bin"
require_dir "$REPO_ROOT/www"
require_dir "$REPO_ROOT/config"
require_dir "$REPO_ROOT/state"
require_dir "$REPO_ROOT/scripts"
require_dir "$REPO_ROOT/docs/contracts"
require_dir "$REPO_ROOT/tests"

echo "OK: required directories present"

require_file "$REPO_ROOT/README.md"
require_file "$REPO_ROOT/.gitignore"
require_file "$REPO_ROOT/LICENSE"
require_file "$REPO_ROOT/CONTRIBUTING.md"
require_file "$REPO_ROOT/SECURITY.md"
require_file "$REPO_ROOT/CODE_OF_CONDUCT.md"
require_file "$REPO_ROOT/config/config.schema.json"
require_file "$REPO_ROOT/config/devices.example.json"
require_file "$REPO_ROOT/config/env.example"
require_file "$REPO_ROOT/.github/workflows/ci.yml"
require_file "$REPO_ROOT/.github/ISSUE_TEMPLATE/bug_report.md"
require_file "$REPO_ROOT/.github/ISSUE_TEMPLATE/feature_request.md"
require_file "$REPO_ROOT/.github/pull_request_template.md"

echo "OK: contract files found: config/config.schema.json, config/devices.example.json"

validate_json "$REPO_ROOT/config/config.schema.json"
validate_json "$REPO_ROOT/config/devices.example.json"

echo "OK: JSON files parse successfully"

grep -q '"instance_name"' "$REPO_ROOT/config/config.schema.json" || fail "schema missing instance_name"
grep -q '"site_title"' "$REPO_ROOT/config/config.schema.json" || fail "schema missing site_title"
grep -q '"dashboard_public_url"' "$REPO_ROOT/config/config.schema.json" || fail "schema missing dashboard_public_url"
grep -q '"feature_flags"' "$REPO_ROOT/config/config.schema.json" || fail "schema missing feature_flags"
grep -q '"ai"' "$REPO_ROOT/config/config.schema.json" || fail "schema missing ai"
grep -q '"devices"' "$REPO_ROOT/config/config.schema.json" || fail "schema missing devices"

echo "OK: schema keys present for v1 contract"

grep -q '^HOMELABMON_SECRET_ENV_FILE=' "$REPO_ROOT/config/env.example" || fail "env example missing secret env path"
grep -q '^HOMELABMON_AI_API_KEY_FILE=' "$REPO_ROOT/config/env.example" || fail "env example missing AI key file path"
grep -q '^AI_API_KEY=__INJECT_AT_RUNTIME_ONLY__$' "$REPO_ROOT/config/env.example" || fail "env example missing runtime-only secret placeholder"

echo "OK: env placeholders present"

require_readable "$REPO_ROOT/scripts/setup-init.sh"
require_readable "$REPO_ROOT/scripts/setup-apply.sh"
require_readable "$REPO_ROOT/scripts/setup-verify.sh"
require_readable "$REPO_ROOT/scripts/deploy.sh"
require_readable "$REPO_ROOT/scripts/rollback.sh"

echo "OK: scripts are readable for bash invocation"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "INFO: dry-run verification completed without mutating repository state"
else
  echo "INFO: verification completed"
fi
