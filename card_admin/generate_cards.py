#!/usr/bin/env python3
"""Generate card keys and store only their SHA-256 hashes in SQLite."""

from __future__ import annotations

import argparse
import csv
import hashlib
import secrets
import sqlite3
import time
from pathlib import Path


ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def make_card() -> str:
    groups = ["".join(secrets.choice(ALPHABET) for _ in range(5)) for _ in range(4)]
    return "DYL-" + "-".join(groups)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def initialize(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT NOT NULL UNIQUE,
            key_hint TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unused',
            created_at INTEGER NOT NULL,
            activated_at INTEGER,
            machine_id TEXT,
            activation_id TEXT UNIQUE,
            token_hash TEXT
        )
        """
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--db", default=str(Path(__file__).with_name("cards.db")))
    parser.add_argument("--out", default=str(Path(__file__).with_name("generated_cards.csv")))
    args = parser.parse_args()
    if not 1 <= args.count <= 10000:
        raise SystemExit("count must be between 1 and 10000")

    database = Path(args.db).resolve()
    output = Path(args.out).resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite existing card export: {output}")
    database.parent.mkdir(parents=True, exist_ok=True)
    cards: list[str] = []
    now = int(time.time())
    with sqlite3.connect(database) as connection:
        initialize(connection)
        while len(cards) < args.count:
            card = make_card()
            try:
                connection.execute(
                    "INSERT INTO cards(key_hash, key_hint, status, created_at) VALUES (?, ?, 'unused', ?)",
                    (digest(card), f"{card[:9]}...{card[-5:]}", now),
                )
            except sqlite3.IntegrityError:
                continue
            cards.append(card)

    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["序号", "卡密", "状态"])
        for index, card in enumerate(cards, 1):
            writer.writerow([index, card, "未使用"])
    print(f"Generated {len(cards)} cards")
    print(f"Database: {database}")
    print(f"Plaintext export: {output}")


if __name__ == "__main__":
    main()
