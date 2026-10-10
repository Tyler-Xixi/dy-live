from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import os


@dataclass(frozen=True)
class ServerSettings:
    data_dir: Path
    origin: str = "https://license.txblog.cn"
    test_mode: bool = False
    key_id: str = "primary-v1"
    secrets_dir: Path | None = None
    proxy_rate_limited: bool = False

    def __post_init__(self):
        parsed = urlsplit(self.origin)
        if parsed.scheme != "https" and not (self.test_mode and parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "testserver")):
            raise ValueError("https_origin_required")
        if not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("invalid_origin")

    @property
    def secret_path(self):
        return self.secrets_dir if self.secrets_dir is not None else self.data_dir / "secrets"

    @property
    def database_path(self):
        return self.data_dir / "licenses.sqlite3"

    def validate_secrets(self):
        if not (self.secret_path / "signing.pem").is_file() or not (self.secret_path / "digest.key").is_file():
            raise ValueError("initialize_server_secrets_first")
        if len((self.secret_path / "digest.key").read_bytes()) != 32:
            raise ValueError("invalid_digest_key")

    @classmethod
    def from_env(cls):
        return cls(Path(os.environ.get("LICENSE_DATA_DIR", "/data")),
                   os.environ.get("LICENSE_ORIGIN", "https://license.txblog.cn"),
                   False, os.environ.get("LICENSE_KEY_ID", "primary-v1"),
                   Path(os.environ.get("LICENSE_SECRETS_DIR", "/secrets")),
                   os.environ.get("LICENSE_LIMIT_AT_PROXY", "0") == "1")
