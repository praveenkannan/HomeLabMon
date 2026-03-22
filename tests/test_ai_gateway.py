import base64
import hashlib
import hmac
import importlib.util
import ipaddress
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BIN_DIR = Path(__file__).resolve().parents[1] / 'bin'
sys.path.insert(0, str(BIN_DIR))

import ai_capability
import ai_gateway


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def make_token(claims, key, kid='test-kid'):
    header = {'alg': 'HS256', 'typ': 'JWT', 'kid': kid}
    encoded_header = _b64url(json.dumps(header, separators=(',', ':'), sort_keys=True).encode('utf-8'))
    encoded_claims = _b64url(json.dumps(claims, separators=(',', ':'), sort_keys=True).encode('utf-8'))
    signing_input = f'{encoded_header}.{encoded_claims}'.encode('ascii')
    signature = hmac.new(key.encode('utf-8'), signing_input, hashlib.sha256).digest()
    return f'{encoded_header}.{encoded_claims}.{_b64url(signature)}'


def load_http_status_module():
    path = BIN_DIR / 'http_status.py'
    spec = importlib.util.spec_from_file_location('http_status_test_module', path)
    module = importlib.util.module_from_spec(spec)

    class DummyServer:
        def __init__(self, *args, **kwargs):
            self.socket = object()

        def serve_forever(self):
            return None

    class DummyContext:
        minimum_version = None

        def __init__(self, *args, **kwargs):
            pass

        def load_cert_chain(self, *args, **kwargs):
            return None

        def wrap_socket(self, sock, server_side=False):
            return sock

    with mock.patch('os.chdir', return_value=None), \
         mock.patch('http.server.ThreadingHTTPServer', DummyServer), \
         mock.patch('ssl.SSLContext', DummyContext):
        spec.loader.exec_module(module)
    return module


class CapabilityResolverTests(unittest.TestCase):
    def test_auto_mode_without_key_disables_ai_without_error(self):
        config = {
            'ai': {
                'ai_enabled': 'auto',
                'allow_ai_fallback': True,
                'ai_provider': 'openai-compatible',
                'ai_base_url': 'https://ai.example.invalid',
                'ai_model': 'ops-assistant-v1',
                'ai_timeout_ms': 5000,
            }
        }

        resolved = ai_capability.resolve_ai_mode(config, api_key='')

        self.assertEqual(resolved['mode'], 'AI_DISABLED')
        self.assertFalse(resolved['enabled'])
        self.assertEqual(resolved['reason'], 'missing_api_key')

    def test_forced_mode_without_key_raises_when_fallback_disallowed(self):
        config = {
            'ai': {
                'ai_enabled': 'true',
                'allow_ai_fallback': False,
                'ai_provider': 'openai-compatible',
                'ai_base_url': 'https://ai.example.invalid',
                'ai_model': 'ops-assistant-v1',
                'ai_timeout_ms': 5000,
            }
        }

        with self.assertRaises(ai_capability.AIConfigurationError):
            ai_capability.resolve_ai_mode(config, api_key='')

    def test_auto_mode_with_key_and_probe_enables_ai(self):
        config = {
            'ai': {
                'ai_enabled': 'auto',
                'allow_ai_fallback': True,
                'ai_provider': 'openai-compatible',
                'ai_base_url': 'https://ai.example.invalid',
                'ai_model': 'ops-assistant-v1',
                'ai_timeout_ms': 5000,
            }
        }

        resolved = ai_capability.resolve_ai_mode(
            config,
            api_key='secret',
            capability_probe=lambda *_args, **_kwargs: {'ok': True, 'status': 'ready'},
        )

        self.assertEqual(resolved['mode'], 'AI_ENABLED')
        self.assertTrue(resolved['enabled'])
        self.assertEqual(resolved['probe']['status'], 'ready')


class TokenVerificationTests(unittest.TestCase):
    def test_verify_signed_token_accepts_valid_token_once(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            replay_cache = ai_gateway.FileReplayCache(Path(tmpdir) / 'replay.json')
            claims = {
                'iss': 'homelab-dev',
                'aud': 'homelabmon-ai',
                'iat': 1_000,
                'exp': 1_120,
                'jti': 'jti-123',
                'scope': 'ai:chat',
            }
            token = make_token(claims, 'secret')

            accepted = ai_gateway.verify_signed_token(
                token=token,
                signing_keys={'test-kid': 'secret'},
                expected_issuer='homelab-dev',
                expected_audience='homelabmon-ai',
                required_scope='ai:chat',
                replay_cache=replay_cache,
                now=1_030,
            )

            self.assertEqual(accepted['jti'], 'jti-123')

            with self.assertRaises(ai_gateway.TokenVerificationError) as ctx:
                ai_gateway.verify_signed_token(
                    token=token,
                    signing_keys={'test-kid': 'secret'},
                    expected_issuer='homelab-dev',
                    expected_audience='homelabmon-ai',
                    required_scope='ai:chat',
                    replay_cache=replay_cache,
                    now=1_031,
                )

            self.assertEqual(ctx.exception.error_code, 'replay_detected')
            self.assertEqual(ctx.exception.status_code, 409)

    def test_verify_signed_token_rejects_wrong_issuer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            replay_cache = ai_gateway.FileReplayCache(Path(tmpdir) / 'replay.json')
            claims = {
                'iss': 'other-instance',
                'aud': 'homelabmon-ai',
                'iat': 1_000,
                'exp': 1_120,
                'jti': 'jti-issuer',
                'scope': 'ai:chat',
            }
            token = make_token(claims, 'secret')

            with self.assertRaises(ai_gateway.TokenVerificationError) as ctx:
                ai_gateway.verify_signed_token(
                    token=token,
                    signing_keys={'test-kid': 'secret'},
                    expected_issuer='homelab-dev',
                    expected_audience='homelabmon-ai',
                    required_scope='ai:chat',
                    replay_cache=replay_cache,
                    now=1_030,
                )

            self.assertEqual(ctx.exception.error_code, 'invalid_token')


class HttpScaffoldingTests(unittest.TestCase):
    def make_config(self):
        return {
            'instance_name': 'homelab-dev',
            'site_title': 'HomeLabMon Dev Dashboard',
            'dashboard_public_url': 'https://monitor.example.invalid/status.html',
            'timezone': 'America/Los_Angeles',
            'api_contract_version': '1.0',
            'feature_flags': {
                'ai_chat_enabled': False,
                'advanced_animations': True,
                'restart_actions_enabled': False,
            },
            'ai': {
                'ai_enabled': 'auto',
                'allow_ai_fallback': True,
                'ai_provider': 'openai-compatible',
                'ai_base_url': 'https://ai.example.invalid',
                'ai_model': 'ops-assistant-v1',
                'ai_timeout_ms': 5000,
            },
            'devices': [
                {
                    'id': 'pi-monitor',
                    'display_name': 'Pi Monitor',
                    'host': 'monitor.example.invalid',
                    'logo_url': './www/assets/pi.svg',
                    'checks': {'ping': True, 'tcp_ports': [22], 'http_urls': [], 'dns_names': []},
                    'action_policy': {
                        'allow_restart': True,
                        'allowed_actions': ['restart', 'run_check_now', 'snooze'],
                    },
                    'maintenance': {'mode': 'active', 'snooze_until': None},
                }
            ],
        }

    def test_capability_endpoint_reports_disabled_mode_without_key(self):
        module = load_http_status_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / 'devices.json'
            config_path.write_text(json.dumps(self.make_config()), encoding='utf-8')
            actions_path = Path(tmpdir) / 'actions.json'

            status_code, payload = module.handle_json_request(
                method='GET',
                path='/api/v1/ai/capability',
                headers={},
                body=b'',
                client_ip='100.100.10.10',
                config_path=config_path,
                action_state_path=actions_path,
                ai_api_key='',
                capability_probe=lambda *_args, **_kwargs: {'ok': True, 'status': 'ready'},
                allowed_cidrs=[ipaddress.ip_network('100.64.0.0/10')],
            )

            self.assertEqual(status_code, 200)
            self.assertEqual(payload['data']['mode'], 'AI_DISABLED')
            self.assertFalse(payload['data']['chat']['available'])

    def test_http_module_prefers_homelabmon_root_and_localhost_allowlist_defaults(self):
        with mock.patch.dict('os.environ', {'HOMELABMON_ROOT': '/srv/homelabmon'}, clear=True):
            module = load_http_status_module()

        self.assertEqual(str(module.ROOT), '/srv/homelabmon/www')
        self.assertEqual(str(module.CONFIG_PATH), '/srv/homelabmon/config/devices.local.json')
        self.assertEqual(
            [str(network) for network in module.ALLOWED_CIDRS],
            ['127.0.0.1/32'],
        )

    def test_action_propose_and_confirm_require_whitelist_and_human_confirmation(self):
        module = load_http_status_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / 'devices.json'
            config_path.write_text(json.dumps(self.make_config()), encoding='utf-8')
            actions_path = Path(tmpdir) / 'actions.json'

            propose_status, propose_payload = module.handle_json_request(
                method='POST',
                path='/api/v1/actions/propose',
                headers={},
                body=json.dumps({'device_id': 'pi-monitor', 'action': 'restart', 'reason': 'operator requested'}).encode('utf-8'),
                client_ip='100.100.10.10',
                config_path=config_path,
                action_state_path=actions_path,
                ai_api_key='',
                capability_probe=None,
                allowed_cidrs=[ipaddress.ip_network('100.64.0.0/10')],
            )

            self.assertEqual(propose_status, 202)
            self.assertEqual(propose_payload['data']['status'], 'pending_confirmation')
            action_id = propose_payload['data']['action_id']

            confirm_status, confirm_payload = module.handle_json_request(
                method='POST',
                path='/api/v1/actions/confirm',
                headers={'X-Admin-Token': 'operator-secret'},
                body=json.dumps({'action_id': action_id}).encode('utf-8'),
                client_ip='100.100.10.10',
                config_path=config_path,
                action_state_path=actions_path,
                admin_token='operator-secret',
                ai_api_key='',
                capability_probe=None,
                allowed_cidrs=[ipaddress.ip_network('100.64.0.0/10')],
            )

            self.assertEqual(confirm_status, 200)
            self.assertEqual(confirm_payload['data']['status'], 'confirmed')
            self.assertEqual(confirm_payload['data']['execution']['status'], 'not_executed')

    def test_legacy_restart_endpoint_is_not_available(self):
        module = load_http_status_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / 'devices.json'
            config_path.write_text(json.dumps(self.make_config()), encoding='utf-8')

            status_code, payload = module.handle_json_request(
                method='POST',
                path='/api/restart?device=pi-monitor',
                headers={'X-Admin-Token': 'operator-secret'},
                body=b'',
                client_ip='100.100.10.10',
                config_path=config_path,
                action_state_path=Path(tmpdir) / 'actions.json',
                admin_token='operator-secret',
                ai_api_key='',
                capability_probe=None,
                allowed_cidrs=[ipaddress.ip_network('100.64.0.0/10')],
            )

            self.assertEqual(status_code, 410)
            self.assertEqual(payload['error']['code'], 'legacy_endpoint_disabled')

    def test_selected_device_live_endpoint_returns_current_state(self):
        module = load_http_status_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / 'devices.json'
            config_path.write_text(json.dumps(self.make_config()), encoding='utf-8')
            status_path = Path(tmpdir) / 'status.json'
            status_path.write_text(json.dumps({
                'generated_at': '2026-03-22T04:00:00+00:00',
                'summary': {'up': 1, 'down': 0, 'disabled': 0, 'devices': 1},
                'devices': {
                    'pi-monitor': {
                        'name': 'pi-monitor',
                        'display_name': 'Pi Monitor',
                    }
                },
                'state': {
                    'pi-monitor': {
                        'healthy': True,
                        'reason': 'ping ok',
                        'checked_at': '2026-03-22T04:00:00+00:00',
                        'metrics': {
                            'ping': {'avg_ms': 0.8, 'loss_pct': 0.0},
                            'tcp': [{'port': 22, 'ok': True, 'latency_ms': 2.1}],
                            'http': [],
                            'dns': [],
                        },
                        'heat_state': 'normal',
                        'heat_value_c': 48.2,
                    }
                }
            }), encoding='utf-8')

            status_code, payload = module.handle_json_request(
                method='GET',
                path='/api/v1/devices/pi-monitor/live',
                headers={},
                body=b'',
                client_ip='100.100.10.10',
                config_path=config_path,
                action_state_path=Path(tmpdir) / 'actions.json',
                status_path=status_path,
                ai_api_key='',
                capability_probe=None,
                allowed_cidrs=[ipaddress.ip_network('100.64.0.0/10')],
            )

            self.assertEqual(status_code, 200)
            self.assertEqual(payload['data']['device_id'], 'pi-monitor')
            self.assertEqual(payload['data']['status'], 'UP')
            self.assertEqual(payload['data']['reason'], 'ping ok')
            self.assertEqual(payload['data']['metrics']['ping']['avg_ms'], 0.8)


if __name__ == '__main__':
    unittest.main()
