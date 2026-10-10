import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import dy_grab_gui as app
from license_protocol import LicenseStatus


class Offline:
    def __init__(self, directory, valid=False):
        self.state_path = Path(directory) / "license.json"
        self.valid = valid
    def verify_saved(self): return self.valid
    def activate(self, card):
        if card != "test-offline": raise ValueError("invalid test card")
        self.valid = True


class Online:
    def __init__(self, directory):
        self.path = Path(directory) / "online_license.json"
        self.closed = False
        self.entered, self.release = threading.Event(), threading.Event()
    def verify_saved(self): return LicenseStatus(False, "online", "activation_required")
    def snapshot(self): return self.verify_saved()
    def activate(self, card):
        self.entered.set(); self.release.wait(3)
        return LicenseStatus(False, "online", "network_unconfirmed")
    def close(self): self.closed = True


class LicenseGUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.online = Online(self.temp.name)
        self.dialog = None
    def tearDown(self):
        self.online.release.set()
        if self.dialog:
            try: self.dialog.destroy()
            except app.tk.TclError: pass
        self.temp.cleanup()
    def dialog_for(self, valid=False):
        self.dialog = app.ActivationDialog(Offline(self.temp.name, valid), self.online)
        self.dialog.withdraw()
        return self.dialog
    def test_legacy_saved_license_unchanged(self):
        path = Path(self.temp.name) / "license.json"
        path.write_text('legacy unchanged', encoding='utf-8')
        dialog = self.dialog_for(True)
        dialog.verify_existing()
        self.assertTrue(dialog.authorized)
        self.assertEqual(dialog.authorized_mode, "offline")
        self.assertEqual(path.read_text(), 'legacy unchanged')
    def test_failed_online_never_auto_falls_back(self):
        dialog = self.dialog_for(True)
        dialog.mode_var.set("online")
        dialog.card_var.set("DYL-test")
        self.online.release.set()
        dialog.activate()
        deadline = time.monotonic()+2
        while time.monotonic()<deadline:
            dialog.update(); time.sleep(.01)
        self.assertFalse(dialog.authorized)
        self.assertEqual(dialog.mode_var.get(), "online")
    def test_gui_remains_responsive_while_transport_blocks(self):
        dialog = self.dialog_for()
        dialog.mode_var.set("online")
        dialog.card_var.set("DYL-test")
        started = time.monotonic()
        dialog.activate()
        self.assertLess(time.monotonic()-started, .1)
        called = []
        dialog.after(0, lambda: called.append(True))
        dialog.update()
        self.assertEqual(called, [True])
        self.assertFalse(dialog.authorized)

    def test_saved_expired_credential_refreshes_without_plaintext_card(self):
        dialog = self.dialog_for()
        self.online.credential = {"test": "saved"}
        self.online.verify_saved = lambda: LicenseStatus(False, "online", "expired")
        refreshed = threading.Event()
        def refresh():
            refreshed.set()
            return LicenseStatus(True, "online", "valid")
        self.online.refresh = refresh
        dialog.verify_existing()
        self.assertTrue(refreshed.wait(1))
        deadline = time.monotonic()+2
        while not dialog.authorized and time.monotonic()<deadline:
            dialog.update()
            time.sleep(.01)
        self.assertTrue(dialog.authorized)
