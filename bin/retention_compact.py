#!/usr/bin/env python3
import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(os.getenv('HOMELABMON_ROOT', os.getenv('PI_MONITOR_ROOT', '/opt/homelabmon')))
STATE_DIR = BASE / 'state'
HISTORY_LEGACY_PATH = STATE_DIR / 'history.jsonl'
HISTORY_DAY_DIR = STATE_DIR / 'history'
INCIDENTS_LEGACY_PATH = STATE_DIR / 'incidents.jsonl'
INCIDENTS_DAY_DIR = STATE_DIR / 'incidents'
ROLLUPS_DIR = STATE_DIR / 'rollups'
READ_MODEL_ROLLUPS_PATH = STATE_DIR / 'read_model' / 'rollups.json'

DATE_PREFIX_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})')


def utc_now_dt():
    return datetime.now(timezone.utc)


def utc_now():
    return utc_now_dt().isoformat()


def parse_iso(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def parse_file_date(path):
    match = DATE_PREFIX_RE.match(path.stem)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), '%Y-%m-%d').date()
    except ValueError:
        return None


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def save_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')
    tmp.replace(path)


def compact_legacy_jsonl(path, cutoff, *, timestamp_keys=('timestamp',)):
    if not path.exists():
        return {'path': str(path), 'exists': False, 'kept': 0, 'removed': 0}

    kept_rows = []
    removed = 0
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            removed += 1
            continue
        ts = None
        for key in timestamp_keys:
            ts = parse_iso(row.get(key))
            if ts is not None:
                break
        if ts is None or ts < cutoff:
            removed += 1
            continue
        kept_rows.append(row)

    if removed > 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8') as fh:
            for row in kept_rows:
                fh.write(json.dumps(row, sort_keys=True) + '\n')

    return {'path': str(path), 'exists': True, 'kept': len(kept_rows), 'removed': removed}


def prune_partition_dir(dir_path, cutoff):
    if not dir_path.exists():
        return {'path': str(dir_path), 'exists': False, 'deleted_files': 0, 'kept_files': 0}

    deleted = 0
    kept = 0
    cutoff_date = cutoff.date()

    for path in sorted(dir_path.glob('*')):
        if not path.is_file():
            continue
        file_date = parse_file_date(path)
        if file_date is None:
            kept += 1
            continue
        if file_date < cutoff_date:
            path.unlink(missing_ok=True)
            deleted += 1
        else:
            kept += 1

    return {'path': str(dir_path), 'exists': True, 'deleted_files': deleted, 'kept_files': kept}


def compact_rollup_placeholder(cutoff):
    payload = load_json(READ_MODEL_ROLLUPS_PATH, {})
    if not isinstance(payload, dict):
        payload = {}

    items = payload.get('items', [])
    if not isinstance(items, list):
        items = []

    kept = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ts = parse_iso(item.get('timestamp'))
        if ts is None:
            day = item.get('day')
            if isinstance(day, str):
                try:
                    ts = datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                except ValueError:
                    ts = None
        if ts is None or ts >= cutoff:
            kept.append(item)

    removed = max(0, len(items) - len(kept))
    payload['items'] = kept
    payload['generated_at'] = utc_now()
    payload.setdefault('placeholder', True)
    payload.setdefault('schema_version', 1)
    payload.setdefault('retention_days', 365)
    save_json(READ_MODEL_ROLLUPS_PATH, payload)

    return {'path': str(READ_MODEL_ROLLUPS_PATH), 'exists': True, 'kept': len(kept), 'removed': removed}


def main(argv=None):
    parser = argparse.ArgumentParser(description='compact pi-monitor retention data')
    parser.add_argument('--raw-days', type=int, default=30)
    parser.add_argument('--incident-days', type=int, default=90)
    parser.add_argument('--rollup-days', type=int, default=365)
    args = parser.parse_args(argv)

    now = utc_now_dt()
    raw_cutoff = now - timedelta(days=max(1, int(args.raw_days)))
    incident_cutoff = now - timedelta(days=max(1, int(args.incident_days)))
    rollup_cutoff = now - timedelta(days=max(1, int(args.rollup_days)))

    summary = {
        'ran_at': utc_now(),
        'retention_days': {
            'raw': int(args.raw_days),
            'incidents': int(args.incident_days),
            'rollups': int(args.rollup_days),
        },
        'history': {
            'legacy': compact_legacy_jsonl(HISTORY_LEGACY_PATH, raw_cutoff, timestamp_keys=('timestamp',)),
            'partitioned': prune_partition_dir(HISTORY_DAY_DIR, raw_cutoff),
        },
        'incidents': {
            'legacy': compact_legacy_jsonl(INCIDENTS_LEGACY_PATH, incident_cutoff, timestamp_keys=('timestamp',)),
            'partitioned': prune_partition_dir(INCIDENTS_DAY_DIR, incident_cutoff),
        },
        'rollups': {
            'partitioned': prune_partition_dir(ROLLUPS_DIR, rollup_cutoff),
            'read_model': compact_rollup_placeholder(rollup_cutoff),
        },
    }

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
