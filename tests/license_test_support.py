import tempfile
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from license_server.config import ServerSettings


class TestClock:
    def __init__(self): self.now = 1000
    def wall_time(self): return self.now
    def monotonic_time(self): return float(self.now)


def server_fixture():
    temp = tempfile.TemporaryDirectory()
    base = Path(temp.name)
    secrets_dir = base / "secrets"
    secrets_dir.mkdir()
    key = Ed25519PrivateKey.generate()
    (secrets_dir / "signing.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (secrets_dir / "digest.key").write_bytes(b"x" * 32)
    settings = ServerSettings(base, "https://license.txblog.cn", True)
    return temp, settings, TestClock(), {"primary-v1": key.public_key().public_bytes_raw()}
