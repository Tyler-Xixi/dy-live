from dataclasses import dataclass
import hashlib
import secrets
import threading
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from .database import Database


class AuthDenied(ValueError):
    pass


@dataclass(frozen=True)
class AdminSession:
    token: str
    csrf: str


class AuthService:
    def __init__(self, db: Database, clock):
        self.db, self.clock = db, clock
        self.hasher = PasswordHasher()
        self._dummy = self.hasher.hash(secrets.token_urlsafe(32))
        self._attempts, self._lock = {}, threading.Lock()

    def _password(self, password):
        if type(password) is not str or not 12 <= len(password) <= 256:
            raise ValueError("password_length_12_to_256")

    def initialize(self, username, password):
        self._password(password)
        if type(username) is not str or not 1 <= len(username) <= 64: raise ValueError("invalid_username")
        with self.db.connect(write=True) as connection:
            if connection.execute("SELECT id FROM admin").fetchone(): raise ValueError("admin_already_exists")
            connection.execute("INSERT INTO admin VALUES(1,?,?)", (username, self.hasher.hash(password)))

    def login(self, username, password, source):
        if type(username) is not str or len(username) > 64 or type(password) is not str or len(password) > 256:
            raise AuthDenied("invalid_credentials")
        now = self.clock.wall_time()
        # All attempted account names share the service's sole administrator limit.
        with self._lock:
            self._attempts = {key: [t for t in times if t > now-900] for key, times in self._attempts.items() if any(t > now-900 for t in times)}
            keys = ("account", "source:" + str(source)[:128])
            if any(len(self._attempts.get(key, [])) >= 5 for key in keys): raise AuthDenied("rate_limited")
            for key in keys: self._attempts.setdefault(key, []).append(now)
        with self.db.connect(write=True) as connection:
            row = connection.execute("SELECT username,password_hash FROM admin WHERE id=1").fetchone()
            candidate_hash = row["password_hash"] if row else self._dummy
            try: valid = self.hasher.verify(candidate_hash, password)
            except (VerificationError, InvalidHashError): valid = False
            if not row or username != row["username"] or not valid: raise AuthDenied("invalid_credentials")
            if self.hasher.check_needs_rehash(row["password_hash"]):
                connection.execute("UPDATE admin SET password_hash=? WHERE id=1", (self.hasher.hash(password),))
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            connection.execute("DELETE FROM sessions WHERE created_at<=? OR seen_at<=?", (now-28800, now-1800))
            connection.execute("INSERT INTO sessions VALUES(?,?,?,?)", (self._digest(token), csrf, now, now))
        with self._lock:
            for key in keys: self._attempts.pop(key, None)
        return AdminSession(token, csrf)

    def _digest(self, token):
        return hashlib.sha256(token.encode()).hexdigest()

    def require(self, session_id):
        if type(session_id) is not str or not 20 <= len(session_id) <= 100: raise AuthDenied("login_required")
        now = self.clock.wall_time()
        with self.db.connect(write=True) as connection:
            row = connection.execute("SELECT * FROM sessions WHERE digest=?", (self._digest(session_id),)).fetchone()
            if row is None: raise AuthDenied("login_required")
            if now >= row["created_at"]+28800 or now >= row["seen_at"]+1800 or now < row["created_at"]:
                connection.execute("DELETE FROM sessions WHERE digest=?", (self._digest(session_id),))
                # Commit deletion even though the caller is denied.
                connection.commit()
                raise AuthDenied("session_expired")
            connection.execute("UPDATE sessions SET seen_at=? WHERE digest=?", (now, self._digest(session_id)))
            return AdminSession(session_id, row["csrf"])

    def logout(self, session_id):
        if not session_id: return
        with self.db.connect(write=True) as connection:
            connection.execute("DELETE FROM sessions WHERE digest=?", (self._digest(session_id),))

    def reset_password(self, password):
        self._password(password)
        with self.db.connect(write=True) as connection:
            if not connection.execute("SELECT id FROM admin").fetchone(): raise ValueError("admin_not_initialized")
            connection.execute("UPDATE admin SET password_hash=? WHERE id=1", (self.hasher.hash(password),))
            connection.execute("DELETE FROM sessions")
