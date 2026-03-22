import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import temperature_collect


class ThresholdResolutionTests(unittest.TestCase):
    def test_resolve_thresholds_prefers_per_device_overrides(self):
        config = {
            'temperature': {
                'defaults': {
                    'warning_c': 60.0,
                    'critical_c': 75.0,
                    'stale_after_s': 300,
                },
                'devices': {
                    'asus-router': {
                        'warning_c': 55.0,
                        'critical_c': 70.0,
                    }
                },
            }
        }
        device = {'name': 'asus-router'}

        thresholds = temperature_collect.resolve_thresholds(config, device)

        self.assertEqual(
            thresholds,
            {
                'warning_c': 55.0,
                'critical_c': 70.0,
                'stale_after_s': 300,
                'source': 'device',
            },
        )

    def test_resolve_thresholds_uses_global_defaults_without_override(self):
        config = {
            'temperature': {
                'defaults': {
                    'warning_c': 60.0,
                    'critical_c': 75.0,
                    'stale_after_s': 300,
                }
            }
        }
        device = {'name': 'synology-nas'}

        thresholds = temperature_collect.resolve_thresholds(config, device)

        self.assertEqual(
            thresholds,
            {
                'warning_c': 60.0,
                'critical_c': 75.0,
                'stale_after_s': 300,
                'source': 'global',
            },
        )


class CapabilityClassificationTests(unittest.TestCase):
    def test_classify_capability_marks_device_without_temperature_config_unsupported(self):
        config = {'temperature': {'defaults': {'warning_c': 60.0, 'critical_c': 75.0, 'stale_after_s': 300}}}
        device = {'name': 'arlo-hub'}

        result = temperature_collect.collect_device_temperature(config, device, env={}, now_iso='2026-03-22T03:00:00+00:00')

        self.assertEqual(result['capability'], 'unsupported')
        self.assertEqual(result['heat']['state'], 'UNKNOWN')
        self.assertEqual(result['method'], 'none')

    @patch('temperature_collect.shutil.which', return_value=None)
    def test_snmp_device_without_snmpget_binary_is_unavailable(self, mock_which):
        config = {
            'temperature': {
                'defaults': {'warning_c': 60.0, 'critical_c': 75.0, 'stale_after_s': 300},
                'devices': {
                    'synology-nas': {
                        'method': 'snmpv3',
                        'oid': '1.3.6.1.4.1.6574.1.2.0',
                        'username_env': 'HLM_SYNOLOGY_SNMP_USER',
                        'auth_password_env': 'HLM_SYNOLOGY_SNMP_AUTH',
                        'privacy_password_env': 'HLM_SYNOLOGY_SNMP_PRIV',
                    }
                },
            }
        }
        device = {'name': 'synology-nas', 'host': '198.51.100.177'}

        result = temperature_collect.collect_device_temperature(config, device, env={}, now_iso='2026-03-22T03:00:00+00:00')

        self.assertEqual(result['capability'], 'unavailable')
        self.assertEqual(result['heat']['state'], 'UNKNOWN')
        self.assertIn('snmpget', result['error'])


class AdapterCollectionTests(unittest.TestCase):
    @patch('temperature_collect.subprocess.run')
    @patch('temperature_collect.shutil.which', return_value='/usr/bin/snmpget')
    def test_snmpv3_collection_parses_numeric_output_and_sets_hot_state(self, mock_which, mock_run):
        mock_run.return_value = Mock(
            returncode=0,
            stdout='SNMPv2-SMI::enterprises.6574.1.2.0 = INTEGER: 71\n',
            stderr='',
        )
        config = {
            'temperature': {
                'defaults': {'warning_c': 60.0, 'critical_c': 75.0, 'stale_after_s': 300},
                'devices': {
                    'synology-nas': {
                        'method': 'snmpv3',
                        'oid': '1.3.6.1.4.1.6574.1.2.0',
                        'scale': 1.0,
                        'warning_c': 65.0,
                        'critical_c': 70.0,
                        'username_env': 'HLM_SYNOLOGY_SNMP_USER',
                        'auth_protocol': 'SHA',
                        'auth_password_env': 'HLM_SYNOLOGY_SNMP_AUTH',
                        'privacy_protocol': 'AES',
                        'privacy_password_env': 'HLM_SYNOLOGY_SNMP_PRIV',
                    }
                },
            }
        }
        device = {'name': 'synology-nas', 'host': '198.51.100.177'}
        env = {
            'HLM_SYNOLOGY_SNMP_USER': 'monitor',
            'HLM_SYNOLOGY_SNMP_AUTH': 'auth-secret',
            'HLM_SYNOLOGY_SNMP_PRIV': 'priv-secret',
        }

        result = temperature_collect.collect_device_temperature(config, device, env=env, now_iso='2026-03-22T03:00:00+00:00')

        self.assertEqual(result['capability'], 'supported')
        self.assertEqual(result['method'], 'snmpv3')
        self.assertEqual(result['heat']['state'], 'HOT')
        self.assertEqual(result['heat']['value_c'], 71.0)
        self.assertEqual(result['heat']['thresholds']['source'], 'device')

    @patch('temperature_collect.urlopen')
    def test_mac_exporter_collection_reads_json_and_sets_warm_state(self, mock_urlopen):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(
            {
                'temperature_c': 62.4,
                'sampled_at': '2026-03-22T02:58:00+00:00',
            }
        ).encode('utf-8')
        mock_urlopen.return_value = response
        config = {
            'temperature': {
                'defaults': {'warning_c': 60.0, 'critical_c': 75.0, 'stale_after_s': 300},
                'devices': {
                    'macstudio-llm': {
                        'method': 'mac_api',
                        'url': 'http://macstudio.local:9100/temperature',
                    }
                },
            }
        }
        device = {'name': 'macstudio-llm', 'host': '198.51.100.10'}

        result = temperature_collect.collect_device_temperature(config, device, env={}, now_iso='2026-03-22T03:00:00+00:00')

        self.assertEqual(result['capability'], 'supported')
        self.assertEqual(result['method'], 'mac_api')
        self.assertEqual(result['heat']['state'], 'WARM')
        self.assertEqual(result['heat']['value_c'], 62.4)
        self.assertEqual(result['heat']['sampled_at'], '2026-03-22T02:58:00+00:00')

    @patch('temperature_collect.urlopen')
    def test_stale_mac_exporter_sample_becomes_unknown(self, mock_urlopen):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(
            {
                'temperature_c': 62.4,
                'sampled_at': '2026-03-22T02:40:00+00:00',
            }
        ).encode('utf-8')
        mock_urlopen.return_value = response
        config = {
            'temperature': {
                'defaults': {'warning_c': 60.0, 'critical_c': 75.0, 'stale_after_s': 300},
                'devices': {
                    'macstudio-llm': {
                        'method': 'mac_api',
                        'url': 'http://macstudio.local:9100/temperature',
                    }
                },
            }
        }
        device = {'name': 'macstudio-llm', 'host': '198.51.100.10'}

        result = temperature_collect.collect_device_temperature(config, device, env={}, now_iso='2026-03-22T03:00:00+00:00')

        self.assertEqual(result['capability'], 'supported')
        self.assertEqual(result['heat']['state'], 'UNKNOWN')
        self.assertIsNone(result['heat']['value_c'])


if __name__ == '__main__':
    unittest.main()
