import base64
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from license_protocol import canonical_bytes


class Signer:
    def __init__(self, private_key: Ed25519PrivateKey, key_id: str):
        if not isinstance(private_key, Ed25519PrivateKey) or not 1 <= len(key_id) <= 128:
            raise ValueError("invalid_signing_key")
        self._key, self.key_id = private_key, key_id

    @classmethod
    def from_file(cls, path: Path, key_id: str):
        try:
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
            return cls(key, key_id)
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError("signing_key_unavailable") from exc

    def sign(self, payload: dict) -> dict:
        data = {**payload, "key_id": self.key_id}
        signature = self._key.sign(canonical_bytes(data))
        return {"payload": data, "signature": base64.urlsafe_b64encode(signature).decode("ascii")}

    def public_bytes(self) -> bytes:
        return self._key.public_key().public_bytes_raw()
