#!/usr/bin/env python3
import functools
import json
import os
import ssl
import uuid
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address, ip_network
from pathlib import Path
from urllib.parse import urlparse

from ai_capability import AIConfigurationError, build_capability_payload
from runtime_config import certificate_path, config_path as resolve_config_path, env_value, runtime_path

API_VERSION = '1.0'
ALLOWED_ACTIONS = {'restart', 'run_check_now', 'snooze', 'maintenance_on', 'maintenance_off'}

ROOT = runtime_path('www')
CONFIG_PATH = resolve_config_path()
READ_MODEL_STATUS_PATH = runtime_path('state', 'read_model', 'status.json')
READ_MODEL_INCIDENTS_PATH = runtime_path('state', 'read_model', 'incidents.json')
READ_MODEL_ROLLUPS_PATH = runtime_path('state', 'read_model', 'rollups.json')
STATUS_PATH = runtime_path('state', 'status.json')
ACTION_STATE_PATH = runtime_path('state', 'actions', 'pending_actions.json')

BIND_HOST = env_value('HOMELABMON_BIND_HOST', 'PI_MONITOR_BIND_HOST', default='127.0.0.1')
BIND_PORT = int(env_value('HOMELABMON_BIND_PORT', 'PI_MONITOR_BIND_PORT', default='8081'))
CERT_FILE = certificate_path('homelabmon.crt', env_name='HOMELABMON_TLS_CERT', legacy_env_name='PI_MONITOR_TLS_CERT')
KEY_FILE = certificate_path('homelabmon.key', env_name='HOMELABMON_TLS_KEY', legacy_env_name='PI_MONITOR_TLS_KEY')
ALLOWED_CIDRS = [
    ip_network(cidr.strip())
    for cidr in env_value('HOMELABMON_ALLOWED_CIDRS', 'PI_MONITOR_ALLOWED_CIDRS', default='127.0.0.1/32').split(',')
    if cidr.strip()
]
ADMIN_TOKEN = env_value('HOMELABMON_ADMIN_TOKEN', 'PI_MONITOR_ADMIN_TOKEN', default='') or ''
AI_API_KEY = os.getenv('AI_API_KEY', '')


def load_devices(config_path=CONFIG_PATH):
    try:
        data = json.loads(Path(config_path).read_text(encoding='utf-8'))
    except Exception:
        data = {}
    items = data.get('devices', []) if isinstance(data, dict) else []
    devices = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        device_id = str(item.get('id') or item.get('name') or '').strip()
        if not device_id:
            continue
        action_policy = item.get('action_policy') if isinstance(item.get('action_policy'), dict) else {}
        allowed = [action for action in action_policy.get('allowed_actions', []) if action in ALLOWED_ACTIONS]
        if action_policy.get('allow_restart') and 'restart' not in allowed:
            allowed.append('restart')
        restart = item.get('restart') if isinstance(item.get('restart'), dict) else {}
        if restart and restart.get('type') in {'command', 'http'} and 'restart' not in allowed:
            allowed.append('restart')
        devices[device_id] = {
            'id': device_id,
            'display_name': item.get('display_name') or item.get('name') or device_id,
            'allowed_actions': sorted(set(allowed)),
            'raw': item,
        }
    return devices


def load_runtime_config(config_path=CONFIG_PATH):
    return load_json(Path(config_path), {})


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def default_incidents_payload():
    return {'generated_at': utc_now(), 'items': [], 'count': 0, 'placeholder': True}


def default_rollups_payload():
    return {'generated_at': utc_now(), 'items': [], 'placeholder': True, 'retention_days': 365}


def default_status_payload():
    raw_state = load_json(STATUS_PATH, {})
    if not isinstance(raw_state, dict):
        raw_state = {}
    summary = {
        'up': sum(1 for item in raw_state.values() if isinstance(item, dict) and item.get('healthy') and item.get('reason') != 'disabled'),
        'down': sum(1 for item in raw_state.values() if isinstance(item, dict) and not item.get('healthy')),
        'disabled': sum(1 for item in raw_state.values() if isinstance(item, dict) and item.get('reason') == 'disabled'),
        'devices': len(raw_state),
    }
    return {'generated_at': utc_now(), 'summary': summary, 'state': raw_state, 'placeholder': True}


def live_device_payload(device_id, status_path=READ_MODEL_STATUS_PATH, config_path=CONFIG_PATH):
    status_payload = load_json(status_path, default_status_payload())
    state = status_payload.get('state', {}) if isinstance(status_payload, dict) else {}
    devices = status_payload.get('devices', {}) if isinstance(status_payload, dict) else {}
    item = state.get(device_id)
    if not isinstance(item, dict):
        return None
    device_meta = devices.get(device_id) if isinstance(devices, dict) else None
    if not isinstance(device_meta, dict):
        device_meta = load_devices(config_path).get(device_id, {})
    reason = item.get('reason', 'unknown')
    if reason == 'disabled':
        status = 'DISABLED'
    elif item.get('healthy'):
        status = 'UP'
    else:
        status = 'DOWN'
    return {
        'device_id': device_id,
        'display_name': device_meta.get('display_name') or item.get('display_name') or device_id,
        'slug': device_id,
        'status': status,
        'healthy': bool(item.get('healthy')),
        'reason': reason,
        'checked_at': item.get('checked_at', ''),
        'metrics': item.get('metrics', {}),
        'heat_state': item.get('heat_state'),
        'heat_value_c': item.get('heat_value_c'),
        'generated_at': status_payload.get('generated_at', utc_now()),
    }


def api_payload(path):
    if path == '/api/status':
        return load_json(READ_MODEL_STATUS_PATH, default_status_payload())
    if path == '/api/incidents':
        return load_json(READ_MODEL_INCIDENTS_PATH, default_incidents_payload())
    if path == '/api/rollups':
        return load_json(READ_MODEL_ROLLUPS_PATH, default_rollups_payload())
    return None


def request_id_from_headers(headers):
    for key, value in headers.items():
        if key.lower() == 'x-request-id' and value:
            return value
    return f"req_{uuid.uuid4().hex[:12]}"


def success_envelope(data, request_id=None):
    return {
        'api_version': API_VERSION,
        'request_id': request_id or f"req_{uuid.uuid4().hex[:12]}",
        'generated_at': utc_now(),
        'data': data,
    }


def error_envelope(code, message, retryable=False, request_id=None):
    return {
        'api_version': API_VERSION,
        'request_id': request_id or f"req_{uuid.uuid4().hex[:12]}",
        'generated_at': utc_now(),
        'error': {
            'code': code,
            'message': message,
            'retryable': retryable,
        },
    }


def load_action_state(path):
    path = Path(path)
    state = load_json(path, {'items': []})
    items = state.get('items', []) if isinstance(state, dict) else []
    return {'items': [item for item in items if isinstance(item, dict)]}


def save_action_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding='utf-8')
    tmp.replace(path)


def parse_json_body(body):
    if not body:
        return {}
    try:
        payload = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise ValueError('request body must be valid JSON')
    if not isinstance(payload, dict):
        raise ValueError('request body must be a JSON object')
    return payload


def resolve_capability(config_path, ai_api_key='', capability_probe=None):
    config = load_runtime_config(config_path)
    payload = build_capability_payload(config, api_key=ai_api_key, capability_probe=capability_probe)
    payload['contract_version'] = API_VERSION
    return payload


def load_admin_token(admin_token=None):
    return ADMIN_TOKEN if admin_token is None else admin_token


def normalize_headers(headers):
    return {str(key): value for key, value in (headers or {}).items()}


def action_record(device_id, action, reason, request_id, actor='operator', params=None):
    return {
        'action_id': f"act_{uuid.uuid4().hex[:12]}",
        'device_id': device_id,
        'action': action,
        'reason': reason,
        'requested_by': actor,
        'requested_at': utc_now(),
        'request_id': request_id,
        'status': 'pending_confirmation',
        'params': params or {},
    }


def handle_action_propose(body, config_path, action_state_path, request_id):
    payload = parse_json_body(body)
    device_id = str(payload.get('device_id', '')).strip()
    action = str(payload.get('action', '')).strip()
    reason = str(payload.get('reason', '')).strip()
    if not device_id or not action:
        return 400, error_envelope('invalid_request', 'device_id and action are required', request_id=request_id)
    if action not in ALLOWED_ACTIONS:
        return 409, error_envelope('action_not_allowed', f'action {action} is not whitelisted', request_id=request_id)

    devices = load_devices(config_path)
    device = devices.get(device_id)
    if not device:
        return 404, error_envelope('device_not_found', f'unknown device {device_id}', request_id=request_id)
    if action not in device['allowed_actions']:
        return 409, error_envelope('action_not_allowed', f'action {action} is not enabled for {device_id}', request_id=request_id)
    if action == 'snooze' and not payload.get('snooze_until'):
        return 400, error_envelope('invalid_request', 'snooze_until is required for snooze', request_id=request_id)

    state = load_action_state(action_state_path)
    record = action_record(
        device_id=device_id,
        action=action,
        reason=reason or 'operator requested',
        request_id=request_id,
        params={key: value for key, value in payload.items() if key not in {'device_id', 'action', 'reason'}},
    )
    state['items'].append(record)
    save_action_state(action_state_path, state)
    return 202, success_envelope(
        {
            'action_id': record['action_id'],
            'device_id': device_id,
            'action': action,
            'status': record['status'],
            'confirmation_required': True,
        },
        request_id=request_id,
    )


def handle_action_confirm(body, headers, action_state_path, request_id, admin_token=None):
    token = load_admin_token(admin_token)
    provided = headers.get('X-Admin-Token', '')
    if not token or provided != token:
        return 401, error_envelope('unauthorized', 'operator confirmation token required', request_id=request_id)

    payload = parse_json_body(body)
    action_id = str(payload.get('action_id', '')).strip()
    if not action_id:
        return 400, error_envelope('invalid_request', 'action_id is required', request_id=request_id)

    state = load_action_state(action_state_path)
    for item in state['items']:
        if item.get('action_id') != action_id:
            continue
        if item.get('status') != 'pending_confirmation':
            return 409, error_envelope('action_not_pending', f'action {action_id} is not pending confirmation', request_id=request_id)
        item['status'] = 'confirmed'
        item['confirmed_at'] = utc_now()
        item['execution'] = {
            'status': 'not_executed',
            'reason': 'execution remains human-confirmed and is not automated in Pi scaffolding',
        }
        save_action_state(action_state_path, state)
        return 200, success_envelope(
            {
                'action_id': action_id,
                'device_id': item.get('device_id'),
                'action': item.get('action'),
                'status': item['status'],
                'execution': item['execution'],
            },
            request_id=request_id,
        )

    return 404, error_envelope('action_not_found', f'unknown action {action_id}', request_id=request_id)


def client_allowed(client_ip, allowed_cidrs=None):
    networks = allowed_cidrs if allowed_cidrs is not None else ALLOWED_CIDRS
    ip = ip_address(client_ip)
    return any(ip in network for network in networks)


def handle_json_request(
    method,
    path,
    headers,
    body,
    client_ip,
    config_path=CONFIG_PATH,
    action_state_path=ACTION_STATE_PATH,
    admin_token=None,
    ai_api_key='',
    capability_probe=None,
    allowed_cidrs=None,
    status_path=READ_MODEL_STATUS_PATH,
):
    headers = normalize_headers(headers)
    request_id = request_id_from_headers(headers)
    if not client_allowed(client_ip, allowed_cidrs=allowed_cidrs):
        return 403, error_envelope('forbidden', 'client address not allowed', request_id=request_id)

    parsed = urlparse(path)
    if parsed.path == '/api/restart':
        return 410, error_envelope(
            'legacy_endpoint_disabled',
            'legacy restart endpoint is disabled; use /api/v1/actions/propose and /api/v1/actions/confirm',
            request_id=request_id,
        )

    if method == 'GET' and parsed.path == '/api/v1/ai/capability':
        try:
            payload = resolve_capability(config_path, ai_api_key=ai_api_key, capability_probe=capability_probe)
        except AIConfigurationError as exc:
            return 503, error_envelope('ai_configuration_error', str(exc), request_id=request_id)
        return 200, success_envelope(payload, request_id=request_id)

    if method == 'GET' and parsed.path.startswith('/api/v1/devices/') and parsed.path.endswith('/live'):
        parts = [part for part in parsed.path.split('/') if part]
        if len(parts) == 5:
          device_id = parts[3]
          payload = live_device_payload(device_id, status_path=status_path, config_path=config_path)
          if payload is None:
              return 404, error_envelope('device_not_found', f'unknown device {device_id}', request_id=request_id)
          return 200, success_envelope(payload, request_id=request_id)

    if method == 'GET':
        payload = api_payload(parsed.path)
        if payload is not None:
            return 200, success_envelope(payload, request_id=request_id)

    if method == 'POST' and parsed.path == '/api/v1/actions/propose':
        return handle_action_propose(body, config_path, action_state_path, request_id)

    if method == 'POST' and parsed.path == '/api/v1/actions/confirm':
        return handle_action_confirm(body, headers, action_state_path, request_id, admin_token=admin_token)

    return 404, error_envelope('not_found', f'no route for {method} {parsed.path}', request_id=request_id)


def json_response(handler, status_code, payload):
    body = json.dumps(payload, sort_keys=True).encode('utf-8')
    handler.send_response(status_code)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-API-Version', API_VERSION)
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class GuardedHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=directory or str(ROOT), **kwargs)

    def _allowed(self):
        client_ip = ip_address(self.client_address[0])
        return any(client_ip in network for network in ALLOWED_CIDRS)

    def _deny(self):
        self.send_response(403)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b'forbidden')

    def end_headers(self):
        self.send_header('Strict-Transport-Security', 'max-age=31536000')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        super().end_headers()

    def do_GET(self):
        if not self._allowed():
            return self._deny()
        parsed = urlparse(self.path)
        if parsed.path.startswith('/api/'):
            status_code, payload = handle_json_request(
                method='GET',
                path=self.path,
                headers=self.headers,
                body=b'',
                client_ip=self.client_address[0],
                ai_api_key=AI_API_KEY,
            )
            return json_response(self, status_code, payload)
        return super().do_GET()

    def do_HEAD(self):
        if not self._allowed():
            return self._deny()
        return super().do_HEAD()

    def do_POST(self):
        if not self._allowed():
            return self._deny()
        content_length = int(self.headers.get('Content-Length', '0') or 0)
        body = self.rfile.read(content_length) if content_length > 0 else b''
        status_code, payload = handle_json_request(
            method='POST',
            path=self.path,
            headers=self.headers,
            body=body,
            client_ip=self.client_address[0],
            admin_token=ADMIN_TOKEN,
            ai_api_key=AI_API_KEY,
        )
        return json_response(self, status_code, payload)

    def log_message(self, fmt, *args):
        pass


def build_server():
    handler = functools.partial(GuardedHandler, directory=str(ROOT))
    server = ThreadingHTTPServer((BIND_HOST, BIND_PORT), handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=CERT_FILE, keyfile=KEY_FILE)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    return server


def main():
    server = build_server()
    server.serve_forever()


if __name__ == '__main__':
    main()
