#!/usr/bin/env bash
set -euo pipefail

HOURS=24
REPORT_ONLY=0
OUT=""
ROOT="${HOMELABMON_ROOT:-${PI_MONITOR_ROOT:-/opt/homelabmon}}"
STATUS_BUDGET_BYTES="${HOMELABMON_STATUS_BUDGET_BYTES:-${PI_MONITOR_STATUS_BUDGET_BYTES:-122880}}"
INCIDENTS_BUDGET_BYTES="${HOMELABMON_INCIDENTS_BUDGET_BYTES:-${PI_MONITOR_INCIDENTS_BUDGET_BYTES:-81920}}"

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)

usage() {
  cat <<'EOF'
Usage: run_burnin_validation.sh [--hours N] [--out PATH] [--root PATH] [--report-only]
                                [--status-budget-bytes N] [--incidents-budget-bytes N]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hours)
      HOURS="$2"
      shift 2
      ;;
    --out)
      OUT="$2"
      shift 2
      ;;
    --root)
      ROOT="$2"
      shift 2
      ;;
    --status-budget-bytes)
      STATUS_BUDGET_BYTES="$2"
      shift 2
      ;;
    --incidents-budget-bytes)
      INCIDENTS_BUDGET_BYTES="$2"
      shift 2
      ;;
    --report-only)
      REPORT_ONLY=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$OUT" ]]; then
  OUT="${ROOT}/logs/burnin_validation_$(date -u +%Y%m%d_%H%M%S).txt"
fi

mkdir -p "${ROOT}/logs"
if [[ -f "${ROOT}/config/monitor.env" ]]; then
  set -a
  . "${ROOT}/config/monitor.env"
  set +a
fi

SERVICE_NAME="${HOMELABMON_SERVICE_NAME:-${PI_MONITOR_SERVICE_NAME:-${HOMELABMON_SECURITY_SERVICE_NAME:-${PI_MONITOR_SECURITY_SERVICE_NAME:-homelabmon-status}}}}"

export HOMELABMON_ALERT_DRY_RUN=1

{
  echo "Burn-in validation report (UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "Window hours: $HOURS"
  echo "Host: $(hostname)"
  echo "Runtime root: $ROOT"
  echo "Dashboard: ${HOMELABMON_DASHBOARD_URL:-${PI_MONITOR_DASHBOARD_URL:-}}"
  echo "Operational budgets: status=${STATUS_BUDGET_BYTES}B incidents=${INCIDENTS_BUDGET_BYTES}B"
  echo
} > "$OUT"

if command -v systemctl >/dev/null 2>&1; then
  {
    echo "== Service health =="
    systemctl is-active "$SERVICE_NAME" 2>&1 || true
    echo
  } >> "$OUT"
else
  {
    echo "== Service health =="
    echo "systemctl_unavailable=1"
    echo
  } >> "$OUT"
fi

{
  echo "== Status snapshot =="
  python3 - "$ROOT" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
status_path = root / "state" / "status.json"
read_model_status = root / "state" / "read_model" / "status.json"

if not status_path.exists():
    print("status_json=missing")
else:
    state = json.loads(status_path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        print("status_json=invalid_type")
    else:
        up = sum(1 for v in state.values() if isinstance(v, dict) and v.get("healthy") and v.get("reason") != "disabled")
        down = sum(1 for v in state.values() if isinstance(v, dict) and not v.get("healthy"))
        disabled = sum(1 for v in state.values() if isinstance(v, dict) and v.get("reason") == "disabled")
        print(f"status_devices={len(state)}")
        print(f"status_up={up} status_down={down} status_disabled={disabled}")

if read_model_status.exists():
    payload = json.loads(read_model_status.read_text(encoding="utf-8"))
    print(f"read_model_status_keys={','.join(sorted(payload.keys()))}")
else:
    print("read_model_status=missing")
PY
  echo
} >> "$OUT"

{
  echo "== Last ${HOURS}h history summary =="
  python3 - "$ROOT" "$HOURS" <<'PY'
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

root = Path(sys.argv[1])
hours = int(sys.argv[2])
cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

history_files = sorted((root / "state" / "history").glob("*.jsonl"))
legacy = root / "state" / "history.jsonl"
if legacy.exists():
    history_files.append(legacy)

rows = []
for path in history_files:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            ts = datetime.fromisoformat(row.get("timestamp", ""))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                rows.append(row)
        except Exception:
            continue

print(f"history_rows={len(rows)}")
stats = {}
for row in rows:
    state = row.get("state", {})
    if not isinstance(state, dict):
        continue
    for name, item in state.items():
        if not isinstance(item, dict):
            continue
        slot = stats.setdefault(name, {"checks": 0, "down": 0})
        slot["checks"] += 1
        if not item.get("healthy"):
            slot["down"] += 1

for name in sorted(stats):
    slot = stats[name]
    uptime = round(((slot["checks"] - slot["down"]) / slot["checks"]) * 100, 1) if slot["checks"] else 0.0
    print(f"{name}: checks={slot['checks']} down={slot['down']} uptime={uptime}%")
PY
  echo
} >> "$OUT"

{
  echo "== Incident / flap / heat summary (${HOURS}h) =="
  python3 - "$ROOT" "$HOURS" <<'PY'
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

root = Path(sys.argv[1])
hours = int(sys.argv[2])
cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

incident_payload = root / "state" / "read_model" / "incidents.json"
incident_files = sorted((root / "state" / "incidents").glob("*.jsonl"))
legacy = root / "state" / "incidents.jsonl"
if legacy.exists():
    incident_files.append(legacy)

items = []
if incident_payload.exists():
    try:
        payload = json.loads(incident_payload.read_text(encoding="utf-8"))
        if isinstance(payload.get("items"), list):
            items = payload["items"]
    except Exception:
        pass

if not items:
    for path in incident_files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                continue

recent = []
for item in items:
    ts_raw = item.get("timestamp")
    if not isinstance(ts_raw, str):
        continue
    try:
        ts = datetime.fromisoformat(ts_raw)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            recent.append(item)
    except Exception:
        continue

event_counts = {}
flap_count = 0
for item in recent:
    event = str(item.get("event", "UNKNOWN"))
    event_counts[event] = event_counts.get(event, 0) + 1
    flap = item.get("flap")
    if isinstance(flap, dict) and flap.get("detected") is True:
        flap_count += 1

print(f"incident_rows={len(recent)}")
print(f"incident_flap_detected={flap_count}")
for event in sorted(event_counts):
    print(f"incident_event_{event}={event_counts[event]}")

status_payload = root / "state" / "read_model" / "status.json"
heat_devices = 0
if status_payload.exists():
    try:
        status = json.loads(status_payload.read_text(encoding="utf-8"))
        state = status.get("state", {})
        if isinstance(state, dict):
            for value in state.values():
                if isinstance(value, dict) and ("heat" in value or "temperature" in value):
                    heat_devices += 1
    except Exception:
        pass
print(f"heat_fields_present_devices={heat_devices}")
if heat_devices == 0:
    print("heat_note=runtime currently has no per-device heat fields to validate")
PY
  echo
} >> "$OUT"

validation_failures=0

{
  echo "== Security verification =="
} >> "$OUT"
if [[ -x "${SCRIPT_DIR}/security_verify.sh" ]]; then
  security_args=()
  if [[ -n "${HOMELABMON_SECURITY_SERVICE_NAME:-${PI_MONITOR_SECURITY_SERVICE_NAME:-}}" ]]; then
    security_args+=(--service-name "${HOMELABMON_SECURITY_SERVICE_NAME:-${PI_MONITOR_SECURITY_SERVICE_NAME}}")
  fi
  if [[ -n "${HOMELABMON_SECURITY_UNIT_FILE:-${PI_MONITOR_SECURITY_UNIT_FILE:-}}" ]]; then
    security_args+=(--unit-file "${HOMELABMON_SECURITY_UNIT_FILE:-${PI_MONITOR_SECURITY_UNIT_FILE}}")
  fi
  if [[ -n "${HOMELABMON_SECURITY_SSHD_CONFIG:-${PI_MONITOR_SECURITY_SSHD_CONFIG:-}}" ]]; then
    security_args+=(--sshd-config "${HOMELABMON_SECURITY_SSHD_CONFIG:-${PI_MONITOR_SECURITY_SSHD_CONFIG}}")
  fi
  if [[ -n "${HOMELABMON_SECURITY_EXPECT_BIND_HOST:-${PI_MONITOR_SECURITY_EXPECT_BIND_HOST:-}}" ]]; then
    security_args+=(--expect-bind-host "${HOMELABMON_SECURITY_EXPECT_BIND_HOST:-${PI_MONITOR_SECURITY_EXPECT_BIND_HOST}}")
  fi
  if [[ -n "${HOMELABMON_SECURITY_EXPECT_BIND_PORT:-${PI_MONITOR_SECURITY_EXPECT_BIND_PORT:-}}" ]]; then
    security_args+=(--expect-bind-port "${HOMELABMON_SECURITY_EXPECT_BIND_PORT:-${PI_MONITOR_SECURITY_EXPECT_BIND_PORT}}")
  fi
  if [[ -n "${HOMELABMON_SECURITY_SYSTEMCTL_ENV_FILE:-${PI_MONITOR_SECURITY_SYSTEMCTL_ENV_FILE:-}}" ]]; then
    security_args+=(--systemctl-env-file "${HOMELABMON_SECURITY_SYSTEMCTL_ENV_FILE:-${PI_MONITOR_SECURITY_SYSTEMCTL_ENV_FILE}}")
  fi
  if [[ -n "${HOMELABMON_SECURITY_LISTENERS_FILE:-${PI_MONITOR_SECURITY_LISTENERS_FILE:-}}" ]]; then
    security_args+=(--listeners-file "${HOMELABMON_SECURITY_LISTENERS_FILE:-${PI_MONITOR_SECURITY_LISTENERS_FILE}}")
  fi
  if [[ -n "${HOMELABMON_SECURITY_UFW_STATUS_FILE:-${PI_MONITOR_SECURITY_UFW_STATUS_FILE:-}}" ]]; then
    security_args+=(--ufw-status-file "${HOMELABMON_SECURITY_UFW_STATUS_FILE:-${PI_MONITOR_SECURITY_UFW_STATUS_FILE}}")
  fi

  set +e
  "${SCRIPT_DIR}/security_verify.sh" "${security_args[@]}" >> "$OUT" 2>&1
  security_rc=$?
  set -e
  echo "security_verify_rc=${security_rc}" >> "$OUT"
  echo >> "$OUT"
  if [[ "$security_rc" -ne 0 ]]; then
    validation_failures=$((validation_failures + 1))
  fi
else
  echo "security_verify=missing_executable" >> "$OUT"
  echo >> "$OUT"
  validation_failures=$((validation_failures + 1))
fi

{
  echo "== Contract verification =="
} >> "$OUT"
if [[ -x "${SCRIPT_DIR}/contract_verify.py" ]]; then
  set +e
  python3 "${SCRIPT_DIR}/contract_verify.py" \
    --root "$ROOT" \
    --status-budget-bytes "$STATUS_BUDGET_BYTES" \
    --incidents-budget-bytes "$INCIDENTS_BUDGET_BYTES" >> "$OUT" 2>&1
  contract_rc=$?
  set -e
  echo "contract_verify_rc=${contract_rc}" >> "$OUT"
  echo >> "$OUT"
  if [[ "$contract_rc" -ne 0 ]]; then
    validation_failures=$((validation_failures + 1))
  fi
else
  echo "contract_verify=missing_executable" >> "$OUT"
  echo >> "$OUT"
  validation_failures=$((validation_failures + 1))
fi

if [[ "$REPORT_ONLY" -eq 0 ]]; then
  {
    echo "== Safe command validations (dry-run) =="
    if [[ -x "${ROOT}/bin/cronwatcher.py" ]]; then
      echo "$ python3 ${ROOT}/bin/cronwatcher.py --daily --dry-run --force"
      python3 "${ROOT}/bin/cronwatcher.py" --daily --dry-run --force
      echo
      echo "$ python3 ${ROOT}/bin/cronwatcher.py --weekly --dry-run --force"
      python3 "${ROOT}/bin/cronwatcher.py" --weekly --dry-run --force
      echo
    else
      echo "cronwatcher_path_missing=${ROOT}/bin/cronwatcher.py"
      echo
    fi

    if [[ -x "${ROOT}/bin/send_alert.py" ]]; then
      echo "$ python3 ${ROOT}/bin/send_alert.py burnin OK \"burn-in validation ping\" --dry-run --force"
      python3 "${ROOT}/bin/send_alert.py" burnin OK "burn-in validation ping" --dry-run --force
    else
      echo "send_alert_path_missing=${ROOT}/bin/send_alert.py"
    fi
    echo
  } >> "$OUT" 2>&1
fi

if [[ "$validation_failures" -eq 0 ]]; then
  echo "overall_gate=PASS" >> "$OUT"
else
  echo "overall_gate=FAIL failing_sections=${validation_failures}" >> "$OUT"
fi

echo "Report written: $OUT"
cat "$OUT"

if [[ "$validation_failures" -eq 0 ]]; then
  exit 0
fi
exit 1
