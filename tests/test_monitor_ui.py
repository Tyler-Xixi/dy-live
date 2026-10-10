import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import dy_grab_gui as app


class MonitorUITests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.settings = patch.object(app, 'APP_DATA_DIR', Path(self.folder.name))
        self.settings.start()

    def tearDown(self):
        self.settings.stop()
        self.folder.cleanup()

    def test_time_picker_saves_cross_midnight_and_rejects_long_range(self):
        window=app.App()
        window.withdraw()
        try:
            window.open_time_picker('schedule_windows',1)
            popup=window.time_picker
            body=popup.winfo_children()[0]
            combos=[child for child in body.winfo_children() if isinstance(child,app.ttk.Combobox)]
            for control,value in zip(combos,('23','30','00','01','00','00')):
                control.set(value)
            actions=[child for child in body.winfo_children() if isinstance(child,app.ttk.Frame)][0]
            apply_button=next(child for child in actions.winfo_children() if child.cget('text')=='确定')
            apply_button.invoke()
            self.assertEqual(window.vars['schedule_windows'].get(),'')
            self.assertTrue(popup.winfo_exists())
            for control,value in zip(combos,('23','30','00','00','20','00')):
                control.set(value)
            apply_button.invoke()
            self.assertEqual(window.vars['schedule_windows'].get(),'23:30:00-00:20:00')
            self.assertEqual(window.config_from_form().schedule_windows()[0].duration_ms,3000000)
        finally:
            window.destroy()

    def test_detail_lock_switch_round_trips_and_does_not_change_batch_mode(self):
        errors = []
        with patch.object(app.App, 'report_callback_exception', lambda self, *args: errors.append(args)):
            window = app.App()
        window.withdraw()
        try:
            window.vars['detail_lock_mode'].set(True)
            window.vars['detail_refresh_ms'].set('100')
            window.update()
            config = window.config_from_form()
            self.assertTrue(config.detail_lock_mode)
            self.assertEqual(config.detail_refresh_ms, 500)
            self.assertIn('详情页锁单', window.mode_caption.get())
            self.assertEqual(errors, [])
            window.task_tabs.select(1)
            self.assertFalse(window.config_from_form().detail_lock_mode)
        finally:
            window.destroy()

    def test_advanced_switches_do_not_overlap_new_refresh_field_or_profile(self):
        window = app.App()
        window.withdraw()
        try:
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            profile = next(widget for widget in descendants(window)
                           if isinstance(widget, app.ttk.Entry) and
                           str(widget.cget('textvariable')) == str(window.vars['profile_dir']))
            siblings = profile.master.winfo_children()
            switches = next(widget for widget in siblings if isinstance(widget, app.ttk.LabelFrame))
            last_field_row = max(int(widget.grid_info()['row']) for widget in siblings
                                 if isinstance(widget, app.ttk.Entry))
            self.assertGreater(int(switches.grid_info()['row']), last_field_row+1)
        finally:
            window.destroy()

    def test_heartbeat_replaces_one_line_and_preserves_events(self):
        window=app.App()
        window.withdraw()
        try:
            config=window.config_from_form()
            self.assertEqual(config.product_id, '1')
            self.assertEqual(config.target_price, 20)
            window.log('monitor', '持续监控中 | 监控时间 00:00:01 | 状态 正常 | 35 ms')
            window.drain_logs()
            window.log('monitor', '持续监控中 | 监控时间 00:00:06 | 状态 正常 | 40 ms')
            window.drain_logs()
            text=window.log_text.get('1.0','end')
            self.assertEqual(text.count('持续监控中'),1)
            self.assertNotIn('00:00:01',text)
            window.log('warn','本轮未抢到')
            window.drain_logs()
            window.log('monitor','持续监控中 | 状态 失败 | 页面断开')
            window.drain_logs()
            self.assertIn('本轮未抢到',window.log_text.get('1.0','end'))
            window.clear_logs()
            window.log('info','新的事件')
            window.log('monitor','持续监控中 | 状态 正常')
            window.drain_logs()
            self.assertIn('新的事件',window.log_text.get('1.0','end'))
        finally:
            window.destroy()


if __name__=='__main__':
    unittest.main()
