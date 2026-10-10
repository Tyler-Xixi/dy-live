import dataclasses
import tempfile
import threading
import unittest
import json
import time
from unittest.mock import patch
from pathlib import Path
from fastapi.testclient import TestClient
from license_server.app import create_app
from online_license import OnlineLicenseClient, LicenseTransport
from license_test_support import server_fixture


class LocalTransport:
    def __init__(self, client):
        self.client, self.outage, self.wrong_nonce = client, False, False
    def post(self, path, body):
        if self.outage: raise OSError("simulated network outage")
        data = {**body, "nonce": "wrong" * 8} if self.wrong_nonce else body
        response = self.client.post(path, json=data)
        if response.status_code != 200: raise OSError("request rejected")
        return response.json()


class OnlineTests(unittest.TestCase):
    def setUp(self):
        self.temp, settings, self.clock, self.keys = server_fixture()
        self.app = create_app(settings, self.clock)
        self.http = TestClient(self.app, base_url=settings.origin)
        self.local = tempfile.TemporaryDirectory()
        self.transport = LocalTransport(self.http)
        self.client = OnlineLicenseClient(Path(self.local.name), "device-a", self.keys, self.transport, self.clock)
        self.card = self.app.state.cards.create_batch(1, 1, "").cards[0]

    def tearDown(self):
        self.client.close()
        self.http.close()
        self.local.cleanup()
        self.temp.cleanup()

    def test_offline_cache_expiry_and_first_activation(self):
        self.transport.outage = True
        self.assertFalse(self.client.activate(self.card).allowed)
        self.transport.outage = False
        self.assertTrue(self.client.activate(self.card).allowed)
        self.transport.outage = True
        self.clock.now = 80000
        self.assertTrue(self.client.snapshot().allowed)
        restarted = OnlineLicenseClient(Path(self.local.name), "device-a", self.keys, self.transport, self.clock)
        self.assertTrue(restarted.verify_saved().allowed)
        self.clock.now = 87400
        self.assertFalse(restarted.snapshot().allowed)

    def test_valid_denial_sticky_on_network_failure(self):
        self.client.activate(self.card)
        card_id = self.client.claims.license_id
        self.app.state.cards.update(card_id, "disable", {}, "admin")
        self.assertFalse(self.client.refresh().allowed)
        self.transport.outage = True
        self.assertFalse(self.client.refresh().allowed)
        restarted = OnlineLicenseClient(Path(self.local.name), "device-a", self.keys, self.transport, self.clock)
        self.assertFalse(restarted.verify_saved().allowed)
        self.transport.outage = False
        self.app.state.cards.update(card_id, "restore", {}, "admin")
        self.assertTrue(restarted.refresh().allowed)

    def test_old_success_cannot_override_new_denial(self):
        self.client.activate(self.card)
        entered, release = threading.Event(), threading.Event()
        original = self.transport.post
        def delayed(path, body):
            response = original(path, body)
            if not entered.is_set():
                entered.set()
                release.wait(5)
            return response
        self.transport.post = delayed
        worker = threading.Thread(target=self.client.refresh)
        worker.start()
        self.assertTrue(entered.wait(5))
        self.app.state.cards.update(self.client.claims.license_id, "disable", {}, "admin")
        self.assertFalse(self.client.refresh().allowed)
        release.set()
        worker.join(5)
        self.assertFalse(self.client.snapshot().allowed)

    def test_nonce_wrong_signature_and_device(self):
        self.transport.wrong_nonce = True
        self.assertFalse(self.client.activate(self.card).allowed)
        self.transport.wrong_nonce = False
        self.client.activate(self.card)
        other = OnlineLicenseClient(Path(self.local.name), "device-b", self.keys, self.transport, self.clock)
        self.assertFalse(other.verify_saved().allowed)
        forged = OnlineLicenseClient(Path(self.local.name), "device-a", {}, self.transport, self.clock)
        self.assertFalse(forged.verify_saved().allowed)

    def test_clock_rollback_requires_confirmation(self):
        self.client.activate(self.card)
        self.clock.now = 900
        self.assertFalse(self.client.snapshot().allowed)
        self.assertEqual(self.client.snapshot().reason, "clock_confirmation_required")
        self.assertTrue(self.client.refresh().allowed)

    def test_expired_cache_renewal_refresh(self):
        self.client.activate(self.card)
        card_id = self.client.claims.license_id
        self.clock.now = 90000
        self.assertFalse(self.client.snapshot().allowed)
        self.app.state.cards.update(card_id, "renew", {"days": 1}, "admin")
        self.assertTrue(self.client.refresh().allowed)
        self.assertNotIn(self.card, (Path(self.local.name) / "online_license.json").read_text())

    def test_transport_rejects_http_nonlocal_and_cross_origin(self):
        with self.assertRaises(ValueError): LicenseTransport("http://license.txblog.cn")
        with self.assertRaises(ValueError): LicenseTransport("https://license.txblog.cn/evil")

    def test_cache_parent_creation_failure_denies_activation(self):
        blocked = Path(self.local.name) / "not-a-directory"
        blocked.write_text("test fixture")
        self.client.path = blocked / "online_license.json"
        status = self.client.activate(self.card)
        self.assertFalse(status.allowed)
        self.assertEqual(status.reason, "storage_error")

    def test_slow_save_does_not_hold_snapshot_lock(self):
        self.client.activate(self.card)
        entered, release, read = threading.Event(), threading.Event(), threading.Event()
        original = self.client._save
        def slow_save():
            entered.set()
            release.wait(3)
            original()
        with patch.object(self.client, "_save", slow_save):
            worker = threading.Thread(target=self.client.refresh)
            worker.start()
            self.assertTrue(entered.wait(2))
            reader = threading.Thread(target=lambda: (self.client.snapshot(), read.set()))
            reader.start()
            try: self.assertTrue(read.wait(.2), "snapshot waited for persistence")
            finally:
                release.set()
                worker.join(4)
                reader.join(4)

    def test_offline_observed_expiry_persists_across_restart(self):
        self.client.activate(self.card)
        self.transport.outage = True
        self.clock.now = 90000
        self.assertFalse(self.client.snapshot().allowed)
        deadline = time.monotonic()+2
        saved = False
        while time.monotonic() < deadline:
            saved = json.loads(self.client.path.read_text())["watermark"] >= 90000
            if saved: break
            time.sleep(.02)
        self.assertTrue(saved, "offline time watermark never persisted")
        self.clock.now = 80000
        restarted = OnlineLicenseClient(Path(self.local.name), "device-a", self.keys, self.transport, self.clock)
        try: self.assertFalse(restarted.verify_saved().allowed)
        finally: restarted.close()
