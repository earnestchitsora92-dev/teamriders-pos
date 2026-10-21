"""Copy the local SQLite database into PostgreSQL (used for the Render/Neon deployment).

Usage:
    set DATABASE_URL=postgresql://user:pass@host/db?sslmode=require
    python scripts/migrate_to_postgres.py [path-to-sqlite.db]

The destination tables are replaced with the local data; sessions are not copied
(users sign in again).
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db

TABLES = [
    "users",
    "products",
    "stock_movements",
    "discounts",
    "sales",
    "sale_items",
    "quotations",
    "quotation_items",
    "stock_alerts",
    "cash_transactions",
    "closings",
    "audit_log",
    "sequences",
    "settings",
]


def main() -> None:
    if not db.IS_POSTGRES:
        raise SystemExit("DATABASE_URL must point at PostgreSQL before running this script.")
    source = sys.argv[1] if len(sys.argv) > 1 else str(db.DB_PATH)
    if not os.path.exists(source):
        raise SystemExit(f"SQLite database not found: {source}")

    src = sqlite3.connect(source)
    src.row_factory = sqlite3.Row

    db.init_db(seed=False)

    with db.tx() as conn:
        for table in reversed(TABLES):
            conn.execute(f"DELETE FROM {table}")
        for table in TABLES:
            rows = src.execute(f"SELECT * FROM {table}").fetchall()
            for row in rows:
                data = dict(row)
                columns = list(data.keys())
                sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
                conn.execute(sql, tuple(data[c] for c in columns))
            print(f"{table}: {len(rows)} row(s)")
        for table in TABLES:
            if table in ("settings", "sequences"):
                continue
            conn.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table}), 1), "
                f"(SELECT COUNT(*) > 0 FROM {table}))"
            )
    src.close()
    print("Migration complete.")


if __name__ == "__main__":
    main()
