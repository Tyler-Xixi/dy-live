"""Real SQLite transactions must enforce first binding and preserve expiry."""
import dataclasses
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from license_server.database import Database
from license_server.cards import CardService, LicenseDenied


class FakeClock:
    def __init__(self): self.now = 1000
    def wall_time(self): return self.now
    def monotonic_time(self): return float(self.now)


class CardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.sqlite3")
        self.db.initialize()
        self.clock = FakeClock()
        self.cards = CardService(self.db, b"test-only-digest-secret-32-bytes!!", self.clock)

    def tearDown(self): self.temp.cleanup()

    def create(self, days=1):
        batch = self.cards.create_batch(1, days, "测试备注")
        return batch.cards[0]

    def test_concurrent_first_bind_only_one_device(self):
        card = self.create()
        def bind(device):
            try: return self.cards.bind(card, device)
            except LicenseDenied: return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(bind, ["device-a", "device-b"]))
        self.assertEqual(sum(x is not None for x in results), 1)

    def test_same_device_idempotent(self):
        card = self.create()
        first = self.cards.bind(card, "device-a")
        self.clock.now = 2000
        second = self.cards.bind(card, "device-a")
        self.assertEqual(first.expires_at, 87400)
        self.assertEqual(second.expires_at, 87400)

    def test_expiry_renew_and_unbind_keep_term(self):
        card = self.create()
        first = self.cards.bind(card, "device-a")
        self.clock.now = 90000
        with self.assertRaisesRegex(LicenseDenied, "expired"): self.cards.check(first, "device-a")
        updated = self.cards.update(first.license_id, "renew", {"days": 2}, "admin")
        self.assertEqual(updated.expires_at, 262800)
        unbound = self.cards.update(first.license_id, "unbind", {}, "admin")
        self.assertEqual(unbound.expires_at, 262800)
        with self.assertRaisesRegex(LicenseDenied, "binding_mismatch"): self.cards.check(first, "device-a")
        rebound = self.cards.bind(card, "device-b")
        self.assertEqual(rebound.expires_at, 262800)
        self.assertEqual(rebound.binding_version, 2)

    def test_disable_archive_and_restore(self):
        card = self.create(None)
        claims = self.cards.bind(card, "device-a")
        self.cards.update(claims.license_id, "disable", {}, "admin")
        with self.assertRaisesRegex(LicenseDenied, "disabled"): self.cards.check(claims, "device-a")
        self.cards.update(claims.license_id, "restore", {}, "admin")
        self.assertIsNone(self.cards.check(claims, "device-a").expires_at)
        self.cards.update(claims.license_id, "archive", {}, "admin")
        with self.assertRaisesRegex(LicenseDenied, "archived"): self.cards.bind(card, "device-a")
        with self.assertRaises(ValueError): self.cards.update(claims.license_id, "restore", {}, "admin")

    def test_transaction_rollback_and_restart_backup(self):
        # Force a collision in the second insert: the first insert must be rolled back too.
        with patch("license_server.cards.secrets.token_hex", return_value="a" * 40):
            with self.assertRaises(sqlite3.IntegrityError): self.cards.create_batch(2, 1, "collision")
        self.assertEqual(self.cards.list_cards({}, 1, 20).total, 0)
        card = self.create()
        bound = self.cards.bind(card, "device-a")
        backup = Path(self.temp.name) / "backup.sqlite3"
        self.db.backup(backup)
        restarted = CardService(Database(backup), b"test-only-digest-secret-32-bytes!!", self.clock)
        self.assertEqual(restarted.check(bound, "device-a").expires_at, 87400)
        self.assertGreater(len(restarted.audit(1, 20)), 0)

    def test_batch_database_contains_no_plaintext(self):
        batch = self.cards.create_batch(3, 30, "ordinary")
        with self.db.connect() as connection:
            dump = "\n".join(connection.iterdump())
        for card in batch.cards: self.assertNotIn(card, dump)
        page = self.cards.list_cards({"q": "ordinary"}, 1, 20)
        self.assertEqual(page.total, 3)
        for card in batch.cards: self.assertNotIn(card, str(page))
        self.assertEqual(self.cards.list_cards({"q": "' OR 1=1 --"}, 1, 20).total, 0)

    def test_term_edits_and_invalid_input(self):
        for count, days in ((0, 1), (1001, 1), (1, 0), (1, -1), (1, True)):
            with self.assertRaises(ValueError): self.cards.create_batch(count, days, "")
        card = self.create(None)
        claims = self.cards.bind(card, "device-a")
        view = self.cards.update(claims.license_id, "edit", {"expires_at": 8000, "note": "new"}, "admin")
        self.assertEqual(view.expires_at, 8000)
        view = self.cards.update(claims.license_id, "edit", {"expires_at": None}, "admin")
        self.assertIsNone(view.expires_at)
        with self.assertRaises(ValueError): self.cards.update(claims.license_id, "edit", {"device_id": "attacker"}, "admin")
