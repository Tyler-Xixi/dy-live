import io
import json
import tempfile
import unittest
import importlib.util
from zipfile import ZipFile
from pathlib import Path
from contextlib import redirect_stdout
from license_server.cli import initialize_server, export_public_key, restore_database
from license_server.config import ServerSettings
from license_server.database import Database
from license_server.app import create_app
from license_test_support import TestClock


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = ServerSettings(Path(self.temp.name), "https://license.txblog.cn", True)
    def tearDown(self): self.temp.cleanup()
    def test_missing_key_fail_closed_and_restart_persists(self):
        with self.assertRaises(ValueError): create_app(self.settings)
        initialize_server(self.settings, "admin", "test-only-password-long")
        original_key = (self.settings.secret_path / "signing.pem").read_bytes()
        with self.assertRaises(ValueError): initialize_server(self.settings, "admin", "test-only-password-long")
        self.assertEqual((self.settings.secret_path / "signing.pem").read_bytes(), original_key)
        app = create_app(self.settings, TestClock())
        card = app.state.cards.create_batch(1, 1, "").cards[0]
        claims = app.state.cards.bind(card, "device-a")
        restarted = create_app(self.settings, TestClock())
        self.assertEqual(restarted.state.cards.check(claims, "device-a").expires_at, 87400)
    def test_public_key_export_does_not_include_private_key(self):
        initialize_server(self.settings, "admin", "test-only-password-long")
        output = export_public_key(self.settings)
        self.assertEqual(output["key_id"], "primary-v1")
        self.assertEqual(len(output["public_key"]), 44)
        self.assertNotIn("PRIVATE", json.dumps(output))
    def test_backup_restore_audit_and_card_bindings(self):
        initialize_server(self.settings, "admin", "test-only-password-long")
        app = create_app(self.settings, TestClock())
        card = app.state.cards.create_batch(1, 1, "").cards[0]
        claims = app.state.cards.bind(card, "device-a")
        backup = Path(self.temp.name) / "backup.sqlite3"
        app.state.db.backup(backup)
        destination = Path(self.temp.name) / "restore" / "licenses.sqlite3"
        restore_database(backup, destination)
        restored = Database(destination)
        with restored.connect() as connection:
            self.assertEqual(connection.execute("SELECT device_id FROM cards").fetchone()[0], "device-a")
            self.assertEqual(connection.execute("SELECT count(*) FROM audit").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT count(*) FROM sessions").fetchone()[0], 0)
        with self.assertRaises(ValueError): restore_database(backup, destination)
    def test_compose_no_public_backend_or_database_ports(self):
        # Parse deployment config rather than asserting source text. YAML library is already in runtime.
        import yaml
        config = yaml.safe_load(Path("license_server/compose.yaml").read_text())
        service = config["services"]["license-api"]
        self.assertFalse(service.get("ports"))
        self.assertNotEqual(service["user"], "0")
        self.assertFalse(service.get("privileged", False))
        self.assertIn("license-net", service["networks"])
    def test_nginx_exact_license_domain_only(self):
        # Runtime nginx -t/routes are the real deployment gate; local template only supplies deployment inputs.
        path = Path("license_server/deploy/license.nginx.conf")
        self.assertTrue(path.is_file())

    def test_docker_build_excludes_runtime_secrets(self):
        dockerfile = Path("license_server/Dockerfile").read_text()
        self.assertNotIn("COPY license_server /app/license_server", dockerfile)
        ignored = Path("license_server/Dockerfile.dockerignore")
        self.assertTrue(ignored.is_file())

    def test_upload_bundle_is_source_only(self):
        spec = importlib.util.spec_from_file_location("license_bundle", "build/package_license_server.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        destination = Path(self.temp.name) / "source.zip"
        module.package(destination)
        with ZipFile(destination) as archive:
            names = archive.namelist()
            self.assertIn("license_protocol.py", names)
            self.assertIn("license_server/Dockerfile.dockerignore", names)
            self.assertIn("license_server/deploy/dy-license-renew.service", names)
            self.assertIn("license_server/deploy/dy-license-renew.timer", names)
            self.assertTrue(all(name == "license_protocol.py" or name.startswith("license_server/") for name in names))
            self.assertFalse(any("runtime/" in name or "__pycache__" in name or name.endswith(".pem") for name in names))
        with self.assertRaises(ValueError): module.package(destination)
