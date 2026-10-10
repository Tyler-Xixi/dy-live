"""Offline-capable online license cache; background networking never drives purchases."""
from __future__ import annotations
import json
import os
import secrets
import threading
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler
import ssl
from license_protocol import Clock, LicenseStatus, strict_json_loads, verify_document, verify_envelope, validate_claims


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirect_not_allowed")


class LicenseTransport:
    def __init__(self, origin="https://license.txblog.cn"):
        parsed = urlsplit(origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("https_origin_required")
        self.origin = origin
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))

    def post(self, path: str, body: dict) -> dict:
        if path not in ("/api/v1/activate", "/api/v1/check"): raise ValueError("invalid_endpoint")
        request = Request(self.origin + path, data=json.dumps(body).encode("utf-8"),
                          headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
        with self.opener.open(request, timeout=5) as response:
            if response.status != 200: raise OSError("service_unavailable")
            return strict_json_loads(response.read(65537))


class OnlineLicenseClient:
    def __init__(self, data_dir: Path, device_id: str, public_keys: dict, transport=None, clock=None):
        self.path = Path(data_dir) / "online_license.json"
        self.device_id, self.public_keys = device_id, public_keys
        self.transport, self.clock = transport or LicenseTransport(), clock or Clock()
        self.claims, self.credential, self._last_envelope = None, None, None
        self._last_nonce, self._denial, self._problem = "", None, None
        self._lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._cache_dirty = threading.Event()
        self._persist_thread = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._generation = 0
        self._wall_anchor = self.clock.wall_time()
        self._response_wall = self._wall_anchor
        self._server_anchor = self._wall_anchor
        self._mono_anchor = self.clock.monotonic_time()
        self._watermark = self._wall_anchor
        self._rollback = False

    def verify_saved(self) -> LicenseStatus:
        with self._lock:
            try:
                data = strict_json_loads(self.path.read_bytes())
                claims = validate_claims(verify_document(data["credential"], self.public_keys), self.device_id,
                                         self.clock.wall_time(), enforce_expiry=False)
                envelope = verify_envelope(data["last_envelope"], self.public_keys, data["last_nonce"])
                if type(data["wall_at_save"]) is not int or type(data["watermark"]) is not int:
                    raise ValueError("invalid_cache")
                self.claims, self.credential = claims, data["credential"]
                self._last_envelope, self._last_nonce = data["last_envelope"], data["last_nonce"]
                self._denial = envelope["result"] if envelope["result"] != "allow" else None
                if envelope["result"] == "allow" and envelope["credential"] != self.credential:
                    raise ValueError("invalid_cache")
                wall = self.clock.wall_time()
                self._wall_anchor, self._mono_anchor = wall, self.clock.monotonic_time()
                self._response_wall = data["wall_at_save"]
                self._server_anchor = envelope["server_time"] + max(0, wall-data["wall_at_save"])
                self._watermark = max(wall, data["watermark"])
                self._rollback = wall < data["watermark"] - 5
                self._problem = None
            except (ValueError, OSError, KeyError, TypeError):
                self.claims, self.credential, self._denial = None, None, None
                self._problem = "activation_required"
        if self.claims: self._start_persistence()
        return self.snapshot()

    def snapshot(self) -> LicenseStatus:
        # A bounded memory-only operation: no file reads, signatures or HTTP here.
        with self._lock:
            expiry = self.claims.expires_at if self.claims else None
            if not self.claims: return LicenseStatus(False, "online", "activation_required")
            if self._denial: return LicenseStatus(False, "online", self._denial, expiry)
            if self._problem == "storage_error": return LicenseStatus(False, "online", "storage_error", expiry)
            wall = self.clock.wall_time()
            self._rollback = self._rollback or wall < self._watermark-5
            if wall > self._watermark:
                self._watermark = wall
                self._cache_dirty.set()
            if self._rollback: return LicenseStatus(False, "online", "clock_confirmation_required", expiry)
            elapsed = max(0, self.clock.monotonic_time()-self._mono_anchor, wall-self._wall_anchor)
            now = self._server_anchor + elapsed
            if expiry is not None and now >= expiry: return LicenseStatus(False, "online", "expired", expiry)
            return LicenseStatus(True, "online", self._problem or "valid", expiry)

    def activate(self, card: str) -> LicenseStatus:
        if type(card) is not str or not card.strip().upper().startswith("DYL-") or len(card) > 128:
            return LicenseStatus(False, "online", "invalid_card")
        return self._request("/api/v1/activate", {"card": card.strip().upper()})

    def refresh(self) -> LicenseStatus:
        with self._lock:
            if not self.credential: return self.snapshot()
            credential = self.credential
        return self._request("/api/v1/check", {"credential": credential})

    def _request(self, endpoint, extra):
        with self._lock:
            self._generation += 1
            generation = self._generation
            nonce = secrets.token_urlsafe(32)
        try:
            reply = self.transport.post(endpoint, {**extra, "nonce": nonce, "device_id": self.device_id, "client_version": "online-v1"})
            envelope = verify_envelope(reply, self.public_keys, nonce)
            claims = None
            if envelope["result"] == "allow":
                claims = validate_claims(verify_document(envelope["credential"], self.public_keys), self.device_id, envelope["server_time"])
                if endpoint.endswith("check") and claims.license_id != extra["credential"]["payload"]["license_id"]:
                    raise ValueError("wrong_license")
            with self._lock:
                if generation != self._generation or self._stop.is_set(): return self.snapshot()
                if claims:
                    self.claims, self.credential, self._denial = claims, envelope["credential"], None
                elif self.claims:
                    self._denial = envelope["result"]
                else:
                    return LicenseStatus(False, "online", envelope["result"])
                self._last_envelope, self._last_nonce = reply, nonce
                self._server_anchor = envelope["server_time"]
                self._wall_anchor = self.clock.wall_time()
                self._response_wall = self._wall_anchor
                self._mono_anchor = self.clock.monotonic_time()
                self._watermark, self._rollback = self._wall_anchor, False
                self._problem = None
            self._save()
            self._start_persistence()
            return self.snapshot()
        except (ValueError, OSError, KeyError, TypeError):
            with self._lock:
                if generation == self._generation and self._problem != "storage_error":
                    self._problem = "network_unconfirmed"
                return self.snapshot()

    def _save(self):
        # Serial disk writes, but never hold the purchase snapshot lock during I/O.
        with self._save_lock:
            with self._lock:
                if not self.credential: return
                data = dict(credential=self.credential, last_envelope=self._last_envelope, last_nonce=self._last_nonce,
                            wall_at_save=self._response_wall, watermark=self._watermark)
            temporary = self.path.with_suffix("." + secrets.token_hex(6) + ".tmp")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with temporary.open("x", encoding="utf-8") as stream:
                    json.dump(data, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            except OSError:
                with self._lock: self._problem = "storage_error"
                raise
            finally:
                try: temporary.unlink(missing_ok=True)
                except OSError: pass

    def _start_persistence(self):
        with self._lock:
            if self._persist_thread or self._stop.is_set(): return
            def persist():
                while not self._stop.is_set():
                    if not self._cache_dirty.wait(1): continue
                    if self._stop.is_set(): break
                    self._cache_dirty.clear()
                    try: self._save()
                    except OSError: pass
                    # Coalesce ordinary wall-time progress to at most one write per second.
                    self._stop.wait(1)
            self._persist_thread = threading.Thread(target=persist, daemon=True, name="license-cache")
            self._persist_thread.start()

    def start_background(self, on_change):
        if self._thread and self._thread.is_alive():
            self._wake.set()
            return
        def work():
            failures = 0
            previous = None
            while not self._stop.is_set():
                status = self.refresh()
                if status != previous:
                    on_change(status)
                    previous = status
                failed = self._problem == "network_unconfirmed"
                delay = (30, 60, 120, 300, 600)[min(failures, 4)] if failed else 600
                failures = failures+1 if failed else 0
                self._wake.wait(delay)
                self._wake.clear()
        self._thread = threading.Thread(target=work, daemon=True, name="license-check")
        self._thread.start()

    def close(self):
        self._stop.set()
        self._wake.set()
        self._cache_dirty.set()
        if self._persist_thread and self._persist_thread is not threading.current_thread():
            self._persist_thread.join(2)
        try: self._save()
        except OSError: pass
