"""Deployment transport never sends a password to an unverified SSH host."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class RemoteOpsTests(unittest.TestCase):
    def load(self):
        spec = importlib.util.spec_from_file_location("remote_ops", "build/remote_license_ops.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_password_file_decoding_preserves_spaces_and_rejects_multiline(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.txt"
            for encoding in ("utf-8-sig", "utf-16", "gb18030"):
                path.write_text(" test-password \r\n", encoding=encoding)
                self.assertEqual(module.read_password(path), " test-password ")
            path.write_text("one\ntwo", encoding="utf-8")
            with self.assertRaises(ValueError): module.read_password(path)

    def test_host_mismatch_never_reads_or_sends_password(self):
        module = self.load()
        class Key:
            def asbytes(self): return b"synthetic-untrusted-host"
        class Options: pass
        class Transport:
            def __init__(self, sock): self.auth_called = False
            def get_security_options(self): return Options()
            def start_client(self, **kwargs): pass
            def get_remote_server_key(self): return Key()
            def auth_password(self, *args): self.auth_called = True
            def close(self): pass
        with patch.object(module.socket, "create_connection", return_value=object()), patch.object(module.paramiko, "Transport", Transport), patch.object(module, "read_password") as reader:
            with self.assertRaises(ValueError): module.connect()
            reader.assert_not_called()
