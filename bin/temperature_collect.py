#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = BASE_DIR / 'config' / 'devices.json'
DEFAULT_WARNING_C = 60.0
DEFAULT_CRITICAL_C = 75.0
DEFAULT_STALE_AFTER_S = 300
DEFAULT_COMMAND_TIMEOUT_S = 3.0
DEFAULT_HTTP_TIMEOUT_S = 2.0

CAPABILITY_SUPPORTED = 'supported'
CAPABILITY_UNSUPPORTED = 'unsupported'
CAPABILITY_UNAVAILABLE = 'unavailable'

HEAT_NORMAL = 'NORMAL'
HEAT_WARM = 'WARM'
HEAT_HOT = 'HOT'
HEAT_UNKNOWN = 'UNKNOWN'


class CollectionUnavailable(RuntimeError):
    pass


def utc_now_dt():
    return datetime.now(timezone.utc)


def utc_now_iso():
    return utc_now_dt().isoformat()


def _parse_iso(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _config_temperature_root(config):
    root = config.get('temperature', {})
    return root if isinstance(root, dict) else {}


def _device_temperature_config(config, device):
    root = _config_temperature_root(config)
    per_device = root.get('devices', {})
    merged = {}

    name = device.get('name')
    if isinstance(per_device, dict) and name in per_device and isinstance(per_device[name], dict):
        merged.update(per_device[name])

    nested = device.get('temperature')
    if isinstance(nested, dict):
        merged.update(nested)

    return merged


def resolve_thresholds(config, device):
    root = _config_temperature_root(config)
    defaults = root.get('defaults', {})
    defaults = defaults if isinstance(defaults, dict) else {}
    device_config = _device_temperature_config(config, device)

    warning_default = float(defaults.get('warning_c', DEFAULT_WARNING_C))
    critical_default = float(defaults.get('critical_c', DEFAULT_CRITICAL_C))
    stale_default = int(defaults.get('stale_after_s', DEFAULT_STALE_AFTER_S))

    warning_c = float(device_config.get('warning_c', warning_default))
    critical_c = float(device_config.get('critical_c', critical_default))
    stale_after_s = int(device_config.get('stale_after_s', stale_default))
    source = 'device' if any(key in device_config for key in ('warning_c', 'critical_c', 'stale_after_s')) else 'global'

    if warning_c >= critical_c:
        raise ValueError('temperature warning_c must be lower than critical_c')
    if stale_after_s < 30:
        raise ValueError('temperature stale_after_s must be at least 30 seconds')

    return {
        'warning_c': round(warning_c, 1),
        'critical_c': round(critical_c, 1),
        'stale_after_s': stale_after_s,
        'source': source,
    }


def _base_result(device, method, capability, thresholds, *, error=None):
    return {
        'device': device.get('name'),
        'display_name': device.get('display_name', device.get('name')),
        'host': device.get('host'),
        'method': method,
        'capability': capability,
        'error': error,
        'heat': {
            'state': HEAT_UNKNOWN,
            'value_c': None,
            'sampled_at': None,
            'thresholds': thresholds,
        },
    }


def _command_timeout(config, device_config):
    defaults = _config_temperature_root(config).get('defaults', {})
    defaults = defaults if isinstance(defaults, dict) else {}
    return float(device_config.get('command_timeout_s', defaults.get('command_timeout_s', DEFAULT_COMMAND_TIMEOUT_S)))


def _http_timeout(config, device_config):
    defaults = _config_temperature_root(config).get('defaults', {})
    defaults = defaults if isinstance(defaults, dict) else {}
    return float(device_config.get('http_timeout_s', defaults.get('http_timeout_s', DEFAULT_HTTP_TIMEOUT_S)))


def _resolve_snmpv3_credentials(device_config, env):
    username_key = device_config.get('username_env')
    auth_key = device_config.get('auth_password_env')
    privacy_key = device_config.get('privacy_password_env')

    if not username_key:
        raise CollectionUnavailable('missing SNMPv3 username_env')

    username = env.get(username_key)
    if not username:
        raise CollectionUnavailable(f'missing environment variable {username_key}')

    security_level = device_config.get('security_level')
    if not security_level:
        security_level = 'authPriv' if privacy_key else 'authNoPriv' if auth_key else 'noAuthNoPriv'

    auth_password = env.get(auth_key) if auth_key else None
    privacy_password = env.get(privacy_key) if privacy_key else None

    if security_level in {'authNoPriv', 'authPriv'} and not auth_password:
        raise CollectionUnavailable(f'missing environment variable {auth_key}')
    if security_level == 'authPriv' and not privacy_password:
        raise CollectionUnavailable(f'missing environment variable {privacy_key}')

    return {
        'username': username,
        'security_level': security_level,
        'auth_protocol': device_config.get('auth_protocol', 'SHA'),
        'auth_password': auth_password,
        'privacy_protocol': device_config.get('privacy_protocol', 'AES'),
        'privacy_password': privacy_password,
    }


def _parse_numeric(raw_value):
    matches = re.findall(r'-?\d+(?:\.\d+)?', str(raw_value))
    if not matches:
        raise CollectionUnavailable(f'no numeric temperature value found in {raw_value!r}')
    return float(matches[-1])


def _collect_snmpv3_sample(config, device, device_config, env, now_iso):
    host = device.get('host')
    oid = device_config.get('oid')
    if not host:
        raise CollectionUnavailable('device host is required for SNMP collection')
    if not oid:
        raise CollectionUnavailable('missing SNMP OID')

    snmpget_path = shutil.which('snmpget')
    if not snmpget_path:
        raise CollectionUnavailable('snmpget binary is not available on this host')

    creds = _resolve_snmpv3_credentials(device_config, env)
    timeout_s = max(1.0, _command_timeout(config, device_config))
    cmd = [
        snmpget_path,
        '-v3',
        '-Oqv',
        '-r',
        '0',
        '-t',
        str(timeout_s),
        '-l',
        creds['security_level'],
        '-u',
        creds['username'],
    ]

    if creds['security_level'] in {'authNoPriv', 'authPriv'}:
        cmd.extend(['-a', creds['auth_protocol'], '-A', creds['auth_password']])
    if creds['security_level'] == 'authPriv':
        cmd.extend(['-x', creds['privacy_protocol'], '-X', creds['privacy_password']])

    cmd.extend([host, oid])
    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_s + 1.0,
        check=False,
    )

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or 'snmpget failed').strip()
        raise CollectionUnavailable(message)

    scale = float(device_config.get('scale', 1.0))
    offset_c = float(device_config.get('offset_c', 0.0))
    value_c = _parse_numeric(completed.stdout) * scale + offset_c
    return {
        'value_c': round(value_c, 1),
        'sampled_at': now_iso,
    }


def _json_field(payload, field_name, fallback_names):
    if field_name:
        return payload.get(field_name)
    for name in fallback_names:
        if name in payload:
            return payload[name]
    return None


def _collect_mac_api_sample(config, device_config):
    url = device_config.get('url')
    if not url:
        raise CollectionUnavailable('missing mac_api url')

    timeout_s = max(0.2, _http_timeout(config, device_config))
    with urlopen(url, timeout=timeout_s) as response:
        raw = response.read().decode('utf-8')
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise CollectionUnavailable('mac_api response must be a JSON object')

    value = _json_field(payload, device_config.get('field'), ('temperature_c', 'temp_c', 'value_c'))
    sampled_at = _json_field(payload, device_config.get('sampled_at_field'), ('sampled_at', 'timestamp', 'collected_at'))
    return {
        'value_c': round(float(value), 1),
        'sampled_at': sampled_at,
    }


def _collect_local_file_sample(device_config, now_iso):
    paths = device_config.get('paths') or []
    if not isinstance(paths, list) or not paths:
        raise CollectionUnavailable('missing local_file paths')

    for raw_path in paths:
        try:
            raw = Path(raw_path).read_text(encoding='utf-8').strip()
        except OSError:
            continue
        scale = float(device_config.get('scale', 1.0))
        if scale == 1.0 and raw.isdigit() and float(raw) > 1000:
            scale = 0.001
        value_c = _parse_numeric(raw) * scale + float(device_config.get('offset_c', 0.0))
        return {
            'value_c': round(value_c, 1),
            'sampled_at': now_iso,
        }

    raise CollectionUnavailable('no readable local_file temperature path')


def _evaluate_heat(sample, thresholds, now_dt):
    sampled_at = _parse_iso(sample.get('sampled_at'))
    value_c = sample.get('value_c')
    if value_c is None or sampled_at is None:
        return {
            'state': HEAT_UNKNOWN,
            'value_c': None,
            'sampled_at': None,
            'thresholds': thresholds,
        }

    age_s = (now_dt - sampled_at).total_seconds()
    if age_s > thresholds['stale_after_s']:
        return {
            'state': HEAT_UNKNOWN,
            'value_c': None,
            'sampled_at': None,
            'thresholds': thresholds,
        }

    value_c = round(float(value_c), 1)
    state = HEAT_NORMAL
    if value_c >= thresholds['critical_c']:
        state = HEAT_HOT
    elif value_c >= thresholds['warning_c']:
        state = HEAT_WARM

    return {
        'state': state,
        'value_c': value_c,
        'sampled_at': sampled_at.isoformat(),
        'thresholds': thresholds,
    }


def collect_device_temperature(config, device, *, env=None, now_iso=None):
    env = os.environ if env is None else env
    now_iso = now_iso or utc_now_iso()
    now_dt = _parse_iso(now_iso) or utc_now_dt()

    thresholds = resolve_thresholds(config, device)
    device_config = _device_temperature_config(config, device)
    if not device_config or device_config.get('enabled', True) is False:
        return _base_result(device, 'none', CAPABILITY_UNSUPPORTED, thresholds)

    method = device_config.get('method', '').strip()
    if method not in {'snmpv3', 'mac_api', 'local_file'}:
        return _base_result(device, method or 'none', CAPABILITY_UNSUPPORTED, thresholds, error='unsupported temperature method')

    try:
        if method == 'snmpv3':
            sample = _collect_snmpv3_sample(config, device, device_config, env, now_iso)
        elif method == 'mac_api':
            sample = _collect_mac_api_sample(config, device_config)
        else:
            sample = _collect_local_file_sample(device_config, now_iso)
    except (CollectionUnavailable, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError, HTTPError, URLError, OSError) as exc:
        return _base_result(device, method, CAPABILITY_UNAVAILABLE, thresholds, error=str(exc))

    result = _base_result(device, method, CAPABILITY_SUPPORTED, thresholds)
    result['heat'] = _evaluate_heat(sample, thresholds, now_dt)
    return result


def collect_temperature_inventory(config, *, env=None, now_iso=None):
    now_iso = now_iso or utc_now_iso()
    results = {}
    for device in config.get('devices', []):
        name = device.get('name')
        if not name:
            continue
        results[name] = collect_device_temperature(config, device, env=env, now_iso=now_iso)
    return {
        'generated_at': now_iso,
        'devices': results,
    }


def load_config(path):
    with Path(path).open('r', encoding='utf-8') as handle:
        return json.load(handle)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Collect device temperature telemetry for HomeLabMon.')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG_PATH), help='Path to devices.json')
    parser.add_argument('--device', action='append', default=[], help='Optional device name filter')
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.device:
        wanted = set(args.device)
        config = dict(config)
        config['devices'] = [device for device in config.get('devices', []) if device.get('name') in wanted]

    payload = collect_temperature_inventory(config)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
