import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import send_alert


class AlertHelpersTests(unittest.TestCase):
    @patch('send_alert.urlopen')
    def test_telegram_returns_false_without_config(self, mock_urlopen):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(send_alert.send_telegram('router down'))
            mock_urlopen.assert_not_called()

    def test_dashboard_url_defaults_to_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(send_alert.dashboard_url(), '')

    def test_dashboard_url_prefers_homelabmon_namespace(self):
        with patch.dict(os.environ, {'HOMELABMON_DASHBOARD_URL': 'https://example.test/status.html'}, clear=True):
            self.assertEqual(send_alert.dashboard_url(), 'https://example.test/status.html')


if __name__ == '__main__':
    unittest.main()
