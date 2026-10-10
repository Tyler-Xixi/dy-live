"""Small dedicated SQLite store. Write transactions include their audit entries."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connect(self, write=False):
        connection = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            if write: connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write: connection.commit()
        except BaseException:
            if write: connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1): raise ValueError("unsupported_database_version")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS cards (
                    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, digest TEXT UNIQUE NOT NULL,
                    mask TEXT NOT NULL, note TEXT NOT NULL, duration_days INTEGER,
                    status TEXT NOT NULL CHECK(status IN ('enabled','disabled','archived')),
                    device_id TEXT, binding_version INTEGER NOT NULL CHECK(binding_version>=1),
                    created_at INTEGER NOT NULL, activated_at INTEGER, expires_at INTEGER, last_check INTEGER,
                    CHECK(duration_days IS NULL OR duration_days>0)
                );
                CREATE INDEX IF NOT EXISTS cards_batch ON cards(batch_id);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
                    card_id TEXT, at INTEGER NOT NULL, details TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admin (
                    id INTEGER PRIMARY KEY CHECK(id=1), username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, csrf TEXT NOT NULL, created_at INTEGER NOT NULL, seen_at INTEGER NOT NULL
                );
                PRAGMA user_version=1;
                COMMIT;
            """)

    def backup(self, destination: Path):
        destination = Path(destination)
        if destination.resolve() == self.path.resolve() or destination.exists():
            raise ValueError("backup_destination_must_be_new")
        with self.connect() as source:
            target = sqlite3.connect(str(destination))
            try: source.backup(target)
            finally: target.close()
