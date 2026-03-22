import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))

import runtime_config
import check_devices


ROOT = Path(__file__).resolve().parents[1]


class ConfigContractTests(unittest.TestCase):
    def test_schema_includes_temperature_configuration(self):
        schema = json.loads((ROOT / 'config' / 'config.schema.json').read_text(encoding='utf-8'))
        self.assertIn('temperature', schema.get('properties', {}))

    def test_env_example_points_to_local_inventory_file(self):
        env_example = (ROOT / 'config' / 'env.example').read_text(encoding='utf-8')
        self.assertIn('HOMELABMON_CONFIG_PATH=./config/devices.local.json', env_example)

    def test_runtime_config_resolves_relative_local_inventory_against_runtime_root(self):
        env = {
            'HOMELABMON_ROOT': '/srv/homelabmon',
            'HOMELABMON_CONFIG_PATH': './config/devices.local.json',
        }

        path = runtime_config.config_path(env=env)

        self.assertEqual(path, Path('/srv/homelabmon/config/devices.local.json'))

    def test_runtime_config_keeps_relative_local_inventory_relative_without_runtime_root(self):
        env = {
            'HOMELABMON_CONFIG_PATH': './config/devices.local.json',
        }

        path = runtime_config.config_path(env=env)

        self.assertEqual(path, Path('config/devices.local.json'))

    def test_example_config_drives_monitor_with_schema_shape(self):
        example = json.loads((ROOT / 'config' / 'devices.example.json').read_text(encoding='utf-8'))

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            config_dir = base / 'config'
            state_dir = base / 'state'
            www_dir = base / 'www'
            bin_dir = base / 'bin'
            for path in (config_dir, state_dir, www_dir, bin_dir):
                path.mkdir(parents=True, exist_ok=True)
            config_path = config_dir / 'devices.local.json'
            config_path.write_text(json.dumps(example), encoding='utf-8')
            alert_script = bin_dir / 'send_alert.py'
            alert_script.write_text('#!/usr/bin/env python3\n', encoding='utf-8')

            with ExitStack() as stack:
                patches = [
                    mock.patch.object(check_devices, 'BASE', base),
                    mock.patch.object(check_devices, 'CONFIG_PATH', config_path),
                    mock.patch.object(check_devices, 'STATE_DIR', state_dir),
                    mock.patch.object(check_devices, 'STATE_PATH', state_dir / 'status.json'),
                    mock.patch.object(check_devices, 'HISTORY_PATH', state_dir / 'history.jsonl'),
                    mock.patch.object(check_devices, 'HISTORY_DAY_DIR', state_dir / 'history'),
                    mock.patch.object(check_devices, 'INCIDENTS_PATH', state_dir / 'incidents.jsonl'),
                    mock.patch.object(check_devices, 'INCIDENTS_DAY_DIR', state_dir / 'incidents'),
                    mock.patch.object(check_devices, 'INCIDENT_ENGINE_STATE_PATH', state_dir / 'incident_engine_state.json'),
                    mock.patch.object(check_devices, 'READ_MODEL_DIR', state_dir / 'read_model'),
                    mock.patch.object(check_devices, 'READ_MODEL_STATUS_PATH', state_dir / 'read_model' / 'status.json'),
                    mock.patch.object(check_devices, 'READ_MODEL_INCIDENTS_PATH', state_dir / 'read_model' / 'incidents.json'),
                    mock.patch.object(check_devices, 'READ_MODEL_ROLLUPS_PATH', state_dir / 'read_model' / 'rollups.json'),
                    mock.patch.object(check_devices, 'LOG_PATH', state_dir / 'monitor.log'),
                    mock.patch.object(check_devices, 'ALERT_SCRIPT', alert_script),
                    mock.patch.object(check_devices, 'WWW_PATH', www_dir),
                    mock.patch.object(check_devices, 'STATUS_HTML', www_dir / 'status.html'),
                    mock.patch.object(check_devices, 'ping_check', return_value=(True, 'ping ok', {'ok': True})),
                    mock.patch.object(check_devices, 'tcp_check', return_value=(True, 'tcp ok', {'ok': True, 'port': 22})),
                    mock.patch.object(check_devices, 'http_check', return_value=(True, 'http:200', {'ok': True, 'status': 200})),
                    mock.patch.object(check_devices, 'dns_check', return_value=(True, 'dns ok', {'ok': True, 'answers': ['127.0.0.1']})),
                    mock.patch.object(check_devices, 'collect_pi_host_metrics', return_value={}),
                    mock.patch.object(check_devices, 'send_alert', return_value=None),
                ]
                for patcher in patches:
                    stack.enter_context(patcher)
                check_devices.main()

            state = json.loads((state_dir / 'status.json').read_text(encoding='utf-8'))
            self.assertEqual(sorted(state.keys()), ['pi-monitor', 'router'])
            self.assertEqual(json.loads((state_dir / 'read_model' / 'status.json').read_text(encoding='utf-8'))['devices']['router']['display_name'], 'Edge Router')

    def test_run_monitor_uses_portable_binary_resolution(self):
        script = (ROOT / 'bin' / 'run_monitor.sh').read_text(encoding='utf-8')

        self.assertIn('command -v flock', script)
        self.assertIn('command -v python3', script)
        self.assertNotIn('/usr/bin/flock', script)
        self.assertNotIn('/usr/bin/python3', script)

    def test_burnin_validation_uses_generic_service_name(self):
        script = (ROOT / 'bin' / 'run_burnin_validation.sh').read_text(encoding='utf-8')

        self.assertIn('SERVICE_NAME=', script)
        self.assertIn('systemctl is-active \"$SERVICE_NAME\"', script)
        self.assertNotIn('systemctl is-active pi-monitor-status', script)

    def test_public_templates_do_not_embed_personal_or_overlay_network_identifiers(self):
        files = [
            ROOT / 'config' / 'devices.example.json',
            ROOT / 'docs' / 'contracts' / 'api-v1.md',
            ROOT / 'www' / 'index.html',
            ROOT / 'bin' / 'security_verify.sh',
        ]

        for path in files:
            content = path.read_text(encoding='utf-8')
            self.assertNotIn('tail' + 'net.example', content, msg=f'{path} unexpectedly contains overlay example hostnames')
            self.assertNotIn('tail' + 'scale', content, msg=f'{path} unexpectedly contains overlay network branding')
            self.assertNotIn('VP' + 'HouseMonitor', content, msg=f'{path} unexpectedly contains personal monitor title')
            self.assertNotRegex(content, rf'(?i)\b{"vp"}{"housemonitor"}\b', msg=f'{path} unexpectedly contains a personal host identifier')
            self.assertNotRegex(content, r'tail[0-9a-f]{6,}', msg=f'{path} unexpectedly contains a personal overlay suffix')

    def test_gitignore_excludes_generated_dashboard_html(self):
        ignore = (ROOT / '.gitignore').read_text(encoding='utf-8')

        self.assertIn('www/status.html', ignore)


if __name__ == '__main__':
    unittest.main()
