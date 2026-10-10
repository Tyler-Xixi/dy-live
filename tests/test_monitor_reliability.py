import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import dy_grab_gui as app


class ReliabilityTests(unittest.TestCase):
    def test_network_writer_only_uses_metadata_and_drains_on_stop(self):
        class Response:
            url='https://live.douyin.com/commerce/test'
            status=200
            def text(self):
                raise AssertionError('response bodies must not be read')
            @property
            def headers(self):
                raise AssertionError('extra protocol requests must not be made')
        class Page:
            def on(self,event,callback):
                self.callback=callback
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'network.jsonl'
            runner=app.AutomationRunner(app.AutomationConfig(network_log_path=str(path)),lambda *_:None,threading.Event())
            page=Page();runner.capture_network_evidence(page)
            page.callback(Response())
            runner.network_stop.set();runner.network_worker.join(timeout=2)
            self.assertFalse(runner.network_worker.is_alive())
            row=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(row['status'],200)
            self.assertNotIn('body',row)

    def test_network_capture_disabled_does_not_subscribe(self):
        class Page:
            def on(self,*args):
                raise AssertionError('disabled diagnostics must not subscribe')
        runner=app.AutomationRunner(app.AutomationConfig(save_diagnostics=False),lambda *_:None,threading.Event())
        runner.capture_network_evidence(Page())

    def test_configuration_restore_does_not_restore_payment_switches(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(app,'APP_DATA_DIR',Path(folder)):
            window=app.App(); window.withdraw()
            window.vars['product_name'].set('测试商品')
            window.vars['schedule_windows'].set('23:30-00:20')
            window.vars['dry_run'].set(False)
            window.vars['auto_pay'].set(True)
            window.save_preferences()
            window.destroy()
            restored=app.App(); restored.withdraw()
            try:
                self.assertEqual(restored.vars['product_name'].get(),'测试商品')
                self.assertEqual(restored.vars['schedule_windows'].get(),'23:30-00:20')
                self.assertTrue(restored.vars['dry_run'].get())
                self.assertFalse(restored.vars['auto_pay'].get())
            finally:
                restored.destroy()

    def test_corrupt_configuration_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(app,'APP_DATA_DIR',Path(folder)):
            (Path(folder)/'preferences.json').write_text('{broken',encoding='utf-8')
            window=app.App();window.withdraw()
            try:
                self.assertEqual(window.config_from_form().target_price,20)
            finally:
                window.destroy()
