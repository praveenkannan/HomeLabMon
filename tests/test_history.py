import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import check_devices


class HistorySummaryTests(unittest.TestCase):
    def test_summarize_recent_health_percentages(self):
        rows = [
            {'timestamp': '2026-03-20T00:00:00+00:00', 'state': {'router': {'healthy': True}, 'nas': {'healthy': True}}},
            {'timestamp': '2026-03-20T01:00:00+00:00', 'state': {'router': {'healthy': False}, 'nas': {'healthy': True}}},
            {'timestamp': '2026-03-20T02:00:00+00:00', 'state': {'router': {'healthy': True}, 'nas': {'healthy': True}}},
        ]
        summary = check_devices.summarize_recent_health(rows)
        self.assertEqual(summary['router']['checks'], 3)
        self.assertEqual(summary['router']['healthy_checks'], 2)
        self.assertEqual(summary['router']['uptime_percent'], 67)
        self.assertEqual(summary['nas']['uptime_percent'], 100)

    def test_summarize_hourly_orders_most_recent_hour_first(self):
        rows = [
            {'timestamp': '2026-03-22T02:15:00+00:00', 'state': {'router': {'healthy': True, 'reason': '02'}}},
            {'timestamp': '2026-03-22T03:15:00+00:00', 'state': {'router': {'healthy': True, 'reason': '03'}}},
            {'timestamp': '2026-03-22T04:15:00+00:00', 'state': {'router': {'healthy': True, 'reason': '04'}}},
        ]

        original_now = check_devices.utc_now_dt
        try:
            check_devices.utc_now_dt = lambda: check_devices.datetime(2026, 3, 22, 4, 30, tzinfo=check_devices.timezone.utc)
            series = check_devices.summarize_hourly(rows, hours=3)['router']
        finally:
            check_devices.utc_now_dt = original_now

        self.assertEqual([point['hour'] for point in series], ['04:00', '03:00', '02:00'])
        self.assertEqual([point['reason'] for point in series], ['04', '03', '02'])
        self.assertEqual(
            [point['slot_start'] for point in series],
            [
                '2026-03-22T04:00:00+00:00',
                '2026-03-22T03:00:00+00:00',
                '2026-03-22T02:00:00+00:00',
            ],
        )

    def test_render_dashboard_orders_weekly_columns_current_day_first(self):
        state = {
            'router': {
                'healthy': True,
                'display_name': 'Router',
                'reason': 'ping ok',
                'last_checked': '2026-03-22T04:00:00+00:00',
            }
        }
        original_now = check_devices.utc_now_dt
        original_load = check_devices.load_recent_history
        original_host = check_devices.collect_pi_host_metrics
        original_status_html = check_devices.STATUS_HTML
        original_www_path = check_devices.WWW_PATH
        try:
            check_devices.utc_now_dt = lambda: check_devices.datetime(2026, 3, 22, 4, 30, tzinfo=check_devices.timezone.utc)
            check_devices.load_recent_history = lambda days=7: [
                {'timestamp': '2026-03-22T04:00:00+00:00', 'state': {'router': {'healthy': True, 'reason': 'today'}}},
                {'timestamp': '2026-03-21T04:00:00+00:00', 'state': {'router': {'healthy': False, 'reason': 'yesterday'}}},
            ]
            check_devices.collect_pi_host_metrics = lambda: {}
            temp_dir = Path(__file__).resolve().parent / '_tmp_history_render'
            temp_dir.mkdir(exist_ok=True)
            check_devices.WWW_PATH = temp_dir
            check_devices.STATUS_HTML = temp_dir / 'status.html'
            check_devices.render_status_page(
                state,
                [{'name': 'router', 'display_name': 'Router'}],
                {'site_title': 'Portable Monitor'},
            )
            html = check_devices.STATUS_HTML.read_text(encoding='utf-8')
        finally:
            check_devices.utc_now_dt = original_now
            check_devices.load_recent_history = original_load
            check_devices.collect_pi_host_metrics = original_host
            check_devices.STATUS_HTML = original_status_html
            check_devices.WWW_PATH = original_www_path

        self.assertIn('<th>03-22</th><th>03-21</th>', html)
        self.assertIn('<title>Portable Monitor</title>', html)
        self.assertIn('Low-footprint home infrastructure telemetry board', html)


if __name__ == '__main__':
    unittest.main()
