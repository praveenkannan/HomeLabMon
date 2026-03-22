#!/usr/bin/env python3
import json
import os
import re
import socket
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from runtime_config import config_path as resolve_config_path
from runtime_config import runtime_root

BASE = runtime_root()
CONFIG_PATH = resolve_config_path()
STATE_DIR = BASE / 'state'
STATE_PATH = STATE_DIR / 'status.json'
HISTORY_PATH = STATE_DIR / 'history.jsonl'
HISTORY_DAY_DIR = STATE_DIR / 'history'
INCIDENTS_PATH = STATE_DIR / 'incidents.jsonl'
INCIDENTS_DAY_DIR = STATE_DIR / 'incidents'
INCIDENT_ENGINE_STATE_PATH = STATE_DIR / 'incident_engine_state.json'
READ_MODEL_DIR = STATE_DIR / 'read_model'
READ_MODEL_STATUS_PATH = READ_MODEL_DIR / 'status.json'
READ_MODEL_INCIDENTS_PATH = READ_MODEL_DIR / 'incidents.json'
READ_MODEL_ROLLUPS_PATH = READ_MODEL_DIR / 'rollups.json'
LOG_PATH = BASE / 'logs' / 'monitor.log'
ALERT_SCRIPT = BASE / 'bin' / 'send_alert.py'
WWW_PATH = BASE / 'www'
STATUS_HTML = WWW_PATH / 'status.html'
DEFAULT_MONITOR_NAME = 'HomeLabMon'

try:
    from incident_engine import derive_incidents
except Exception:
    derive_incidents = None

try:
    from temperature_collect import collect_temperature_inventory
except Exception:
    collect_temperature_inventory = None


def utc_now_dt():
    return datetime.now(timezone.utc)


def utc_now():
    return utc_now_dt().isoformat()


def log(message):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open('a', encoding='utf-8') as fh:
        fh.write(f"{utc_now()} {message}\n")


def load_json(path, default):
    if not path.exists():
        return default
    with path.open('r', encoding='utf-8') as fh:
        return json.load(fh)


def save_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    tmp.replace(path)


def _round_ms(value):
    if value is None:
        return None
    return round(float(value), 1)


def ping_check(host, count=2, timeout=1):
    started = time.monotonic()
    result = subprocess.run(
        ['ping', '-c', str(max(1, int(count))), '-W', str(max(1, int(timeout))), host],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    elapsed_ms = _round_ms((time.monotonic() - started) * 1000)
    out = result.stdout or ''
    loss = None
    avg = None

    loss_match = re.search(r'(\d+(?:\.\d+)?)%\s+packet\s+loss', out)
    if loss_match:
        loss = float(loss_match.group(1))

    rtt_match = re.search(r'=\s*([\d.]+)/([\d.]+)/([\d.]+)/', out)
    if rtt_match:
        avg = float(rtt_match.group(2))

    ok = result.returncode == 0
    metric = {
        'ok': ok,
        'count': max(1, int(count)),
        'loss_pct': loss,
        'avg_ms': _round_ms(avg),
        'elapsed_ms': elapsed_ms,
    }
    reason = 'ping ok' if ok else 'ping failed'
    if metric['avg_ms'] is not None or metric['loss_pct'] is not None:
        reason = 'ping avg={avg}ms loss={loss}%'.format(
            avg='-' if metric['avg_ms'] is None else metric['avg_ms'],
            loss='-' if metric['loss_pct'] is None else _round_ms(metric['loss_pct']),
        )
    return ok, reason, metric


def tcp_check(host, port, timeout=1.5):
    started = time.monotonic()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            latency = _round_ms((time.monotonic() - started) * 1000)
            return True, f'tcp:{port} ok', {'port': int(port), 'ok': True, 'latency_ms': latency}
    except OSError as err:
        latency = _round_ms((time.monotonic() - started) * 1000)
        return False, f'tcp:{port} failed', {'port': int(port), 'ok': False, 'latency_ms': latency, 'error': str(err)[:120]}


def http_check(url, timeout=2.0):
    started = time.monotonic()
    try:
        with urlopen(url, timeout=timeout) as response:
            latency = _round_ms((time.monotonic() - started) * 1000)
            ok = 200 <= response.status < 400
            return ok, f'http:{response.status}', {'url': url, 'ok': ok, 'status': int(response.status), 'latency_ms': latency}
    except URLError as err:
        latency = _round_ms((time.monotonic() - started) * 1000)
        return False, 'http failed', {'url': url, 'ok': False, 'status': None, 'latency_ms': latency, 'error': str(err)[:120]}


def dns_check(name, timeout=1.0):
    started = time.monotonic()
    previous_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        infos = socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)
        addrs = sorted({info[4][0] for info in infos if info and info[4]})
        latency = _round_ms((time.monotonic() - started) * 1000)
        return True, f'dns:{name} ok', {'name': name, 'ok': True, 'latency_ms': latency, 'answers': addrs[:3]}
    except OSError as err:
        latency = _round_ms((time.monotonic() - started) * 1000)
        return False, f'dns:{name} failed', {'name': name, 'ok': False, 'latency_ms': latency, 'answers': [], 'error': str(err)[:120]}
    finally:
        socket.setdefaulttimeout(previous_timeout)


def _dns_targets(device, host):
    targets = []
    raw = device.get('dns_names') or device.get('dns_checks') or []
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, list):
        targets.extend([item for item in raw if isinstance(item, str) and item.strip()])
    if device.get('dns_check') and not targets and host:
        targets.append(host)
    return targets


def _list_value(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def normalize_device(device):
    if not isinstance(device, dict):
        return None

    checks = device.get('checks') if isinstance(device.get('checks'), dict) else {}
    action_policy = device.get('action_policy') if isinstance(device.get('action_policy'), dict) else {}
    maintenance = device.get('maintenance') if isinstance(device.get('maintenance'), dict) else {}

    device_id = str(device.get('id') or device.get('name') or '').strip()
    if not device_id:
        return None

    normalized = dict(device)
    normalized['id'] = device_id
    normalized['name'] = device_id
    normalized['display_name'] = device.get('display_name') or device.get('name') or device_id
    normalized['ping'] = checks.get('ping', device.get('ping', False))
    normalized['tcp_ports'] = _list_value(checks.get('tcp_ports', device.get('tcp_ports', [])))
    normalized['http_urls'] = _list_value(checks.get('http_urls', device.get('http_urls', [])))
    normalized['dns_names'] = _list_value(checks.get('dns_names', device.get('dns_names', [])))
    normalized['checks'] = {
        'ping': bool(normalized['ping']),
        'tcp_ports': normalized['tcp_ports'],
        'http_urls': normalized['http_urls'],
        'dns_names': normalized['dns_names'],
    }
    normalized['action_policy'] = action_policy
    normalized['maintenance'] = maintenance
    return normalized


def normalize_devices(devices):
    normalized = []
    for device in devices:
        item = normalize_device(device)
        if item is not None:
            normalized.append(item)
    return normalized


def evaluate_device(device):
    if not device.get('enabled', True):
        return {'healthy': True, 'reason': 'disabled', 'checked_at': utc_now(), 'metrics': {}}

    host = device.get('host', '')
    reasons = []
    metrics = {'ping': None, 'tcp': [], 'http': [], 'dns': []}

    if device.get('ping'):
        ok, reason, metric = ping_check(host, count=device.get('ping_count', 2), timeout=device.get('ping_timeout', 1))
        metrics['ping'] = metric
        reasons.append(reason)
        if not ok:
            return {'healthy': False, 'reason': reason, 'checked_at': utc_now(), 'metrics': metrics}

    for port in device.get('tcp_ports', []):
        ok, reason, metric = tcp_check(host, port)
        metrics['tcp'].append(metric)
        reasons.append(reason)
        if not ok:
            return {'healthy': False, 'reason': reason, 'checked_at': utc_now(), 'metrics': metrics}

    for url in device.get('http_urls', []):
        ok, reason, metric = http_check(url)
        metrics['http'].append(metric)
        reasons.append(reason)
        if not ok:
            return {'healthy': False, 'reason': reason, 'checked_at': utc_now(), 'metrics': metrics}

    for name in _dns_targets(device, host):
        ok, reason, metric = dns_check(name, timeout=device.get('dns_timeout', 1.0))
        metrics['dns'].append(metric)
        reasons.append(reason)
        if not ok:
            return {'healthy': False, 'reason': reason, 'checked_at': utc_now(), 'metrics': metrics}

    return {'healthy': True, 'reason': ', '.join(reasons) if reasons else 'ok', 'checked_at': utc_now(), 'metrics': metrics}


def _parse_iso(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _partition_file(base_dir, ts):
    dt = _parse_iso(ts) or utc_now_dt()
    return base_dir / f"{dt.astimezone(timezone.utc).date().isoformat()}.jsonl"


def _load_jsonl(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _partition_candidates(base_dir, cutoff):
    if not base_dir.exists():
        return []
    cutoff_day = cutoff.astimezone(timezone.utc).date()
    paths = []
    for path in sorted(base_dir.glob('*.jsonl')):
        try:
            day = datetime.strptime(path.stem, '%Y-%m-%d').date()
        except ValueError:
            continue
        if day >= cutoff_day:
            paths.append(path)
    return paths


def _load_recent_rows(*, cutoff, legacy_path, partition_dir):
    rows = []
    for path in _partition_candidates(partition_dir, cutoff):
        rows.extend(_load_jsonl(path))
    if legacy_path.exists():
        rows.extend(_load_jsonl(legacy_path))

    deduped = []
    seen = set()
    for row in rows:
        ts = _parse_iso(row.get('timestamp'))
        if ts is None or ts < cutoff:
            continue
        key = json.dumps(row, sort_keys=True, separators=(',', ':'))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    deduped.sort(key=lambda item: item.get('timestamp', ''))
    return deduped


def _append_jsonl(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(row, sort_keys=True) + '\n')


def load_recent_history(days=7):
    cutoff = utc_now_dt() - timedelta(days=days)
    return _load_recent_rows(cutoff=cutoff, legacy_path=HISTORY_PATH, partition_dir=HISTORY_DAY_DIR)


def load_recent_incidents(days=30):
    cutoff = utc_now_dt() - timedelta(days=days)
    return _load_recent_rows(cutoff=cutoff, legacy_path=INCIDENTS_PATH, partition_dir=INCIDENTS_DAY_DIR)


def summarize_recent_health(rows):
    summary = {}
    for row in rows:
        for name, item in row.get('state', {}).items():
            s = summary.setdefault(name, {'checks': 0, 'healthy_checks': 0, 'uptime_percent': 0})
            s['checks'] += 1
            if item.get('healthy'):
                s['healthy_checks'] += 1
    for s in summary.values():
        if s['checks']:
            s['uptime_percent'] = round((s['healthy_checks'] / s['checks']) * 100)
    return summary


def summarize_weekly_by_day(rows):
    by_device_day = defaultdict(lambda: defaultdict(lambda: {'checks': 0, 'healthy': 0}))
    for row in rows:
        try:
            day = datetime.fromisoformat(row['timestamp']).astimezone(timezone.utc).date().isoformat()
        except Exception:
            continue
        for name, item in row.get('state', {}).items():
            slot = by_device_day[name][day]
            slot['checks'] += 1
            if item.get('healthy'):
                slot['healthy'] += 1
    out = {}
    for name, day_map in by_device_day.items():
        out[name] = {}
        for day, item in day_map.items():
            checks = item['checks']
            out[name][day] = {'checks': checks, 'uptime_percent': round((item['healthy'] / checks) * 100) if checks else 0}
    return out


def summarize_hourly(rows, hours=24):
    now = utc_now_dt().replace(minute=0, second=0, microsecond=0)
    slots = [(now - timedelta(hours=i)) for i in range(0, hours)]
    slot_keys = [slot.isoformat() for slot in slots]
    by_device = defaultdict(dict)
    for row in rows:
        try:
            ts = datetime.fromisoformat(row['timestamp']).astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        except Exception:
            continue
        key = ts.isoformat()
        if key not in slot_keys:
            continue
        for name, item in row.get('state', {}).items():
            by_device[name][key] = {'healthy': bool(item.get('healthy')), 'reason': item.get('reason', 'unknown')}

    output = {}
    for name in by_device:
        series = []
        for key in slot_keys:
            point = by_device[name].get(key)
            series.append({
                'hour': key[11:16],
                'slot_start': key,
                'healthy': None if point is None else point['healthy'],
                'reason': '-' if point is None else point['reason'],
            })
        output[name] = series
    return output


def append_history(state, *, timestamp=None):
    ts = timestamp or utc_now()
    row = {'timestamp': ts, 'state': state}
    _append_jsonl(HISTORY_PATH, row)
    _append_jsonl(_partition_file(HISTORY_DAY_DIR, ts), row)


def append_incidents(incidents):
    INCIDENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    INCIDENTS_DAY_DIR.mkdir(parents=True, exist_ok=True)
    for incident in incidents:
        ts = incident.get('timestamp') or utc_now()
        row = dict(incident)
        row['timestamp'] = ts
        _append_jsonl(INCIDENTS_PATH, row)
        _append_jsonl(_partition_file(INCIDENTS_DAY_DIR, ts), row)


def restart_capabilities(devices):
    caps = {}
    for d in devices:
        restart = d.get('restart')
        action_policy = d.get('action_policy') if isinstance(d.get('action_policy'), dict) else {}
        caps[d['name']] = bool(
            (restart and restart.get('type') in {'command', 'http'})
            or action_policy.get('allow_restart')
        )
    return caps


def collect_pi_host_metrics():
    metrics = {
        'collected_at': utc_now(),
        'hostname': socket.gethostname(),
        'load': {'l1': None, 'l5': None, 'l15': None},
        'memory': {'total_mb': None, 'available_mb': None, 'used_pct': None},
        'temp_c': None,
    }

    try:
        l1, l5, l15 = os.getloadavg()
        metrics['load'] = {'l1': round(l1, 2), 'l5': round(l5, 2), 'l15': round(l15, 2)}
    except OSError:
        pass

    meminfo = {}
    try:
        with open('/proc/meminfo', 'r', encoding='utf-8') as fh:
            for line in fh:
                if ':' not in line:
                    continue
                k, v = line.split(':', 1)
                meminfo[k.strip()] = int(v.strip().split()[0])
    except Exception:
        meminfo = {}

    total_kb = meminfo.get('MemTotal')
    avail_kb = meminfo.get('MemAvailable')
    if total_kb and avail_kb is not None:
        used_kb = max(0, total_kb - avail_kb)
        metrics['memory'] = {
            'total_mb': round(total_kb / 1024.0, 1),
            'available_mb': round(avail_kb / 1024.0, 1),
            'used_pct': round((used_kb / total_kb) * 100.0, 1),
        }

    for temp_path in ('/sys/class/thermal/thermal_zone0/temp', '/sys/class/hwmon/hwmon0/temp1_input'):
        try:
            raw = Path(temp_path).read_text(encoding='utf-8').strip()
            value = float(raw)
            if value > 1000:
                value = value / 1000.0
            metrics['temp_c'] = round(value, 1)
            break
        except Exception:
            continue

    return metrics


def _temperature_entry(payload):
    if not isinstance(payload, dict):
        return {}
    entry = payload.get('temperature')
    return entry if isinstance(entry, dict) else {}


def _temperature_heat_state(payload):
    entry = _temperature_entry(payload)
    heat = entry.get('heat') if isinstance(entry.get('heat'), dict) else {}
    return str(heat.get('state') or 'UNKNOWN').upper()


def attach_temperature_inventory(state, inventory):
    merged = {}
    devices = inventory.get('devices', {}) if isinstance(inventory, dict) else {}
    for name, item in state.items():
        next_item = dict(item)
        temperature = devices.get(name, {}) if isinstance(devices, dict) else {}
        if isinstance(temperature, dict) and temperature:
            heat = temperature.get('heat') if isinstance(temperature.get('heat'), dict) else {}
            next_item['temperature'] = temperature
            next_item['heat_state'] = str(heat.get('state') or 'UNKNOWN').upper()
            next_item['heat_value_c'] = heat.get('value_c')
            next_item['temp_capability'] = temperature.get('capability', 'unsupported')
        merged[name] = next_item
    return merged


def summarize_temperature_trends(rows, points=8):
    trends = {}
    for row in rows:
        for name, item in row.get('state', {}).items():
            temp = _temperature_entry(item)
            heat = temp.get('heat') if isinstance(temp.get('heat'), dict) else {}
            value = heat.get('value_c')
            if value is None:
                continue
            slot = trends.setdefault(name, [])
            try:
                slot.append(round(float(value), 1))
            except (TypeError, ValueError):
                continue

    output = {}
    for name, series in trends.items():
        trimmed = series[-points:]
        direction = 'flat'
        if len(trimmed) >= 2:
            delta = trimmed[-1] - trimmed[0]
            if delta >= 1.0:
                direction = 'up'
            elif delta <= -1.0:
                direction = 'down'
        output[name] = {
            'series': trimmed,
            'direction': direction,
        }
    return output


def derive_temperature_incidents(old_state, new_state, *, now_ts=None):
    timestamp = now_ts or utc_now()
    incidents = []
    for name in sorted(set(old_state.keys()) | set(new_state.keys())):
        if name not in old_state or name not in new_state:
            continue

        previous_state = _temperature_heat_state(old_state.get(name))
        current_item = new_state.get(name, {})
        current_state = _temperature_heat_state(current_item)
        if current_state == 'UNKNOWN':
            continue

        if previous_state != 'HOT' and current_state == 'HOT':
            event = 'DOWN'
        elif previous_state == 'HOT' and current_state != 'HOT':
            event = 'RECOVERED'
        else:
            continue

        temperature = _temperature_entry(current_item)
        heat = temperature.get('heat') if isinstance(temperature.get('heat'), dict) else {}
        reason = 'temperature {state}'.format(
            state=current_state.lower(),
        )
        if heat.get('value_c') is not None:
            reason = '{reason} at {value} C'.format(reason=reason, value=heat.get('value_c'))

        incidents.append({
            'incident_id': f'{name}:{timestamp}:temperature:{event}',
            'timestamp': timestamp,
            'device': name,
            'event': event,
            'reason': reason,
            'reason_code': 'threshold_breach',
            'check_key': 'temperature:system',
            'from_state': previous_state,
            'to_state': current_state,
            'severity': 'critical',
            'flap': {'detected': False, 'transition_count': 0},
        })
    return incidents


def build_active_incidents(state):
    items = []
    for name in sorted(state):
        item = state[name]
        display = item.get('display_name', name)
        if not item.get('healthy') and item.get('reason') != 'disabled':
            items.append({'device': display, 'summary': item.get('reason', 'Device unavailable')})
        if item.get('heat_state') == 'HOT':
            value = item.get('heat_value_c')
            summary = 'Temperature critical'
            if value is not None:
                summary = f'Temperature critical at {value} C'
            items.append({'device': display, 'summary': summary})
    return items


def _metric_line_ping(metric):
    if not metric:
        return '-'
    avg = metric.get('avg_ms')
    loss = metric.get('loss_pct')
    return 'avg {} ms / loss {}%'.format('-' if avg is None else avg, '-' if loss is None else _round_ms(loss))


def _metric_line_tcp(metrics):
    if not metrics:
        return '-'
    parts = []
    for m in metrics[:3]:
        parts.append('{port}:{status}{lat}'.format(
            port=m.get('port', '?'),
            status='ok' if m.get('ok') else 'fail',
            lat='' if m.get('latency_ms') is None else '@{}ms'.format(m.get('latency_ms')),
        ))
    return '; '.join(parts)


def _metric_line_http(metrics):
    if not metrics:
        return '-'
    parts = []
    for m in metrics[:3]:
        parts.append('{status}{lat}'.format(
            status=m.get('status', 'ERR'),
            lat='' if m.get('latency_ms') is None else '@{}ms'.format(m.get('latency_ms')),
        ))
    return '; '.join(parts)


def _metric_line_dns(metrics):
    if not metrics:
        return '-'
    parts = []
    for m in metrics[:3]:
        first_answer = (m.get('answers') or ['-'])[0]
        parts.append('{name}:{status}{lat}:{ans}'.format(
            name=m.get('name', '?'),
            status='ok' if m.get('ok') else 'fail',
            lat='' if m.get('latency_ms') is None else '@{}ms'.format(m.get('latency_ms')),
            ans=first_answer,
        ))
    return '; '.join(parts)


def render_status_page(state, devices, config=None):
    WWW_PATH.mkdir(parents=True, exist_ok=True)
    recent_rows = load_recent_history()
    history_summary = summarize_recent_health(recent_rows)
    weekly = summarize_weekly_by_day(recent_rows)
    hourly = summarize_hourly(load_recent_history(days=1), hours=6)
    temperature_trends = summarize_temperature_trends(recent_rows)
    host_metrics = collect_pi_host_metrics()
    up = sum(1 for item in state.values() if item.get('healthy') and item.get('reason') != 'disabled')
    down = sum(1 for item in state.values() if not item.get('healthy'))
    disabled = sum(1 for item in state.values() if item.get('reason') == 'disabled')
    meta = {d['name']: d for d in devices}
    state_with_display = {}
    for name, item in state.items():
        next_item = dict(item)
        next_item['display_name'] = meta.get(name, {}).get('display_name', name)
        state_with_display[name] = next_item
    active_incidents = build_active_incidents(state_with_display)

    cards = []
    telemetry_rows = []
    weekly_rows = []
    device_metrics = {}
    for name in sorted(state):
        item = state[name]
        m = meta.get(name, {})
        display = m.get('display_name', name)
        logo_url = m.get('logo_url', '')
        fallback_text = escape((display or name or '?')[:1].upper())
        if logo_url:
            logo_box_class = 'logo-box'
            logo_markup = (
                "<img class='logo-img' src='{src}' alt='{alt} logo' loading='lazy' referrerpolicy='no-referrer' "
                "onerror=\"this.style.display='none';this.parentElement.classList.add('logo-box-fallback');\">"
                "<span class='logo-fallback' aria-hidden='true'>{fallback}</span>"
            ).format(src=escape(logo_url), alt=escape(display), fallback=fallback_text)
        else:
            logo_box_class = 'logo-box logo-box-fallback'
            logo_markup = "<span class='logo-fallback' aria-hidden='true'>{}</span>".format(fallback_text)
        reason = item.get('reason', 'unknown')
        checked = item.get('checked_at', 'unknown')
        hist = history_summary.get(name, {'uptime_percent': 'n/a', 'checks': 0})
        if reason == 'disabled':
            label = 'DISABLED'; css = 'status-disabled'
        elif item.get('healthy'):
            label = 'UP'; css = 'status-up'
        else:
            label = 'DOWN'; css = 'status-down'

        device_metric = item.get('metrics', {})
        device_metrics[name] = device_metric
        heat_state = item.get('heat_state', 'UNKNOWN').lower()
        heat_value = item.get('heat_value_c')
        heat_label = 'Temperature unavailable'
        if heat_state == 'normal' and heat_value is not None:
            heat_label = f'Normal {heat_value} C'
        elif heat_state == 'warm' and heat_value is not None:
            heat_label = f'Warm {heat_value} C'
        elif heat_state == 'hot' and heat_value is not None:
            heat_label = f'Hot {heat_value} C'
        trend = temperature_trends.get(name, {})
        trend_series = ','.join(str(value) for value in trend.get('series', []))
        trend_direction = trend.get('direction', 'flat')
        badges = []
        if item.get('temp_capability'):
            badges.append(
                "<span class='heat-badge' data-heat='{heat}' data-heat-label='{label}'>{label}</span>".format(
                    heat=escape(heat_state),
                    label=escape(heat_label),
                )
            )
        if trend_series:
            bars = ''.join("<span class='trend-strip__bar'></span>" for _ in trend.get('series', []))
            badges.append(
                "<span class='trend-strip' data-trend='{trend}' data-trend-label='{label}' data-trend-series='{series}'><span class='trend-strip__bars'>{bars}</span></span>".format(
                    trend=escape(trend_direction),
                    label=escape(trend_direction.title()),
                    series=escape(trend_series),
                    bars=bars,
                )
            )

        cards.append(
            "<article class='card tw-rounded-xl tw-border tw-p-4 device-card' data-device='{name}' data-heat='{heat}' data-heat-label='{heat_label}' data-trend='{trend}' data-trend-label='{trend_label}' data-trend-series='{trend_series}'>"
            "<div class='card-head'><span class='{logo_box_class}'>{logo_markup}</span><div class='title-wrap'><h3 class='tw-text-lg tw-font-semibold'>{display}</h3><p class='subtext'>{name}</p></div></div>"
            "<div class='card-badges'>{badges}</div>"
            "<div class='pill {css}'>{label}</div>"
            "<p class='muted'>{reason}</p><p class='meta'>Last checked: {checked}</p>"
            "<p class='meta'>7d health: {uptime}% over {checks} checks</p>"
            "<button class='restart-btn' data-device='{name}' type='button'>Restart</button>"
            "</article>".format(
                name=escape(name), logo_box_class=logo_box_class, logo_markup=logo_markup, display=escape(display), css=css,
                label=label, reason=escape(reason), checked=escape(checked),
                uptime=escape(str(hist['uptime_percent'])), checks=escape(str(hist['checks'])),
                heat=escape(heat_state), heat_label=escape(heat_label), trend=escape(trend_direction),
                trend_label=escape(trend_direction.title()), trend_series=escape(trend_series), badges=''.join(badges),
            )
        )

        telemetry_rows.append(
            "<tr><td>{display}<div class='subtext'>{name}</div></td><td>{label}</td><td>{reason}</td><td>{ping}</td><td>{tcp}</td><td>{http}</td><td>{dns}</td><td>{checked}</td></tr>".format(
                display=escape(display),
                name=escape(name),
                label=escape(label),
                reason=escape(reason),
                ping=escape(_metric_line_ping(device_metric.get('ping'))),
                tcp=escape(_metric_line_tcp(device_metric.get('tcp', []))),
                http=escape(_metric_line_http(device_metric.get('http', []))),
                dns=escape(_metric_line_dns(device_metric.get('dns', []))),
                checked=escape(checked),
            )
        )

        day_cells = []
        for i in range(0, 7):
            day = (utc_now_dt() - timedelta(days=i)).date().isoformat()
            info = weekly.get(name, {}).get(day)
            if info:
                day_cells.append("<td>{uptime}% <span class='muted'>({checks})</span></td>".format(uptime=info['uptime_percent'], checks=info['checks']))
            else:
                day_cells.append("<td class='muted'>-</td>")
        weekly_rows.append("<tr><td>{display}<div class='subtext'>{name}</div></td>{cells}</tr>".format(display=escape(display), name=escape(name), cells=''.join(day_cells)))

    day_headers = ''.join("<th>{}</th>".format(escape((utc_now_dt() - timedelta(days=i)).date().isoformat()[5:])) for i in range(0, 7))
    incident_markup = ''.join(
        "<li><strong>{device}</strong><span>{summary}</span></li>".format(
            device=escape(item['device']),
            summary=escape(item['summary']),
        )
        for item in active_incidents[:6]
    ) or "<li class='muted'>No active incidents.</li>"

    config = config if isinstance(config, dict) else {}
    site_title = str(config.get('site_title') or config.get('instance_name') or DEFAULT_MONITOR_NAME)
    html = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>{title}</title><link rel='icon' type='image/svg+xml' href='favicon.svg'><link rel='stylesheet' href='status.css'></head><body><main class='container' data-layout='observability-dashboard'>"
        "<section class='banner dashboard-banner tw-rounded-2xl tw-border tw-p-6' data-panel='banner'><p class='eyebrow'>Home Ops Monitor</p><h1>{title}</h1>"
        "<p class='muted'>Low-footprint home infrastructure telemetry board with rolling weekly history and operational actions.</p>"
        "<div class='dashboard-toolbar'><div class='theme-control' id='theme-control' data-theme-control><label for='theme-mode-select'>Theme</label><select id='theme-mode-select' data-theme-selector aria-label='Theme selector'><option value='system'>System</option><option value='light'>Light</option><option value='dark'>Dark</option></select></div></div>"
        "<div class='summary-grid'><div><p class='label'>Up</p><p class='value'>{up}</p></div><div><p class='label'>Down</p><p class='value'>{down}</p></div>"
        "<div><p class='label'>Disabled</p><p class='value'>{disabled}</p></div><div><p class='label'>Updated (UTC)</p><p class='value small'>{updated}</p></div></div></section>"
        "<section class='panel active-incidents-panel' data-panel='active-incidents'><h2>Active Incidents</h2><ul class='incident-list'>{incident_markup}</ul></section>"
        "<section class='panel dashboard-pi-metrics' data-panel='metrics'><h2>Pi Host Metrics</h2><div id='pi-host-metrics' class='muted'>Loading host metrics...</div></section>"
        "<section class='panel device-cards-panel' data-panel='cards'><h2>Device Health Cards</h2><p class='muted'>Click a card for the last 6 hourly states. Restart requires propose plus confirm.</p><div class='card-grid'>{cards}</div></section>"
        "<section class='panel selected-device-panel' data-panel='selected'><h2>Selected Device</h2><div id='hourly-detail' class='hourly-detail muted'>Click a device card to view hourly status for the last 6 hours.</div></section>"
        "<section class='panel ai-ops-panel' data-panel='ai'><h2>AI Ops</h2><div id='ai-capability-shell' class='muted'>Checking AI capability...</div></section>"
        "<section class='panel dashboard-weekly-panel' data-panel='weekly'><h2>Weekly Health History (7 Days)</h2><div class='table-wrap'><table><thead><tr><th>Device</th>{day_headers}</tr></thead><tbody>{weekly_rows}</tbody></table></div></section>"
        "<section class='panel dashboard-telemetry-panel' data-panel='telemetry'><h2>Telemetry Feed</h2><div class='table-wrap'><table><thead><tr><th>Device</th><th>Status</th><th>Reason</th><th>Ping</th><th>TCP</th><th>HTTP</th><th>DNS</th><th>Last Checked</th></tr></thead><tbody>{telemetry_rows}</tbody></table></div></section>"
        "</main><script id='hourly-data' type='application/json'>{hourly_json}</script><script id='restart-caps' type='application/json'>{restart_json}</script><script id='device-metrics' type='application/json'>{device_metrics_json}</script><script id='pi-metrics-data' type='application/json'>{pi_metrics_json}</script><script src='status.js'></script></body></html>"
    ).format(
        title=escape(site_title),
        up=up,
        down=down,
        disabled=disabled,
        updated=escape(utc_now()),
        incident_markup=incident_markup,
        cards=''.join(cards),
        day_headers=day_headers,
        weekly_rows=''.join(weekly_rows),
        telemetry_rows=''.join(telemetry_rows),
        hourly_json=escape(json.dumps(hourly), quote=False),
        restart_json=escape(json.dumps(restart_capabilities(devices)), quote=False),
        device_metrics_json=escape(json.dumps(device_metrics), quote=False),
        pi_metrics_json=escape(json.dumps(host_metrics), quote=False),
    )

    previous = STATUS_HTML.read_text(encoding='utf-8') if STATUS_HTML.exists() else None
    if previous != html:
        STATUS_HTML.write_text(html, encoding='utf-8')


def _fallback_derive_incidents(old_state, new_state, *, now_ts=None, engine_state=None):
    timestamp = now_ts or utc_now()
    incidents = []
    for name, current in new_state.items():
        previous = old_state.get(name)
        if previous is None:
            continue
        prev_healthy = bool(previous.get('healthy'))
        curr_healthy = bool(current.get('healthy'))
        if prev_healthy == curr_healthy:
            continue
        event = 'RECOVERED' if curr_healthy else 'DOWN'
        incidents.append({
            'incident_id': f'{name}:{timestamp}:{event}',
            'timestamp': timestamp,
            'device': name,
            'event': event,
            'reason': current.get('reason', 'unknown'),
            'from_state': 'UP' if prev_healthy else 'DOWN',
            'to_state': 'UP' if curr_healthy else 'DOWN',
            'flap': {'detected': False, 'transition_count': 0},
        })
    return incidents, (engine_state or {})


def send_alert(incident):
    payload = [
        sys.executable,
        str(ALERT_SCRIPT),
        incident.get('device', 'unknown'),
        incident.get('event', 'UNKNOWN'),
        incident.get('reason', 'unknown'),
    ]
    incident_id = incident.get('incident_id')
    if incident_id:
        payload.extend(['--incident-id', str(incident_id)])
    flap = incident.get('flap') if isinstance(incident.get('flap'), dict) else {}
    if flap.get('detected'):
        payload.extend(['--flap', str(flap.get('transition_count', 0))])
    result = subprocess.run(payload, check=False)
    if result.returncode != 0:
        log(f"alert_failed name={incident.get('device', 'unknown')} state={incident.get('event', 'UNKNOWN')}")


def write_read_models(state, devices):
    READ_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        'up': sum(1 for item in state.values() if item.get('healthy') and item.get('reason') != 'disabled'),
        'down': sum(1 for item in state.values() if not item.get('healthy')),
        'disabled': sum(1 for item in state.values() if item.get('reason') == 'disabled'),
        'devices': len(state),
    }
    status_payload = {
        'generated_at': utc_now(),
        'summary': summary,
        'state': state,
        'devices': {item.get('name'): item for item in devices},
    }
    save_json(READ_MODEL_STATUS_PATH, status_payload)

    incidents = load_recent_incidents(days=90)
    incident_payload = {
        'generated_at': utc_now(),
        'items': incidents[-500:],
        'count': len(incidents),
    }
    save_json(READ_MODEL_INCIDENTS_PATH, incident_payload)

    rollups_payload = load_json(READ_MODEL_ROLLUPS_PATH, {})
    if not isinstance(rollups_payload, dict):
        rollups_payload = {}
    rollups_payload.setdefault('schema_version', 1)
    rollups_payload.setdefault('placeholder', True)
    rollups_payload.setdefault('retention_days', 365)
    rollups_payload.setdefault('items', [])
    rollups_payload['generated_at'] = utc_now()
    save_json(READ_MODEL_ROLLUPS_PATH, rollups_payload)


def main():
    config = load_json(CONFIG_PATH, {'devices': []})
    devices = normalize_devices(config.get('devices', []))
    old_state = load_json(STATE_PATH, {})
    new_state = {device['name']: evaluate_device(device) for device in devices}
    run_ts = utc_now()
    if collect_temperature_inventory is not None:
        try:
            temperature_inventory = collect_temperature_inventory(config, now_iso=run_ts)
            new_state = attach_temperature_inventory(new_state, temperature_inventory)
        except Exception as exc:
            log(f'temperature_inventory_failed error={exc}')
    previous_engine_state = load_json(INCIDENT_ENGINE_STATE_PATH, {})
    if derive_incidents is not None:
        incidents, engine_state = derive_incidents(old_state, new_state, now_ts=run_ts, engine_state=previous_engine_state)
    else:
        incidents, engine_state = _fallback_derive_incidents(old_state, new_state, now_ts=run_ts, engine_state=previous_engine_state)
    incidents.extend(derive_temperature_incidents(old_state, new_state, now_ts=run_ts))

    for incident in incidents:
        event = incident.get('event', '')
        if event in {'DOWN', 'RECOVERED'}:
            send_alert(incident)
        log(
            "transition name={name} state={state} reason={reason} flap={flap}".format(
                name=incident.get('device', 'unknown'),
                state=event or 'UNKNOWN',
                reason=incident.get('reason', 'unknown'),
                flap=bool((incident.get('flap') or {}).get('detected')),
            )
        )

    save_json(INCIDENT_ENGINE_STATE_PATH, engine_state)
    save_json(STATE_PATH, new_state)
    append_history(new_state, timestamp=run_ts)
    append_incidents(incidents)
    render_status_page(new_state, devices, config)
    write_read_models(new_state, devices)
    healthy = sum(1 for item in new_state.values() if item.get('healthy'))
    log(f"run devices={len(new_state)} healthy={healthy}")


if __name__ == '__main__':
    main()
