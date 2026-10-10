import threading
import unittest
import dy_grab_gui as app
from license_protocol import LicenseStatus


class GuardTests(unittest.TestCase):
    def runner(self):
        return app.AutomationRunner(app.AutomationConfig(), lambda *_: None, threading.Event(),
            license_status=lambda: LicenseStatus(False, "online", "disabled"))
    def test_revocation_retains_browser_during_captcha(self):
        runner = self.runner()
        runner.state['manual_verification_waiting'] = True
        with self.assertRaises(app.AuthorizationExpired): runner.assert_running()
        self.assertTrue(runner.state['retain_browser'])
        self.assertFalse(runner.stop_event.is_set())
    def test_revocation_after_submit_keeps_readonly_ack(self):
        runner = self.runner()
        runner.state['order_submitted'] = True
        runner.assert_running()
        with self.assertRaises(app.AuthorizationExpired): runner.assert_purchase_authorized()
        self.assertTrue(runner.state['retain_browser'])
    def test_explicit_stop_still_closes(self):
        runner = self.runner()
        runner.stop_event.set()
        with self.assertRaises(app.GracefulStop): runner.assert_running()
        self.assertFalse(runner.state['retain_browser'])
    def test_click_denied_without_invoking_payment(self):
        runner = self.runner()
        runner.state['manual_verification_waiting'] = False
        clicked = []
        class Button:
            def click(self, **kwargs): clicked.append(True)
        with self.assertRaises(app.AuthorizationExpired): runner.safe_click(Button(), 'submit payment / create order')
        self.assertEqual(clicked, [])
