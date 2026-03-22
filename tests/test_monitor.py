import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))

import incident_engine


class TransitionTests(unittest.TestCase):
    def test_first_observation_does_not_alert(self):
        old = {}
        new = {'router': {'healthy': True, 'reason': 'ok'}}
        incidents, _ = incident_engine.derive_incidents(old, new, now_ts='2026-03-22T00:00:00+00:00')
        self.assertEqual(incidents, [])

    def test_down_transition_alerts_once(self):
        old = {'router': {'healthy': True, 'reason': 'ok'}}
        new = {'router': {'healthy': False, 'reason': 'tcp:443 failed'}}
        incidents, _ = incident_engine.derive_incidents(old, new, now_ts='2026-03-22T00:00:00+00:00')
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]['event'], 'DOWN')

    def test_recovery_transition_alerts_once(self):
        old = {'router': {'healthy': False, 'reason': 'ping failed'}}
        new = {'router': {'healthy': True, 'reason': 'ok'}}
        incidents, _ = incident_engine.derive_incidents(old, new, now_ts='2026-03-22T00:00:00+00:00')
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]['event'], 'RECOVERED')

    def test_unchanged_state_does_not_alert(self):
        old = {'router': {'healthy': True, 'reason': 'ok'}}
        new = {'router': {'healthy': True, 'reason': 'ping ok, tcp:80 ok'}}
        incidents, _ = incident_engine.derive_incidents(old, new, now_ts='2026-03-22T00:00:00+00:00')
        self.assertEqual(incidents, [])


if __name__ == '__main__':
    unittest.main()
