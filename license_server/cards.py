from dataclasses import dataclass
from dataclasses import asdict
import hashlib
import hmac
import json
import secrets
import uuid
from license_protocol import Clock, LicenseClaims, PRODUCT
from .database import Database


class LicenseDenied(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class GeneratedBatch:
    batch_id: str
    cards: list[str]


@dataclass(frozen=True)
class CardView:
    id: str
    batch_id: str
    mask: str
    note: str
    duration_days: int | None
    status: str
    device_summary: str | None
    binding_version: int
    created_at: int
    activated_at: int | None
    expires_at: int | None
    last_check: int | None


@dataclass(frozen=True)
class CardPage:
    items: list[CardView]
    total: int
    page: int
    page_size: int


def positive(value, maximum=365000):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("positive_integer_required")
    return value


class CardService:
    def __init__(self, db: Database, digest_secret: bytes, clock: Clock, key_id="primary-v1"):
        if len(digest_secret) < 32: raise ValueError("invalid_digest_key")
        self.db, self._secret, self.clock, self.key_id = db, digest_secret, clock, key_id

    def _digest(self, card):
        if type(card) is not str or len(card) > 128: raise ValueError("invalid_card")
        return hmac.new(self._secret, card.strip().upper().encode(), hashlib.sha256).hexdigest()

    def _audit(self, connection, actor, action, card_id, details=None):
        connection.execute("INSERT INTO audit(actor,action,card_id,at,details) VALUES(?,?,?,?,?)",
                           (actor, action, card_id, self.clock.wall_time(), json.dumps(details or {}, ensure_ascii=False)))

    def create_batch(self, count, duration_days, note):
        positive(count, 1000)
        if duration_days is not None: positive(duration_days)
        if type(note) is not str or len(note) > 500: raise ValueError("invalid_note")
        batch_id, cards = uuid.uuid4().hex, []
        with self.db.connect(write=True) as connection:
            for _ in range(count):
                card = "DYL-" + secrets.token_hex(20).upper()
                card_id = uuid.uuid4().hex
                connection.execute("""INSERT INTO cards(id,batch_id,digest,mask,note,duration_days,status,
                    binding_version,created_at) VALUES(?,?,?,?,?,?,'enabled',1,?)""",
                    (card_id, batch_id, self._digest(card), "DYL-…" + card[-6:], note, duration_days, self.clock.wall_time()))
                self._audit(connection, "admin", "create", card_id, {"batch_id": batch_id, "duration_days": duration_days})
                cards.append(card)
        return GeneratedBatch(batch_id, cards)

    def _check_state(self, row):
        if row is None: raise LicenseDenied("unknown_card")
        if row["status"] != "enabled": raise LicenseDenied(row["status"])
        if row["expires_at"] is not None and self.clock.wall_time() >= row["expires_at"]:
            raise LicenseDenied("expired")

    def _claims(self, row):
        return LicenseClaims(1, PRODUCT, row["id"], row["device_id"], row["binding_version"],
                             self.clock.wall_time(), "permanent" if row["duration_days"] is None else "limited",
                             row["expires_at"], self.key_id)

    def bind(self, card, device_id):
        if type(device_id) is not str or not 1 <= len(device_id) <= 128: raise ValueError("invalid_device")
        with self.db.connect(write=True) as connection:
            row = connection.execute("SELECT * FROM cards WHERE digest=?", (self._digest(card),)).fetchone()
            self._check_state(row)
            if row["device_id"] is not None and row["device_id"] != device_id: raise LicenseDenied("binding_mismatch")
            if row["device_id"] is None:
                activated_at = row["activated_at"] if row["activated_at"] is not None else self.clock.wall_time()
                expiry = row["expires_at"]
                if row["activated_at"] is None and row["duration_days"] is not None:
                    expiry = activated_at + row["duration_days"] * 86400
                connection.execute("UPDATE cards SET device_id=?,activated_at=?,expires_at=? WHERE id=?",
                                   (device_id, activated_at, expiry, row["id"]))
                self._audit(connection, "client", "activate", row["id"])
            connection.execute("UPDATE cards SET last_check=? WHERE id=?", (self.clock.wall_time(), row["id"]))
            return self._claims(connection.execute("SELECT * FROM cards WHERE id=?", (row["id"],)).fetchone())

    def check(self, claims, device_id):
        with self.db.connect(write=True) as connection:
            row = connection.execute("SELECT * FROM cards WHERE id=?", (claims.license_id,)).fetchone()
            # A removed/unknown genuine card fails as archived, not as a transient transport failure.
            if row is None: raise LicenseDenied("archived")
            self._check_state(row)
            if row["device_id"] != device_id or claims.device_id != device_id or row["binding_version"] != claims.binding_version:
                raise LicenseDenied("binding_mismatch")
            connection.execute("UPDATE cards SET last_check=? WHERE id=?", (self.clock.wall_time(), row["id"]))
            return self._claims(row)

    def _view(self, row):
        status = row["status"]
        if status == "enabled":
            if row["expires_at"] is not None and row["expires_at"] <= self.clock.wall_time(): status = "expired"
            elif row["activated_at"] is None: status = "unused"
            elif row["device_id"] is None: status = "unbound"
            else: status = "active"
        return CardView(row["id"], row["batch_id"], row["mask"], row["note"], row["duration_days"], status,
                        hashlib.sha256(row["device_id"].encode()).hexdigest()[:12] if row["device_id"] else None,
                        row["binding_version"], row["created_at"], row["activated_at"], row["expires_at"], row["last_check"])

    def get(self, card_id):
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
            if row is None: raise ValueError("card_not_found")
            return self._view(row)

    def update(self, card_id, action, values, actor):
        if action not in ("edit", "renew", "disable", "restore", "unbind", "archive"):
            raise ValueError("invalid_action")
        if type(values) is not dict: raise ValueError("invalid_values")
        allowed = {"edit": {"note", "duration_days", "expires_at"}, "renew": {"days"}}.get(action, set())
        if set(values) - allowed: raise ValueError("invalid_fields")
        with self.db.connect(write=True) as connection:
            row = connection.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
            if row is None: raise ValueError("card_not_found")
            if row["status"] == "archived": raise ValueError("card_archived")
            changes = {}
            if action == "edit":
                if "note" in values:
                    if type(values["note"]) is not str or len(values["note"]) > 500: raise ValueError("invalid_note")
                    changes["note"] = values["note"]
                if "duration_days" in values:
                    if row["activated_at"] is not None: raise ValueError("edit_expiry_for_activated_card")
                    days = values["duration_days"]
                    if days is not None: positive(days)
                    changes["duration_days"] = days
                if "expires_at" in values:
                    if row["activated_at"] is None: raise ValueError("edit_duration_for_unused_card")
                    expiry = values["expires_at"]
                    if expiry is not None and (type(expiry) is not int or not self.clock.wall_time() < expiry <= 253402300799):
                        raise ValueError("future_expiry_required")
                    changes.update(expires_at=expiry, duration_days=None if expiry is None else row["duration_days"] or 1)
            elif action == "renew":
                days = positive(values.get("days"))
                if row["expires_at"] is None: raise ValueError("renew_requires_activated_limited_card")
                changes["expires_at"] = max(row["expires_at"], self.clock.wall_time()) + days * 86400
            elif action == "unbind":
                changes.update(device_id=None, binding_version=row["binding_version"] + 1)
            else:
                changes["status"] = {"disable": "disabled", "restore": "enabled", "archive": "archived"}[action]
            if not changes: raise ValueError("no_changes")
            # Column names come only from the literal allowlist above, never from request input.
            assignment = ",".join(f"{field}=?" for field in changes)
            connection.execute(f"UPDATE cards SET {assignment} WHERE id=?", (*changes.values(), card_id))
            audit_changes = {k: v for k, v in changes.items() if k != "device_id"}
            self._audit(connection, actor, action, card_id, audit_changes)
            return self._view(connection.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone())

    def list_cards(self, filters, page, page_size):
        positive(page, 1000000)
        positive(page_size, 100)
        clauses, params = [], []
        if filters.get("status") != "archived": clauses.append("status!='archived'")
        if filters.get("q"):
            query = str(filters["q"])
            if len(query) > 500: raise ValueError("query_too_long")
            clauses.append("(instr(note,?)>0 OR instr(id,?)>0 OR instr(mask,?)>0 OR batch_id=?)")
            params.extend([query] * 4)
        if filters.get("batch_id"):
            clauses.append("batch_id=?")
            params.append(filters["batch_id"])
        for name, operator in (("expires_before", "<="), ("expires_after", ">=")):
            if filters.get(name) is not None:
                clauses.append("expires_at" + operator + "?")
                params.append(int(filters[name]))
        with self.db.connect() as connection:
            rows = connection.execute("SELECT * FROM cards WHERE " + (" AND ".join(clauses) or "1") + " ORDER BY created_at DESC,id", params).fetchall()
        items = [self._view(row) for row in rows]
        if filters.get("status"): items = [x for x in items if x.status == filters["status"]]
        return CardPage(items[(page - 1) * page_size:page * page_size], len(items), page, page_size)

    def audit(self, page=1, page_size=50):
        positive(page, 1000000)
        positive(page_size, 100)
        with self.db.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT actor,action,card_id,at,details FROM audit ORDER BY id DESC LIMIT ? OFFSET ?", (page_size, (page-1)*page_size))]
