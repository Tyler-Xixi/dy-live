import unittest
from dataclasses import replace
from fastapi.testclient import TestClient
from license_server.app import create_app
from license_protocol import verify_envelope, verify_document
from license_test_support import server_fixture


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp, settings, self.clock, self.keys = server_fixture()
        self.app = create_app(settings, self.clock)
        self.client = TestClient(self.app, base_url=settings.origin)
        self.app.state.auth.initialize("admin", "test-only-password-long")
        self.settings = settings

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def login(self):
        response = self.client.get("/admin/login")
        token = response.headers["x-csrf-token"]
        response = self.client.post("/admin/login", json={"username": "admin", "password": "test-only-password-long"},
                                    headers={"Origin": self.settings.origin, "X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)
        return response.json()["csrf"]

    def activate(self, card, device="device-a"):
        return self.client.post("/api/v1/activate", json={"card": card, "device_id": device,
                               "client_version": "test", "nonce": "a" * 32})

    def test_api_two_devices_and_nonce_signed(self):
        card = self.app.state.cards.create_batch(1, 1, "").cards[0]
        response = self.activate(card)
        payload = verify_envelope(response.json(), self.keys, "a" * 32)
        self.assertEqual(payload["result"], "allow")
        reply = verify_envelope(self.activate(card, "device-b").json(), self.keys, "a" * 32)
        self.assertEqual(reply["result"], "binding_mismatch")
        self.assertEqual(self.activate("unknown-card").status_code, 400)

    def test_expired_authentic_credential_can_refresh_after_renewal(self):
        card = self.app.state.cards.create_batch(1, 1, "").cards[0]
        payload = verify_envelope(self.activate(card).json(), self.keys, "a" * 32)
        credential = payload["credential"]
        claims = verify_document(credential, self.keys)
        self.clock.now = 90000
        self.app.state.cards.update(claims["license_id"], "renew", {"days": 1}, "admin")
        response = self.client.post("/api/v1/check", json={"credential": credential, "device_id": "device-a",
                                   "client_version": "test", "nonce": "b" * 32})
        renewed = verify_envelope(response.json(), self.keys, "b" * 32)
        self.assertEqual(renewed["result"], "allow")
        self.assertEqual(renewed["credential"]["payload"]["expires_at"], 176400)

    def test_unauthenticated_and_csrf_write_denied(self):
        self.assertEqual(self.client.post("/admin/cards/generate", json={"count": 1}).status_code, 403)
        csrf = self.login()
        body = {"count": 1, "duration_days": 30, "note": "demo"}
        self.assertEqual(self.client.post("/admin/cards/generate", json=body).status_code, 403)
        self.assertEqual(self.client.post("/admin/cards/generate", json=body,
                        headers={"Origin": "https://evil.invalid", "X-CSRF-Token": csrf}).status_code, 403)
        response = self.client.post("/admin/cards/generate", json=body,
                        headers={"Origin": self.settings.origin, "X-CSRF-Token": csrf})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        cookie = self.client.cookies.get("dyl_admin")
        self.assertTrue(cookie)

    def test_login_rate_limits_do_not_trust_forged_forwarded_ip(self):
        response = self.client.get("/admin/login")
        token = response.headers["x-csrf-token"]
        for index in range(5):
            self.client.post("/admin/login", json={"username": "admin", "password": "wrong"},
                headers={"Origin": self.settings.origin, "X-CSRF-Token": token, "X-Forwarded-For": f"10.0.0.{index}"})
        response = self.client.post("/admin/login", json={"username": "admin", "password": "test-only-password-long"},
            headers={"Origin": self.settings.origin, "X-CSRF-Token": token, "X-Forwarded-For": "10.0.0.99"})
        self.assertEqual(response.status_code, 429)

    def test_untrusted_host_oversize_and_hidden_docs(self):
        self.assertEqual(self.client.get("/health", headers={"Host": "evil.invalid"}).status_code, 400)
        self.assertEqual(self.client.get("/docs").status_code, 404)
        response = self.client.post("/api/v1/activate", content=b"x" * 65537)
        self.assertEqual(response.status_code, 413)

    def test_proxy_limited_mode_does_not_group_all_customers(self):
        proxy_settings = replace(self.settings, proxy_rate_limited=True)
        with TestClient(create_app(proxy_settings, self.clock), base_url=self.settings.origin) as client:
            for _ in range(125):
                response = client.post("/api/v1/activate", json={"card":"unknown-card", "device_id":"test",
                    "client_version":"test", "nonce":"n"*32})
                self.assertEqual(response.status_code, 400)
        for _ in range(120): self.activate("unknown-card")
        self.assertEqual(self.activate("unknown-card").status_code, 429)
