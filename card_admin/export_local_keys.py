#!/usr/bin/env python3
"""Export card hashes from the admin database into the offline client module."""

from __future__ import annotations

import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "cards.db"
OUTPUT_PATH = BASE_DIR.parent / "local_license_keys.py"


def main() -> None:
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute("SELECT key_hash FROM cards ORDER BY id").fetchall()
    hashes = [str(row[0]) for row in rows]
    content = [
        '"""Generated offline card hashes. Do not edit manually."""',
        "",
        "VALID_CARD_HASHES = frozenset({",
    ]
    content.extend(f'    "{value}",' for value in hashes)
    content.extend(["})", ""])
    OUTPUT_PATH.write_text("\n".join(content), encoding="utf-8")
    print(f"Exported {len(hashes)} card hashes to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
