#!/usr/bin/env python3
import json
import os
from pathlib import Path

DEFAULT_RUNTIME_ROOT = Path('/opt/homelabmon')
LEGACY_RUNTIME_ROOT = Path('/opt/pi-monitor')
LEGACY_CERT_NAMES = {
    'homelabmon.crt': 'vphomemonitor.crt',
    'homelabmon.key': 'vphomemonitor.key',
}


def env_value(*names, default=None, env=None):
    source = os.environ if env is None else env
    for name in names:
        value = source.get(name)
        if value is None:
            continue
        value = str(value).strip()
        if value:
            return value
    return default


def runtime_root(env=None):
    configured = env_value('HOMELABMON_ROOT', 'PI_MONITOR_ROOT', env=env)
    if configured:
        return Path(configured)
    if LEGACY_RUNTIME_ROOT.exists() and not DEFAULT_RUNTIME_ROOT.exists():
        return LEGACY_RUNTIME_ROOT
    return DEFAULT_RUNTIME_ROOT


def runtime_path(*parts, root=None, env=None):
    base = Path(root) if root is not None else runtime_root(env=env)
    return base.joinpath(*parts)


def config_path(env=None):
    configured = env_value('HOMELABMON_CONFIG_PATH', 'PI_MONITOR_CONFIG_PATH', env=env)
    if configured:
        path = Path(configured)
        if path.is_absolute():
            return path
        if env_value('HOMELABMON_ROOT', 'PI_MONITOR_ROOT', env=env):
            return runtime_path(*path.parts, env=env)
        return Path(*path.parts)

    local_path = runtime_path('config', 'devices.local.json', env=env)
    legacy_path = runtime_path('config', 'devices.json', env=env)
    if local_path.exists() or not legacy_path.exists():
        return local_path
    return legacy_path


def load_config(path=None, env=None):
    config_file = Path(path) if path is not None else config_path(env=env)
    if not config_file.exists():
        return {}
    try:
        return json.loads(config_file.read_text(encoding='utf-8'))
    except Exception:
        return {}


def dashboard_url(config=None, env=None):
    configured = env_value('HOMELABMON_DASHBOARD_URL', 'PI_MONITOR_DASHBOARD_URL', env=env)
    if configured is not None:
        return configured
    payload = config if config is not None else load_config(env=env)
    if isinstance(payload, dict):
        url = payload.get('dashboard_public_url')
        if isinstance(url, str) and url.strip():
            return url.strip()
    return ''


def certificate_path(filename, *, env_name, legacy_env_name, env=None):
    configured = env_value(env_name, legacy_env_name, env=env)
    if configured:
        return configured
    path = runtime_path('certs', filename, env=env)
    if path.exists():
        return str(path)
    legacy_name = LEGACY_CERT_NAMES.get(filename)
    if legacy_name:
        legacy_path = runtime_path('certs', legacy_name, env=env)
        if legacy_path.exists():
            return str(legacy_path)
    return str(path)
