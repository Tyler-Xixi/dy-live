import threading
import unittest
from datetime import datetime
from unittest.mock import patch

import dy_grab_gui as app


class FlashWindowsTests(unittest.TestCase):
    def test_monitor_heartbeat_reports_measured_time_and_throttles(self):
        logs=[]
        runner=app.AutomationRunner(app.AutomationConfig(), lambda level,msg: logs.append((level,msg)), threading.Event())
        runner.monitor_started_at=100
        with patch.object(app.time, 'monotonic', return_value=110):
            runner.report_monitor_status(35, None)
            runner.report_monitor_status(40, None)
        self.assertEqual(len(logs), 1)
        self.assertIn('00:00:10', logs[0][1])
        self.assertIn('35 ms', logs[0][1])
        self.assertIn('实时监控', logs[0][1])
        self.assertIn('仅日志每5秒刷新', logs[0][1])
        with patch.object(app.time, 'monotonic', return_value=111):
            runner.report_monitor_status(80, '页面连接中断')
        self.assertIn('失败', logs[-1][1])
        self.assertIn('页面连接中断', logs[-1][1])

    def test_ranges_cross_midnight_and_one_hour_limit(self):
        self.assertEqual(app.invalid_schedule_entries('23:30-00:20,12:00-13:00'), [])
        self.assertTrue(app.invalid_schedule_entries('12:00-13:01'))
        self.assertTrue(app.invalid_schedule_entries('12:00-12:00'))
        self.assertTrue(app.invalid_schedule_entries('12:00'))

    def test_previous_day_window_is_active_after_midnight(self):
        runner = app.AutomationRunner(app.AutomationConfig(schedule_windows_raw='23:30-00:20'), lambda *_: None, threading.Event())
        now = datetime(2026, 10, 8, 0, 10, tzinfo=app.AUTOMATION_TIMEZONE)
        with patch.object(app.time, 'time', return_value=now.timestamp()):
            self.assertEqual(runner.active_window_end_local_ms(0), now.replace(minute=20).timestamp()*1000)

    def test_initial_stock_empty_waits_and_failure_logs_once(self):
        logs = []
        runner = app.AutomationRunner(app.AutomationConfig(), lambda level, msg: logs.append(msg), threading.Event())
        runner.record_stock_state('unavailable')
        runner.record_stock_state('unavailable')
        self.assertEqual(len(logs), 1)
        runner.record_stock_state('ready')
        with self.assertRaises(app.GracefulStop):
            runner.record_stock_state('unavailable')

    def test_continue_after_sold_out_deduplicates_failure(self):
        logs = []
        runner = app.AutomationRunner(app.AutomationConfig(continue_after_sold_out=True), lambda level, msg: logs.append(msg), threading.Event())
        runner.record_stock_state('ready')
        runner.record_stock_state('unavailable')
        runner.record_stock_state('unavailable')
        self.assertEqual(sum('未抢到' in msg for msg in logs), 1)


if __name__ == '__main__':
    unittest.main()
