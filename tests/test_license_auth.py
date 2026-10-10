import unittest
from license_server.database import Database
from license_server.auth import AuthService, AuthDenied
from license_test_support import server_fixture


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp, settings, self.clock, _ = server_fixture()
        self.db = Database(settings.database_path)
        self.db.initialize()
        self.auth = AuthService(self.db, self.clock)
        self.auth.initialize("admin", "test-only-password-long")

    def tearDown(self): self.temp.cleanup()

    def test_session_idle_absolute_and_reset(self):
        session = self.auth.login("admin", "test-only-password-long", "source")
        self.clock.now = 2799
        self.assertEqual(self.auth.require(session.token).csrf, session.csrf)
        self.clock.now = 4600
        with self.assertRaises(AuthDenied): self.auth.require(session.token)
        self.clock.now = 5000
        session = self.auth.login("admin", "test-only-password-long", "source")
        for now in range(6000, 33800, 1000):
            self.clock.now = now
            self.auth.require(session.token)
        self.clock.now = 33800
        with self.assertRaises(AuthDenied): self.auth.require(session.token)
        session = self.auth.login("admin", "test-only-password-long", "source")
        self.auth.reset_password("different-test-password-long")
        with self.assertRaises(AuthDenied): self.auth.require(session.token)

    def test_logout_revokes_and_database_contains_no_plaintext(self):
        session = self.auth.login("admin", "test-only-password-long", "source")
        with self.db.connect() as connection: dump = "\n".join(connection.iterdump())
        self.assertNotIn("test-only-password-long", dump)
        self.assertNotIn(session.token, dump)
        self.auth.logout(session.token)
        with self.assertRaises(AuthDenied): self.auth.require(session.token)
        with self.assertRaises(ValueError): self.auth.initialize("other", "other-test-password-long")

    def test_login_rate_limits_expire(self):
        for _ in range(5):
            with self.assertRaises(AuthDenied): self.auth.login("admin", "wrong", "source")
        with self.assertRaisesRegex(AuthDenied, "rate_limited"):
            self.auth.login("admin", "test-only-password-long", "source")
        self.clock.now += 901
        self.assertTrue(self.auth.login("admin", "test-only-password-long", "source").token)
