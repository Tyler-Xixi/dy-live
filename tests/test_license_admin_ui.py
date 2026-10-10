import unittest
from fastapi.testclient import TestClient
from license_server.app import create_app
from license_test_support import server_fixture


class AdminUITests(unittest.TestCase):
    def setUp(self):
        self.temp, self.settings, self.clock, self.keys = server_fixture()
        self.app = create_app(self.settings, self.clock)
        self.app.state.auth.initialize("admin", "test-only-password-long")
        self.client = TestClient(self.app, base_url=self.settings.origin)
        page = self.client.get("/admin/login")
        response = self.client.post("/admin/login", json={"username": "admin", "password": "test-only-password-long"},
            headers={"Origin": self.settings.origin, "X-CSRF-Token": page.headers["x-csrf-token"]})
        self.headers = {"Origin": self.settings.origin, "X-CSRF-Token": response.json()["csrf"]}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def html(self, path): return self.client.get(path, headers={"Accept": "text/html"})

    def test_anonymous_browser_navigation_goes_to_login(self):
        with TestClient(self.app, base_url=self.settings.origin) as anonymous:
            page = anonymous.get("/admin/cards", headers={"Accept": "text/html"}, follow_redirects=False)
            self.assertEqual(page.status_code, 303)
            self.assertEqual(page.headers["location"], "/admin/login")
            self.assertEqual(anonymous.get("/admin/cards").status_code, 403)

    def test_admin_generate_edit_revoke_unbind_archive_flow(self):
        page = self.html("/admin/cards")
        self.assertIn("卡密管理", page.text)
        self.assertIn("生成卡密", page.text)
        response = self.client.post("/admin/cards/generate", json={"count": 1, "duration_days": 30, "note": "演示"}, headers=self.headers)
        card_id = self.client.get("/admin/cards").json()["items"][0]["id"]
        self.assertIn("演示", self.html("/admin/cards/" + card_id).text)
        for action in ("disable", "restore", "unbind", "archive"):
            response = self.client.post(f"/admin/cards/{card_id}/{action}", json={}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/admin/cards").json()["total"], 0)
        self.assertEqual(self.client.get("/admin/cards?status=archived").json()["total"], 1)
        self.assertIn("archive", self.html("/admin/audit").text)

    def test_plaintext_only_once_and_batch_export(self):
        generated = self.client.post("/admin/cards/generate", json={"count": 2, "duration_days": None, "note": ""}, headers=self.headers).json()
        self.assertEqual(len(generated["cards"]), 2)
        for card in generated["cards"]:
            self.assertNotIn(card, self.html("/admin/cards").text)
            self.assertNotIn(card, self.client.get("/admin/cards").text)
        script = self.client.get("/static/admin.js")
        self.assertEqual(script.status_code, 200)
        page = self.html("/admin/cards")
        self.assertIn('id="download-cards"', page.text)
        self.assertEqual(page.headers["cache-control"], "no-store")

    def test_note_html_escaped(self):
        self.app.state.cards.create_batch(1, 1, '<script>alert(1)</script>')
        response = self.html("/admin/cards")
        self.assertNotIn('<script>alert(1)</script>', response.text)
        self.assertIn('&lt;script&gt;', response.text)

    def test_month_year_custom_days_and_expiry_timezone(self):
        page = self.html("/admin/cards")
        self.assertIn('value="30"', page.text)
        self.assertIn('value="365"', page.text)
        self.assertIn("香港时间", page.text)
        for days in (1, 30, 365, 47):
            result = self.client.post("/admin/cards/generate", json={"count": 1, "duration_days": days, "note": ""}, headers=self.headers)
            self.assertEqual(result.status_code, 200)

    def test_expiry_filter_controls_apply_hong_kong_time(self):
        page = self.html("/admin/cards")
        self.assertIn('name="expiry_from"', page.text)
        self.assertIn('name="expiry_to"', page.text)
        short = self.app.state.cards.create_batch(1, 1, "short").cards[0]
        long = self.app.state.cards.create_batch(1, 30, "long").cards[0]
        self.app.state.cards.bind(short, "short-device")
        self.app.state.cards.bind(long, "long-device")
        filtered = self.html("/admin/cards?expiry_from=1970-01-03T08%3A00&expiry_to=1970-02-01T08%3A00")
        self.assertNotIn('class="note">short', filtered.text)
        self.assertIn('class="note">long', filtered.text)
        self.assertEqual(self.html("/admin/cards?expiry_from=not-a-date").status_code, 400)
