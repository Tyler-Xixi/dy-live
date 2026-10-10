"""Catch tampered, cross-device, expired and ambiguous signed license input."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from license_protocol import canonical_bytes, strict_json_loads, validate_claims, verify_document, verify_envelope
from license_server.signing import Signer
from license_server.config import ServerSettings


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.signer = Signer(self.key, "test-key")
        self.keys = {"test-key": self.key.public_key().public_bytes_raw()}
        self.payload = dict(version=1, product="DYLiveAssistant", license_id="card-a", device_id="device-a",
                            binding_version=1, issued_at=1000, duration_type="limited", expires_at=2000,
                            key_id="test-key")

    def test_signature_device_expiry_and_nonce(self):
        doc = self.signer.sign(self.payload)
        self.assertEqual(verify_document(doc, self.keys), self.payload)
        self.assertEqual(validate_claims(self.payload, "device-a", 1000).expires_at, 2000)
        for device, now in (("device-b", 1000), ("device-a", 2000)):
            with self.assertRaises(ValueError): validate_claims(self.payload, device, now)
        reply = self.signer.sign(dict(version=1, product="DYLiveAssistant", key_id="test-key", nonce="n" * 32,
                                     server_time=1000, result="allow", credential=doc))
        self.assertEqual(verify_envelope(reply, self.keys, "n" * 32)["result"], "allow")
        with self.assertRaises(ValueError): verify_envelope(reply, self.keys, "m" * 32)

    def test_tampered_signature_and_untrusted_key_rejected(self):
        document = self.signer.sign(self.payload)
        document["payload"]["device_id"] = "device-b"
        with self.assertRaises(ValueError): verify_document(document, self.keys)
        with self.assertRaises(ValueError): verify_document(self.signer.sign(self.payload), {})
        document = self.signer.sign(self.payload)
        document["signature"] = "not-a-signature"
        with self.assertRaises(ValueError): verify_document(document, self.keys)

    def test_invalid_shapes_and_duplicate_keys(self):
        self.assertEqual(canonical_bytes({"b": 2, "a": 1}), b'{"a":1,"b":2}')
        for source in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":1.2}'):
            with self.assertRaises(ValueError): strict_json_loads(source)
        for changes in ({"version": True}, {"expires_at": 0}, {"duration_type": "permanent"},
                        {"extra": "x"}, {"issued_at": -1}, {"binding_version": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_claims({**self.payload, **changes}, "device-a", 1000)

    def test_old_key_and_wrong_product_rejected(self):
        with self.assertRaises(ValueError):
            validate_claims({**self.payload, "product": "AnotherApp"}, "device-a", 1000)
        permanent = {**self.payload, "duration_type": "permanent", "expires_at": None}
        self.assertIsNone(validate_claims(permanent, "device-a", 100000).expires_at)
        # A genuine expired credential must remain usable as evidence for server-side renewal checks.
        self.assertEqual(validate_claims(self.payload, "device-a", 3000, False).license_id, "card-a")

    def test_config_and_private_key_file_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with self.assertRaises(ValueError): ServerSettings(base, "http://example.com")
            settings = ServerSettings(base, "https://license.txblog.cn")
            with self.assertRaises(ValueError): settings.validate_secrets()
            with self.assertRaises(ValueError): Signer.from_file(base / "absent.pem", "test-key")
            path = base / "temporary-test.pem"
            path.write_bytes(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()))
            signer = Signer.from_file(path, "test-key")
            self.assertEqual(verify_document(signer.sign(self.payload), self.keys), self.payload)
