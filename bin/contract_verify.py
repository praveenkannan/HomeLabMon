#!/usr/bin/env python3
"""HomeLabMon contract/runtime verifier.

Validates repository contract artifacts and runtime read-model payloads using
lightweight checks intended for Raspberry Pi usage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONTRACT_FILES = (
    "api-v1.md",
    "incident-semantics.md",
    "temperature-contract.md",
    "ai-token-contract.md",
    "error-schema.json",
)

DEFAULT_ROOT = Path(os.getenv("HOMELABMON_ROOT", os.getenv("PI_MONITOR_ROOT", "/opt/homelabmon")))
DEFAULT_CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "docs" / "contracts"


@dataclass
class Finding:
    level: str
    code: str
    message: str


class Reporter:
    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def pass_(self, code: str, message: str) -> None:
        self.findings.append(Finding("PASS", code, message))

    def warn(self, code: str, message: str) -> None:
        self.findings.append(Finding("WARN", code, message))

    def fail(self, code: str, message: str) -> None:
        self.findings.append(Finding("FAIL", code, message))

    def count(self, level: str) -> int:
        return sum(1 for item in self.findings if item.level == level)

    def render(self) -> None:
        print("HomeLabMon contract verification")
        for item in self.findings:
            print(f"{item.level:4} {item.code}: {item.message}")
        print(
            f"Summary: pass={self.count('PASS')} "
            f"warn={self.count('WARN')} fail={self.count('FAIL')}"
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="verify HomeLabMon contract/runtime artifacts")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="runtime root (default: /opt/homelabmon)")
    parser.add_argument(
        "--contracts-dir",
        default=str(DEFAULT_CONTRACTS_DIR),
        help="contracts directory (default: repo/docs/contracts)",
    )
    parser.add_argument(
        "--status-budget-bytes",
        type=int,
        default=120 * 1024,
        help="operational budget for status payload",
    )
    parser.add_argument(
        "--incidents-budget-bytes",
        type=int,
        default=80 * 1024,
        help="operational budget for incidents payload",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat WARN findings as failures (non-zero exit)",
    )
    return parser.parse_args(argv)


def parse_json(path: Path, reporter: Reporter, code_prefix: str) -> dict[str, Any] | None:
    if not path.exists():
        reporter.fail(f"{code_prefix}_missing", f"missing file: {path}")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        reporter.fail(f"{code_prefix}_invalid_json", f"invalid JSON at {path}: {exc}")
        return None
    if not isinstance(data, dict):
        reporter.fail(f"{code_prefix}_invalid_type", f"top-level JSON in {path} is not an object")
        return None
    reporter.pass_(f"{code_prefix}_json_ok", f"JSON parse ok: {path}")
    return data


def parse_kib(cell: str) -> int | None:
    raw = cell.strip()
    if not raw:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*KiB", raw, re.IGNORECASE)
    if not match:
        return None
    return int(float(match.group(1)) * 1024)


def parse_response_budgets(api_contract_text: str) -> dict[str, int]:
    budgets: dict[str, int] = {}
    row_re = re.compile(
        r"^\|\s*`(?P<endpoint>(?:GET|POST)\s+/[^`]+)`\s*\|\s*(?P<request>[^|]+)\|\s*(?P<response>[^|]+)\|"
    )
    for line in api_contract_text.splitlines():
        match = row_re.match(line.strip())
        if not match:
            continue
        response_kib = parse_kib(match.group("response"))
        if response_kib is not None:
            budgets[match.group("endpoint")] = response_kib
    return budgets


def parse_iso(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        dt = datetime.fromisoformat(value)
    except Exception:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return True


def check_contract_docs(contracts_dir: Path, reporter: Reporter) -> dict[str, int]:
    for name in CONTRACT_FILES:
        path = contracts_dir / name
        if path.exists():
            reporter.pass_("contract_exists", f"found {path}")
        else:
            reporter.fail("contract_missing", f"missing required contract file: {path}")

    api_path = contracts_dir / "api-v1.md"
    api_text = api_path.read_text(encoding="utf-8") if api_path.exists() else ""
    if "/api/v1" in api_text:
        reporter.pass_("contract_api_base", "api-v1 contract includes /api/v1 base path")
    else:
        reporter.fail("contract_api_base", "api-v1 contract missing /api/v1 base path")
    if '"api_version": "1.0"' in api_text:
        reporter.pass_("contract_api_version", "api-v1 contract defines api_version=1.0")
    else:
        reporter.fail("contract_api_version", 'api-v1 contract missing "api_version": "1.0" requirement')

    budgets = parse_response_budgets(api_text)
    if budgets:
        reporter.pass_("contract_payload_budget", f"parsed {len(budgets)} payload budget rows from api-v1.md")
    else:
        reporter.warn("contract_payload_budget", "unable to parse payload budgets from api-v1.md")

    error_schema_path = contracts_dir / "error-schema.json"
    schema = parse_json(error_schema_path, reporter, "error_schema")
    if schema is not None:
        version = (
            schema.get("properties", {})
            .get("api_version", {})
            .get("const")
        )
        if version == "1.0":
            reporter.pass_("error_schema_version", "error-schema pins api_version const=1.0")
        else:
            reporter.fail("error_schema_version", "error-schema api_version const must be 1.0")
    return budgets


def check_payload_budget(
    path: Path,
    budget_bytes: int | None,
    endpoint: str,
    reporter: Reporter,
    code: str,
) -> None:
    if not path.exists():
        reporter.fail(f"{code}_missing", f"{endpoint} payload file missing: {path}")
        return
    size = path.stat().st_size
    if budget_bytes is None:
        reporter.warn(code, f"{endpoint} budget unavailable; observed payload size={size} bytes")
        return
    if size <= budget_bytes:
        reporter.pass_(code, f"{endpoint} payload={size} bytes <= budget={budget_bytes}")
    else:
        reporter.fail(code, f"{endpoint} payload={size} bytes exceeds budget={budget_bytes}")


def check_status_payload(status: dict[str, Any], reporter: Reporter) -> None:
    required = ("generated_at", "summary", "state")
    missing = [key for key in required if key not in status]
    if missing:
        reporter.fail("status_fields", f"status payload missing fields: {', '.join(missing)}")
    else:
        reporter.pass_("status_fields", "status payload has required fields")

    if not parse_iso(status.get("generated_at")):
        reporter.fail("status_generated_at", "status.generated_at must be an RFC3339 timestamp")
    else:
        reporter.pass_("status_generated_at", "status.generated_at format valid")

    summary = status.get("summary")
    if not isinstance(summary, dict):
        reporter.fail("status_summary", "status.summary must be an object")
    else:
        expected_keys = ("up", "down", "disabled", "devices")
        missing_keys = [key for key in expected_keys if key not in summary]
        if missing_keys:
            reporter.fail("status_summary", f"status.summary missing keys: {', '.join(missing_keys)}")
        else:
            reporter.pass_("status_summary", "status.summary includes up/down/disabled/devices")

    state = status.get("state")
    if not isinstance(state, dict):
        reporter.fail("status_state", "status.state must be an object map")
    else:
        reporter.pass_("status_state", f"status.state device count={len(state)}")

    heat_devices = 0
    if isinstance(state, dict):
        for value in state.values():
            if not isinstance(value, dict):
                continue
            if "heat" in value or "temperature" in value:
                heat_devices += 1

    if heat_devices > 0:
        reporter.pass_("status_heat", f"heat telemetry present for {heat_devices} device entries")
    else:
        reporter.warn(
            "status_heat",
            "no per-device heat fields found in status.state (allowed for current runtime, but limits heat validation)",
        )

    if {"api_version", "request_id", "generated_at", "data"}.issubset(status.keys()):
        reporter.pass_("status_envelope", "status payload matches API envelope shape")
    else:
        reporter.warn(
            "status_envelope",
            "status payload is read-model style, not full API v1 envelope (expected while /api/v1 layer is pending)",
        )


def check_incidents_payload(incidents: dict[str, Any], reporter: Reporter) -> None:
    required = ("generated_at", "items", "count")
    missing = [key for key in required if key not in incidents]
    if missing:
        reporter.fail("incidents_fields", f"incidents payload missing fields: {', '.join(missing)}")
        return
    reporter.pass_("incidents_fields", "incidents payload has required fields")

    if not parse_iso(incidents.get("generated_at")):
        reporter.fail("incidents_generated_at", "incidents.generated_at must be an RFC3339 timestamp")
    else:
        reporter.pass_("incidents_generated_at", "incidents.generated_at format valid")

    items = incidents.get("items")
    count = incidents.get("count")
    if not isinstance(items, list):
        reporter.fail("incidents_items", "incidents.items must be a list")
        return
    reporter.pass_("incidents_items", f"incidents.items entries={len(items)}")

    if not isinstance(count, int) or count < 0:
        reporter.fail("incidents_count", "incidents.count must be a non-negative integer")
    elif count < len(items):
        reporter.fail("incidents_count", f"incidents.count={count} is smaller than items length={len(items)}")
    else:
        reporter.pass_("incidents_count", f"incidents.count={count} is consistent with items length={len(items)}")

    flap_items = 0
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            reporter.fail("incident_entry_type", f"incident index={idx} is not an object")
            continue

        for key in ("incident_id", "timestamp", "device", "event", "reason", "flap"):
            if key not in item:
                reporter.fail("incident_entry_field", f"incident index={idx} missing field: {key}")

        if item.get("timestamp") and not parse_iso(item.get("timestamp")):
            reporter.fail("incident_timestamp", f"incident index={idx} has invalid timestamp")

        flap = item.get("flap")
        if not isinstance(flap, dict):
            reporter.fail("incident_flap", f"incident index={idx} flap must be an object")
            continue
        detected = flap.get("detected")
        transition_count = flap.get("transition_count")
        if not isinstance(detected, bool):
            reporter.fail("incident_flap", f"incident index={idx} flap.detected must be boolean")
        if not isinstance(transition_count, int) or transition_count < 0:
            reporter.fail("incident_flap", f"incident index={idx} flap.transition_count must be >= 0 integer")
        if detected:
            flap_items += 1

    reporter.pass_("incidents_flap_summary", f"flap-marked incidents={flap_items}")

    if {"api_version", "request_id", "generated_at", "data"}.issubset(incidents.keys()):
        reporter.pass_("incidents_envelope", "incidents payload matches API envelope shape")
    else:
        reporter.warn(
            "incidents_envelope",
            "incidents payload is read-model style, not full API v1 envelope (expected while /api/v1 layer is pending)",
        )


def check_rollups_payload(rollups: dict[str, Any], reporter: Reporter) -> None:
    if "generated_at" not in rollups:
        reporter.fail("rollups_fields", "rollups payload missing generated_at")
    elif parse_iso(rollups.get("generated_at")):
        reporter.pass_("rollups_generated_at", "rollups.generated_at format valid")
    else:
        reporter.fail("rollups_generated_at", "rollups.generated_at must be an RFC3339 timestamp")

    items = rollups.get("items")
    if isinstance(items, list):
        reporter.pass_("rollups_items", f"rollups.items entries={len(items)}")
    else:
        reporter.fail("rollups_items", "rollups.items must be a list")

    retention_days = rollups.get("retention_days")
    if isinstance(retention_days, int) and retention_days >= 0:
        reporter.pass_("rollups_retention", f"rollups.retention_days={retention_days}")
    else:
        reporter.warn("rollups_retention", "rollups.retention_days missing or invalid")


def check_http_layer_alignment(root: Path, reporter: Reporter) -> None:
    http_status = root / "bin" / "http_status.py"
    if not http_status.exists():
        reporter.warn("http_layer_path", f"runtime API script missing at {http_status}")
        return
    text = http_status.read_text(encoding="utf-8")
    if "/api/v1" in text:
        reporter.pass_("http_layer_version_path", "runtime API script references /api/v1 paths")
    else:
        reporter.warn(
            "http_layer_version_path",
            "runtime API script does not reference /api/v1 paths (still serving /api/* read-model endpoints)",
        )


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    reporter = Reporter()

    root = Path(args.root)
    contracts_dir = Path(args.contracts_dir)

    contract_budgets = check_contract_docs(contracts_dir, reporter)

    read_model_dir = root / "state" / "read_model"
    status_path = read_model_dir / "status.json"
    incidents_path = read_model_dir / "incidents.json"
    rollups_path = read_model_dir / "rollups.json"

    status = parse_json(status_path, reporter, "status_payload")
    incidents = parse_json(incidents_path, reporter, "incidents_payload")
    rollups = parse_json(rollups_path, reporter, "rollups_payload")

    status_contract_budget = contract_budgets.get("GET /devices")
    incidents_contract_budget = contract_budgets.get("GET /incidents")
    check_payload_budget(
        status_path,
        status_contract_budget,
        "GET /devices (contract)",
        reporter,
        "budget_contract_status",
    )
    check_payload_budget(
        incidents_path,
        incidents_contract_budget,
        "GET /incidents (contract)",
        reporter,
        "budget_contract_incidents",
    )
    check_payload_budget(
        status_path,
        int(args.status_budget_bytes),
        "status (operational)",
        reporter,
        "budget_ops_status",
    )
    check_payload_budget(
        incidents_path,
        int(args.incidents_budget_bytes),
        "incidents (operational)",
        reporter,
        "budget_ops_incidents",
    )

    if status is not None:
        check_status_payload(status, reporter)
    if incidents is not None:
        check_incidents_payload(incidents, reporter)
    if rollups is not None:
        check_rollups_payload(rollups, reporter)

    check_http_layer_alignment(root, reporter)

    reporter.render()

    fail_count = reporter.count("FAIL")
    warn_count = reporter.count("WARN")
    if fail_count > 0:
        return 1
    if args.strict and warn_count > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
