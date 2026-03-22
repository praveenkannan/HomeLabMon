#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from runtime_config import config_path as resolve_config_path
from runtime_config import dashboard_url as resolve_dashboard_url
from runtime_config import runtime_root

BASE = runtime_root()
STATE_JSON = BASE / 'state' / 'status.json'
HISTORY_JSONL = BASE / 'state' / 'history.jsonl'
HISTORY_DAY_DIR = BASE / 'state' / 'history'
CW_STATE = BASE / 'state' / 'cronwatcher_state.json'
LOG_PATH = BASE / 'logs' / 'monitor.log'
ALERT_SCRIPT = BASE / 'bin' / 'send_alert.py'
DEVICES_JSON = resolve_config_path()


def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    return now_utc().isoformat()


def dashboard_url():
    return resolve_dashboard_url()


def detail_url(device_name):
    return f"{dashboard_url()}#device={device_name}"


def load_state():
    if not CW_STATE.exists():
        return {}
    try:
        return json.loads(CW_STATE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_state(state):
    CW_STATE.parent.mkdir(parents=True, exist_ok=True)
    CW_STATE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding='utf-8')


def log(message):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open('a', encoding='utf-8') as fh:
        fh.write(f"{now_iso()} cronwatcher {message}\n")


def send_alert(state, reason, *, summary_type='event', device='cronwatcher', dry_run=False, force=False):
    payload = [
        sys.executable,
        str(ALERT_SCRIPT),
        'cronwatcher',
        state,
        reason,
        '--summary-type',
        summary_type,
        '--device',
        device,
    ]
    if dry_run:
        payload.append('--dry-run')
    if force:
        payload.append('--force')
    return subprocess.run(payload, check=False).returncode == 0


def parse_recent_rows(days=7):
    cutoff = now_utc() - timedelta(days=days)
    rows = []
    seen = set()

    candidates = []
    if HISTORY_DAY_DIR.exists():
        cutoff_day = cutoff.date()
        for path in sorted(HISTORY_DAY_DIR.glob('*.jsonl')):
            try:
                day = datetime.strptime(path.stem, '%Y-%m-%d').date()
            except ValueError:
                continue
            if day >= cutoff_day:
                candidates.append(path)
    if HISTORY_JSONL.exists():
        candidates.append(HISTORY_JSONL)

    for path in candidates:
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                ts = datetime.fromisoformat(row['timestamp'])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if ts < cutoff:
                continue
            key = json.dumps(row, sort_keys=True, separators=(',', ':'))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)

    rows.sort(key=lambda item: item.get('timestamp', ''))
    return rows


def load_device_meta():
    if not DEVICES_JSON.exists():
        return {}
    try:
        data = json.loads(DEVICES_JSON.read_text(encoding='utf-8'))
    except Exception:
        return {}
    meta = {}
    for item in data.get('devices', []):
        meta[item.get('name', '')] = {
            'display_name': item.get('display_name') or item.get('name', ''),
            'enabled': bool(item.get('enabled', True)),
        }
    return meta


def summarize_rows(rows):
    stats = {}
    for row in rows:
        for name, item in row.get('state', {}).items():
            s = stats.setdefault(name, {'checks': 0, 'up': 0, 'down': 0, 'last_reason': 'unknown'})
            s['checks'] += 1
            if item.get('healthy'):
                s['up'] += 1
            else:
                s['down'] += 1
            s['last_reason'] = item.get('reason', 'unknown')
    for s in stats.values():
        checks = s['checks']
        s['uptime_percent'] = round((s['up'] / checks) * 100, 1) if checks else 0.0
    return stats


def render_weekly_summary(rows):
    if not rows:
        return f'weekly summary: no history entries in last 7 days\ndashboard={dashboard_url()}'
    stats = summarize_rows(rows)
    meta = load_device_meta()
    snapshots = len(rows)
    devices = len(stats)
    up_now = down_now = disabled_now = 0
    latest = rows[-1].get('state', {})
    for name, item in latest.items():
        reason = item.get('reason', '')
        enabled = meta.get(name, {}).get('enabled', True)
        if (not enabled) or reason == 'disabled':
            disabled_now += 1
        elif item.get('healthy'):
            up_now += 1
        else:
            down_now += 1

    lines = [
        f'weekly summary (7d): snapshots={snapshots} devices={devices} up_now={up_now} down_now={down_now} disabled_now={disabled_now}',
        f'dashboard={dashboard_url()}',
        'devices:',
    ]

    for name, s in sorted(stats.items(), key=lambda kv: (kv[1]['uptime_percent'], kv[0])):
        display = meta.get(name, {}).get('display_name', name)
        lines.append(
            f"- {display} ({name}): uptime={s['uptime_percent']}% checks={s['checks']} up={s['up']} down={s['down']} last_reason={s['last_reason']} detail={detail_url(name)}"
        )
    return '\n'.join(lines)


def render_daily_digest(rows):
    if not rows:
        return f'daily digest: no history entries in last 24h\ndashboard={dashboard_url()}'
    stats = summarize_rows(rows)
    meta = load_device_meta()
    snapshots = len(rows)
    down_with_events = sum(1 for s in stats.values() if s['down'] > 0)

    lines = [
        f'daily digest (24h): snapshots={snapshots} devices={len(stats)} devices_with_down_checks={down_with_events}',
        f'dashboard={dashboard_url()}',
        'devices:',
    ]
    for name, s in sorted(stats.items(), key=lambda kv: (kv[1]['uptime_percent'], kv[0])):
        display = meta.get(name, {}).get('display_name', name)
        lines.append(
            f"- {display} ({name}): uptime={s['uptime_percent']}% checks={s['checks']} down={s['down']} last_reason={s['last_reason']} detail={detail_url(name)}"
        )
    return '\n'.join(lines)


def max_status_age_minutes():
    if not STATE_JSON.exists():
        return None
    data = json.loads(STATE_JSON.read_text(encoding='utf-8'))
    latest_ts = None
    for item in data.values():
        ts = item.get('checked_at')
        if not ts:
            continue
        dt = datetime.fromisoformat(ts)
        if latest_ts is None or dt > latest_ts:
            latest_ts = dt
    if latest_ts is None:
        return None
    return (now_utc() - latest_ts).total_seconds() / 60.0


def do_health(max_age, *, dry_run=False, force=False):
    age = max_status_age_minutes()
    state = load_state()
    stale = age is None or age > max_age
    was_stale = state.get('stale', False)

    if stale and not was_stale:
        reason = (
            f'monitor stale: last status age={"unknown" if age is None else round(age, 1)}m '
            f'threshold={max_age}m dashboard={dashboard_url()} detail={detail_url("cronwatcher")}'
        )
        send_alert('DOWN', reason, summary_type='event', device='cronwatcher', dry_run=dry_run, force=force)
        log(reason)
    elif (not stale) and was_stale:
        reason = (
            f'monitor recovered: last status age={round(age, 1)}m '
            f'dashboard={dashboard_url()} detail={detail_url("cronwatcher")}'
        )
        send_alert('RECOVERED', reason, summary_type='event', device='cronwatcher', dry_run=dry_run, force=force)
        log(reason)

    state['stale'] = stale
    state['last_health_check'] = now_iso()
    state['last_age_minutes'] = None if age is None else round(age, 2)
    save_state(state)


def do_weekly(*, dry_run=False, force=False):
    rows = parse_recent_rows(days=7)
    summary = render_weekly_summary(rows)
    send_alert('WEEKLY', summary, summary_type='weekly', device='cronwatcher', dry_run=dry_run, force=force)
    log('weekly_digest_sent')


def do_daily(*, dry_run=False, force=False):
    rows = parse_recent_rows(days=1)
    summary = render_daily_digest(rows)
    send_alert('DAILY', summary, summary_type='daily', device='cronwatcher', dry_run=dry_run, force=force)
    log('daily_digest_sent')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--weekly', action='store_true')
    parser.add_argument('--daily', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--max-age-minutes', type=float, default=float(os.getenv('CRONWATCHER_MAX_AGE_MINUTES', '10')))
    args = parser.parse_args()

    if args.weekly:
        do_weekly(dry_run=args.dry_run, force=args.force)
        return 0
    if args.daily:
        do_daily(dry_run=args.dry_run, force=args.force)
        return 0

    do_health(args.max_age_minutes, dry_run=args.dry_run, force=args.force)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
