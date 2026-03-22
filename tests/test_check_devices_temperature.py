import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))

import check_devices


class TemperatureStateTests(unittest.TestCase):
    def test_attach_temperature_inventory_adds_shortcuts(self):
        state = {
            'router': {'healthy': True, 'reason': 'ok'},
        }
        inventory = {
            'devices': {
                'router': {
                    'capability': 'supported',
                    'heat': {
                        'state': 'WARM',
                        'value_c': 61.2,
                        'sampled_at': '2026-03-22T01:00:00+00:00',
                        'thresholds': {'warning_c': 60.0, 'critical_c': 75.0, 'source': 'global'},
                    },
                }
            }
        }

        merged = check_devices.attach_temperature_inventory(state, inventory)

        self.assertEqual(merged['router']['heat_state'], 'WARM')
        self.assertEqual(merged['router']['heat_value_c'], 61.2)
        self.assertEqual(merged['router']['temp_capability'], 'supported')
        self.assertIn('temperature', merged['router'])

    def test_derive_temperature_incidents_opens_and_recovers_hot_state(self):
        old_state = {
            'router': {
                'temperature': {'heat': {'state': 'NORMAL'}},
            }
        }
        new_state = {
            'router': {
                'display_name': 'Router',
                'temperature': {
                    'capability': 'supported',
                    'heat': {
                        'state': 'HOT',
                        'value_c': 78.1,
                        'sampled_at': '2026-03-22T01:02:00+00:00',
                    },
                },
            }
        }

        incidents = check_devices.derive_temperature_incidents(
            old_state,
            new_state,
            now_ts='2026-03-22T01:02:00+00:00',
        )

        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]['event'], 'DOWN')
        self.assertEqual(incidents[0]['check_key'], 'temperature:system')
        self.assertEqual(incidents[0]['reason_code'], 'threshold_breach')

        recovered = check_devices.derive_temperature_incidents(
            new_state,
            {
                'router': {
                    'temperature': {
                        'capability': 'supported',
                        'heat': {
                            'state': 'NORMAL',
                            'value_c': 66.0,
                            'sampled_at': '2026-03-22T01:12:00+00:00',
                        },
                    },
                }
            },
            now_ts='2026-03-22T01:12:00+00:00',
        )

        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]['event'], 'RECOVERED')

    def test_unknown_temperature_does_not_open_or_resolve(self):
        old_state = {
            'router': {
                'temperature': {'heat': {'state': 'HOT'}},
            }
        }
        new_state = {
            'router': {
                'temperature': {
                    'capability': 'unavailable',
                    'heat': {'state': 'UNKNOWN', 'value_c': None, 'sampled_at': None},
                },
            }
        }

        incidents = check_devices.derive_temperature_incidents(
            old_state,
            new_state,
            now_ts='2026-03-22T01:20:00+00:00',
        )

        self.assertEqual(incidents, [])


if __name__ == '__main__':
    unittest.main()
