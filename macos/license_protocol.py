"""Versioned signed licensing data. No network or private signing keys here."""
from __future__ import annotations
import base64
import json
import time
from dataclasses import dataclass
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PRODUCT = "DYLiveAssistant"


@dataclass(frozen=True)
class LicenseClaims:
    version: int
    product: str
    license_id: str
    device_id: str
    binding_version: int
    issued_at: int
    duration_type: str
    expires_at: int | None
    key_id: str


@dataclass(frozen=True)
class SignedDocument:
    payload: dict
    signature: str


@dataclass(frozen=True)
class LicenseStatus:
    allowed: bool
    mode: str
    reason: str
    expires_at: int | None = None


class Clock:
    def wall_time(self) -> int:
        return int(time.time())

    def monotonic_time(self) -> float:
        return time.monotonic()


def _safe_value(value, depth=0):
    if depth > 12:
        raise ValueError("invalid_document")
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is list and len(value) <= 1000:
        for item in value:
            _safe_value(item, depth + 1)
        return
    if type(value) is dict and len(value) <= 100 and all(type(k) is str for k in value):
        for item in value.values():
            _safe_value(item, depth + 1)
        return
    raise ValueError("invalid_document")


def canonical_bytes(payload: dict) -> bytes:
    if type(payload) is not dict:
        raise ValueError("invalid_document")
    _safe_value(payload)
    result = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(result) > 32768:
        raise ValueError("document_too_large")
    return result


def strict_json_loads(source: str | bytes):
    if len(source) > 65536:
        raise ValueError("document_too_large")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result

    def invalid_number(_):
        raise ValueError("invalid_number")

    try:
        result = json.loads(source, object_pairs_hook=pairs, parse_float=invalid_number, parse_constant=invalid_number)
        _safe_value(result)
        return result
    except (TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError("invalid_document") from exc


def verify_document(document: dict, public_keys: dict[str, bytes]) -> dict:
    try:
        if type(document) is not dict or set(document) != {"payload", "signature"}:
            raise ValueError("invalid_document")
        payload, signature = document["payload"], document["signature"]
        if type(payload) is not dict or type(signature) is not str or len(signature) > 100:
            raise ValueError("invalid_document")
        key_id = payload.get("key_id")
        if type(key_id) is not str or key_id not in public_keys:
            raise ValueError("untrusted_key")
        raw = base64.b64decode(signature.encode("ascii"), altchars=b"-_", validate=True)
        Ed25519PublicKey.from_public_bytes(public_keys[key_id]).verify(raw, canonical_bytes(payload))
        return payload
    except (InvalidSignature, TypeError, KeyError, UnicodeError) as exc:
        raise ValueError("invalid_signature") from exc


def validate_claims(payload: dict, device_id: str, now: int, enforce_expiry: bool = True) -> LicenseClaims:
    fields = set(LicenseClaims.__dataclass_fields__)
    if type(payload) is not dict or set(payload) != fields:
        raise ValueError("invalid_claims")
    if type(payload["version"]) is not int or payload["version"] != 1 or payload["product"] != PRODUCT:
        raise ValueError("wrong_product_or_version")
    for field in ("license_id", "device_id", "key_id"):
        if type(payload[field]) is not str or not 1 <= len(payload[field]) <= 128:
            raise ValueError("invalid_claims")
    if payload["device_id"] != device_id:
        raise ValueError("binding_mismatch")
    if type(payload["binding_version"]) is not int or payload["binding_version"] < 1:
        raise ValueError("invalid_claims")
    if type(payload["issued_at"]) is not int or payload["issued_at"] < 0:
        raise ValueError("invalid_claims")
    expiry = payload["expires_at"]
    if payload["duration_type"] == "permanent":
        if expiry is not None:
            raise ValueError("invalid_claims")
    elif payload["duration_type"] == "limited":
        if type(expiry) is not int or expiry <= 0:
            raise ValueError("invalid_claims")
        if enforce_expiry and now >= expiry:
            raise ValueError("expired")
    else:
        raise ValueError("invalid_claims")
    return LicenseClaims(**payload)


def verify_envelope(document: dict, public_keys: dict[str, bytes], nonce: str) -> dict:
    payload = verify_document(document, public_keys)
    if set(payload) != {"version", "product", "key_id", "nonce", "server_time", "result", "credential"}:
        raise ValueError("invalid_envelope")
    if type(payload["version"]) is not int or payload["version"] != 1 or payload["product"] != PRODUCT:
        raise ValueError("invalid_envelope")
    if payload["nonce"] != nonce or type(payload["server_time"]) is not int or payload["server_time"] < 0:
        raise ValueError("invalid_envelope")
    if payload["result"] not in ("allow", "disabled", "archived", "expired", "binding_mismatch"):
        raise ValueError("invalid_envelope")
    if (payload["result"] == "allow") != (type(payload["credential"]) is dict):
        raise ValueError("invalid_envelope")
    if payload["result"] != "allow" and payload["credential"] is not None:
        raise ValueError("invalid_envelope")
    return payload
