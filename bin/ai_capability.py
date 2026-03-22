#!/usr/bin/env python3
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

AI_DISABLED = 'AI_DISABLED'
AI_ENABLED = 'AI_ENABLED'


class AIConfigurationError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _ai_settings(config):
    ai = config.get('ai', {}) if isinstance(config, dict) else {}
    return ai if isinstance(ai, dict) else {}


def probe_remote_capability(ai_settings, api_key):
    base_url = str(ai_settings.get('ai_base_url', '')).rstrip('/')
    if not base_url:
        return {'ok': False, 'status': 'missing_base_url', 'reason': 'missing_base_url'}
    timeout = max(0.1, float(ai_settings.get('ai_timeout_ms', 5000)) / 1000.0)
    request = urllib.request.Request(
        f'{base_url}/capability',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Accept': 'application/json',
            'User-Agent': 'HomeLabMon/1.0',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return {'ok': False, 'status': 'unreachable', 'reason': str(exc)}
    if isinstance(payload, dict) and payload.get('ok') is True:
        return payload
    return {
        'ok': False,
        'status': payload.get('status', 'unavailable') if isinstance(payload, dict) else 'unavailable',
        'reason': payload.get('reason', 'probe_failed') if isinstance(payload, dict) else 'probe_failed',
    }


def resolve_ai_mode(config, api_key='', capability_probe=None):
    ai = _ai_settings(config)
    mode_setting = str(ai.get('ai_enabled', 'auto')).lower()
    allow_fallback = bool(ai.get('allow_ai_fallback', True))
    probe = {'ok': False, 'status': 'disabled'}
    base = {
        'mode': AI_DISABLED,
        'enabled': False,
        'provider': ai.get('ai_provider', ''),
        'base_url': ai.get('ai_base_url', ''),
        'model': ai.get('ai_model', ''),
        'timeout_ms': ai.get('ai_timeout_ms', 0),
        'probe': probe,
        'reason': 'disabled_by_config',
    }

    if mode_setting == 'false':
        return base
    if mode_setting not in {'auto', 'true'}:
        raise AIConfigurationError(f'unsupported ai_enabled mode: {mode_setting}')
    if not api_key:
        if mode_setting == 'true' and not allow_fallback:
            raise AIConfigurationError('AI is required but AI_API_KEY is missing')
        base['probe'] = {'ok': False, 'status': 'missing_api_key'}
        base['reason'] = 'missing_api_key'
        return base

    probe_fn = capability_probe or probe_remote_capability
    probe = probe_fn(ai, api_key)
    if isinstance(probe, dict) and probe.get('ok') is True:
        base['mode'] = AI_ENABLED
        base['enabled'] = True
        base['probe'] = probe
        base['reason'] = 'available'
        return base

    reason = 'capability_probe_failed'
    if isinstance(probe, dict):
        reason = probe.get('reason') or probe.get('status') or reason
    if mode_setting == 'true' and not allow_fallback:
        raise AIConfigurationError(f'AI is required but unavailable: {reason}')
    base['probe'] = probe if isinstance(probe, dict) else {'ok': False, 'status': 'unavailable'}
    base['reason'] = reason
    return base


def build_capability_payload(config, api_key='', capability_probe=None):
    resolved = resolve_ai_mode(config, api_key=api_key, capability_probe=capability_probe)
    return {
        'mode': resolved['mode'],
        'enabled': resolved['enabled'],
        'reason': resolved['reason'],
        'provider': resolved['provider'],
        'model': resolved['model'],
        'base_url': resolved['base_url'],
        'probe': resolved['probe'],
        'updated_at': utc_now(),
        'chat': {
            'available': resolved['enabled'],
            'transport': 'remote',
            'requires_signed_token': True,
        },
        'actions': {
            'propose_available': True,
            'confirm_available': True,
            'execution_mode': 'human_confirm_only',
        },
    }
