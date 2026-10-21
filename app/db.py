"""SQLite storage layer: schema, connection helpers, seed data, settings."""
from __future__ import annotations
import atexit
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent
DATA_DIR = Path(os.environ.get("MX_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("MX_DB_PATH", DATA_DIR / "mxspares.db"))

# When DATABASE_URL is set (Render/Neon), all data lives in PostgreSQL instead of SQLite.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
IS_POSTGRES = DATABASE_URL.startswith("postgresql://")

try:
    from psycopg.errors import UniqueViolation as _PgUniqueViolation
except Exception:  # psycopg is optional for SQLite-only installs
    _PgUniqueViolation = None

IntegrityError = sqlite3.IntegrityError if not IS_POSTGRES else (_PgUniqueViolation or Exception)

TS_FORMAT = "%Y-%m-%d %H:%M:%S"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    full_name     TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'cashier')),
    password_hash TEXT NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash   TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sequences (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS products (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sku           TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    category      TEXT NOT NULL DEFAULT 'General',
    brand         TEXT NOT NULL DEFAULT '',
    compatible    TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    cost_cents    INTEGER NOT NULL DEFAULT 0,
    price_cents   INTEGER NOT NULL DEFAULT 0,
    stock_qty     INTEGER NOT NULL DEFAULT 0,
    reorder_level INTEGER NOT NULL DEFAULT 3,
    is_active     INTEGER NOT NULL DEFAULT 1,
    search_text   TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_search ON products(search_text);
CREATE INDEX IF NOT EXISTS idx_products_active ON products(is_active, stock_qty);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);

CREATE TABLE IF NOT EXISTS stock_movements (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id     INTEGER NOT NULL REFERENCES products(id),
    kind           TEXT NOT NULL,
    qty_delta      INTEGER NOT NULL,
    unit_cost_cents INTEGER NOT NULL DEFAULT 0,
    stock_after    INTEGER NOT NULL,
    reference      TEXT,
    note           TEXT,
    user_id        INTEGER REFERENCES users(id),
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mov_product ON stock_movements(product_id, created_at);
CREATE INDEX IF NOT EXISTS idx_mov_created ON stock_movements(created_at);

CREATE TABLE IF NOT EXISTS discounts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    code           TEXT NOT NULL UNIQUE,
    requested_by   INTEGER NOT NULL REFERENCES users(id),
    approved_by    INTEGER REFERENCES users(id),
    status         TEXT NOT NULL DEFAULT 'pending',
    kind           TEXT NOT NULL,
    value          INTEGER NOT NULL,
    subtotal_cents INTEGER NOT NULL,
    discount_cents INTEGER NOT NULL,
    max_cents      INTEGER NOT NULL,
    reason         TEXT NOT NULL,
    admin_note     TEXT NOT NULL DEFAULT '',
    sale_id        INTEGER,
    created_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL,
    used_at        TEXT,
    decided_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_discounts_created ON discounts(created_at);
CREATE INDEX IF NOT EXISTS idx_discounts_status ON discounts(status, created_at);

CREATE TABLE IF NOT EXISTS sales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_no      TEXT NOT NULL UNIQUE,
    cashier_id      INTEGER NOT NULL REFERENCES users(id),
    customer_name   TEXT NOT NULL DEFAULT '',
    customer_phone  TEXT NOT NULL DEFAULT '',
    customer_email  TEXT NOT NULL DEFAULT '',
    subtotal_cents  INTEGER NOT NULL,
    discount_cents  INTEGER NOT NULL DEFAULT 0,
    discount_id     INTEGER REFERENCES discounts(id),
    tax_cents       INTEGER NOT NULL DEFAULT 0,
    total_cents     INTEGER NOT NULL,
    cogs_cents      INTEGER NOT NULL DEFAULT 0,
    profit_cents    INTEGER NOT NULL DEFAULT 0,
    payment_method  TEXT NOT NULL DEFAULT 'cash',
    tendered_cents  INTEGER NOT NULL DEFAULT 0,
    change_cents    INTEGER NOT NULL DEFAULT 0,
    note            TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'completed',
    voided_by       INTEGER REFERENCES users(id),
    voided_at       TEXT,
    void_reason     TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sales_created ON sales(created_at);
CREATE INDEX IF NOT EXISTS idx_sales_status ON sales(status, created_at);
CREATE INDEX IF NOT EXISTS idx_sales_cashier ON sales(cashier_id, created_at);

CREATE TABLE IF NOT EXISTS sale_items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id          INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
    product_id       INTEGER NOT NULL REFERENCES products(id),
    sku              TEXT NOT NULL,
    name             TEXT NOT NULL,
    qty              INTEGER NOT NULL,
    unit_price_cents INTEGER NOT NULL,
    unit_cost_cents  INTEGER NOT NULL,
    line_total_cents INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sale_items_sale ON sale_items(sale_id);
CREATE INDEX IF NOT EXISTS idx_sale_items_product ON sale_items(product_id);

CREATE TABLE IF NOT EXISTS quotations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_no       TEXT NOT NULL UNIQUE,
    created_by     INTEGER NOT NULL REFERENCES users(id),
    customer_name  TEXT NOT NULL DEFAULT '',
    customer_email TEXT NOT NULL DEFAULT '',
    customer_phone TEXT NOT NULL DEFAULT '',
    subtotal_cents INTEGER NOT NULL,
    discount_cents INTEGER NOT NULL DEFAULT 0,
    tax_cents      INTEGER NOT NULL DEFAULT 0,
    total_cents    INTEGER NOT NULL,
    valid_until    TEXT,
    status         TEXT NOT NULL DEFAULT 'sent',
    note           TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quotations_created ON quotations(created_at);

CREATE TABLE IF NOT EXISTS quotation_items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    quotation_id     INTEGER NOT NULL REFERENCES quotations(id) ON DELETE CASCADE,
    product_id       INTEGER NOT NULL REFERENCES products(id),
    sku              TEXT NOT NULL,
    name             TEXT NOT NULL,
    qty              INTEGER NOT NULL,
    unit_price_cents INTEGER NOT NULL,
    line_total_cents INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quote_items_quote ON quotation_items(quotation_id);

CREATE TABLE IF NOT EXISTS stock_alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id    INTEGER NOT NULL REFERENCES products(id),
    email         TEXT NOT NULL,
    customer_name TEXT NOT NULL DEFAULT '',
    phone         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'waiting',
    note          TEXT NOT NULL DEFAULT '',
    created_by    INTEGER REFERENCES users(id),
    created_at    TEXT NOT NULL,
    notified_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_product ON stock_alerts(product_id, status);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON stock_alerts(status, created_at);

CREATE TABLE IF NOT EXISTS cash_transactions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    method       TEXT NOT NULL DEFAULT 'cash',
    reference    TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    user_id      INTEGER REFERENCES users(id),
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cash_created ON cash_transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_cash_kind ON cash_transactions(kind, created_at);

CREATE TABLE IF NOT EXISTS closings (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    period                     TEXT NOT NULL UNIQUE,
    opening_stock_value_cents  INTEGER NOT NULL DEFAULT 0,
    closing_stock_value_cents  INTEGER NOT NULL DEFAULT 0,
    purchases_cents            INTEGER NOT NULL DEFAULT 0,
    revenue_cents              INTEGER NOT NULL DEFAULT 0,
    discounts_cents            INTEGER NOT NULL DEFAULT 0,
    cogs_cents                 INTEGER NOT NULL DEFAULT 0,
    gross_profit_cents         INTEGER NOT NULL DEFAULT 0,
    expenses_cents             INTEGER NOT NULL DEFAULT 0,
    net_profit_cents           INTEGER NOT NULL DEFAULT 0,
    sales_count                INTEGER NOT NULL DEFAULT 0,
    cash_in_cents              INTEGER NOT NULL DEFAULT 0,
    cash_out_cents             INTEGER NOT NULL DEFAULT 0,
    cash_balance_cents         INTEGER NOT NULL DEFAULT 0,
    closed_by                  INTEGER NOT NULL REFERENCES users(id),
    closed_at                  TEXT NOT NULL,
    notes                      TEXT NOT NULL DEFAULT '',
    snapshot                   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    action     TEXT NOT NULL,
    entity     TEXT NOT NULL DEFAULT '',
    entity_id  TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '',
    ip         TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
"""

# PostgreSQL variant of the same schema (SQLite's AUTOINCREMENT becomes SERIAL).
SCHEMA_PG = SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")

_ID_TABLES = {
    "users", "products", "stock_movements", "discounts", "sales", "sale_items", "quotations",
    "quotation_items", "stock_alerts", "cash_transactions", "closings", "audit_log",
}
_INSERT_RE = re.compile(r"^\s*INSERT\s+INTO\s+([A-Za-z_]+)", re.IGNORECASE)

DEFAULT_SETTINGS = {
    "business_name": "Team Riders",
    "business_tagline": "Motorbike Spares for Riders",
    "business_address": "Plot 12, Biker Lane",
    "business_phone": "+254 700 000 000",
    "business_email": "sales@mxspares.example",
    "currency": "$",
    "tax_rate_percent": "0",
    "tax_label": "VAT",
    "max_discount_percent": "60",
    "low_stock_threshold": "5",
    "receipt_footer": "Thank you for your business. Ride safe!",
    "quotation_valid_days": "14",
    "quotation_terms": "Prices are valid until the date shown. Goods are subject to availability.",
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from": "",
    "smtp_tls": "1",
    "business_initialised": "0",
}

DEMO_PRODUCTS = [
    ("BRK-001", "Front Brake Pads (Set)", "Brakes", "Generic", "Honda CG125, CG150, Bajaj Boxer", 45000, 85000, 24, 5),
    ("BRK-002", "Rear Brake Shoes", "Brakes", "Generic", "Honda CG125, TVS HLX", 38000, 70000, 16, 5),
    ("CHN-001", "Drive Chain 428H-120L", "Transmission", "RK", "Honda CG125, Yamaha YBR125", 90000, 160000, 12, 4),
    ("CHN-002", "Chain & Sprocket Kit CG125", "Transmission", "Generic", "Honda CG125", 220000, 360000, 4, 2),
    ("SPR-001", "Rear Sprocket 42T", "Transmission", "Generic", "Honda CG125", 70000, 130000, 8, 3),
    ("SPK-001", "Spark Plug C7HSA", "Electrical", "NGK", "Honda CG125, Bajaj Boxer, TVS HLX", 12000, 25000, 60, 12),
    ("BTR-001", "Battery 12V 5Ah", "Electrical", "Yuasa", "Honda CG125, Yamaha YBR125", 220000, 340000, 4, 2),
    ("BLB-001", "Headlight Bulb 12V 35W", "Electrical", "Philips", "Universal", 9000, 18000, 40, 10),
    ("HRN-001", "Horn 12V Universal", "Electrical", "Bosch", "Universal", 25000, 45000, 0, 4),
    ("OIL-001", "Engine Oil 20W-50 1L", "Lubricants", "Castrol", "Universal 4-stroke", 48000, 75000, 30, 8),
    ("OIL-002", "Fork Oil 10W 500ml", "Lubricants", "Motul", "Universal", 32000, 55000, 10, 4),
    ("FLT-001", "Oil Filter CG125", "Engine", "Generic", "Honda CG125", 15000, 30000, 18, 5),
    ("AIR-001", "Air Filter Element", "Engine", "Generic", "Honda CG125, Bajaj Boxer", 20000, 40000, 16, 5),
    ("PIS-001", "Piston Kit 0.50mm", "Engine", "Generic", "Honda CG125", 150000, 240000, 5, 2),
    ("RNG-001", "Piston Rings Set", "Engine", "Generic", "Honda CG125", 70000, 120000, 9, 3),
    ("CRB-001", "Carburettor CG125", "Engine", "Generic", "Honda CG125", 260000, 420000, 3, 2),
    ("GSK-001", "Engine Gasket Set", "Engine", "Generic", "Honda CG125", 60000, 110000, 7, 3),
    ("TYR-001", "Tyre 2.75-18 Tube Type", "Tyres & Wheels", "MRF", "Universal", 260000, 390000, 6, 2),
    ("TYR-002", "Tyre 3.00-18 Tube Type", "Tyres & Wheels", "MRF", "Universal", 300000, 450000, 5, 2),
    ("TUB-001", "Inner Tube 2.75-18", "Tyres & Wheels", "MRF", "Universal", 60000, 110000, 14, 4),
    ("CLB-001", "Clutch Cable CG125", "Body & Controls", "Generic", "Honda CG125", 18000, 35000, 22, 6),
    ("LVR-001", "Brake Lever", "Body & Controls", "Generic", "Honda CG125, TVS HLX", 22000, 40000, 14, 5),
    ("MIR-001", "Side Mirror (Pair)", "Body & Controls", "Generic", "Universal", 40000, 70000, 6, 3),
    ("SHK-001", "Rear Shock Absorber (Pair)", "Suspension", "Generic", "Honda CG125", 320000, 520000, 2, 2),
    ("BEA-001", "Steering Bearing Kit", "Suspension", "Generic", "Honda CG125, Bajaj Boxer", 45000, 80000, 10, 3),
    ("FUL-001", "Fuel Filter", "Engine", "Generic", "Universal", 10000, 20000, 25, 8),
    ("SPD-001", "Speedometer Cable", "Body & Controls", "Generic", "Honda CG125", 16000, 30000, 11, 4),
]


def now_str() -> str:
    return datetime.now().strftime(TS_FORMAT)


def today_str() -> str:
    return date.today().isoformat()


def current_period() -> str:
    return date.today().strftime("%Y-%m")


def period_bounds(period: str) -> tuple[str, str]:
    """Return (start, end_exclusive) timestamps covering the calendar month."""
    year, month = (int(x) for x in period.split("-"))
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start.isoformat() + " 00:00:00", end.isoformat() + " 00:00:00"


def shift_period(period: str, months: int) -> str:
    year, month = (int(x) for x in period.split("-"))
    total = year * 12 + (month - 1) + months
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


class _PgRow(dict):
    """dict that also supports sqlite3.Row-style positional access (row[0])."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _pg_row_factory(cursor):
    names = [column.name for column in cursor.description or ()]

    def make_row(values):
        return _PgRow(zip(names, (_pg_value(v) for v in values)))

    return make_row


def _pg_value(value):
    """Postgres numeric aggregates come back as Decimal; the app expects ints/floats."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


class _PgCursor:
    """Small sqlite3.Cursor-like wrapper so the rest of the app is backend-agnostic."""

    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)


class _PgConnection:
    """sqlite3.Connection-like wrapper around a pooled psycopg connection."""

    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool

    def execute(self, sql: str, params: tuple | list = ()):
        text = sql.replace("?", "%s")
        match = _INSERT_RE.match(text)
        if match and match.group(1).lower() in _ID_TABLES and "returning" not in text.lower():
            text = text.rstrip().rstrip(";") + " RETURNING id"
            cursor = self._conn.execute(text, params)
            row = cursor.fetchone()
            lastrowid = None
            if row is not None:
                lastrowid = row["id"] if isinstance(row, dict) else row[0]
            return _PgCursor(cursor, lastrowid)
        return _PgCursor(self._conn.execute(text, params))

    def executescript(self, script: str):
        for statement in script.split(";"):
            if statement.strip():
                self._conn.execute(statement)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        try:
            self._conn.rollback()
        except Exception:
            pass
        self._pool.putconn(self._conn)


_pg_pool = None
_pg_pool_lock = threading.Lock()


def _postgres_pool():
    global _pg_pool
    with _pg_pool_lock:
        if _pg_pool is None:
            from psycopg_pool import ConnectionPool

            _pg_pool = ConnectionPool(
                DATABASE_URL,
                min_size=1,
                max_size=6,
                timeout=15,
                kwargs={"row_factory": _pg_row_factory},
                open=True,
            )
            atexit.register(_close_postgres_pool)
        return _pg_pool


def _close_postgres_pool() -> None:
    global _pg_pool
    pool, _pg_pool = _pg_pool, None
    if pool is not None:
        try:
            pool.close()
        except Exception:
            pass


def connect():
    if IS_POSTGRES:
        pool = _postgres_pool()
        return _PgConnection(pool.getconn(), pool)
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 8000")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        if not IS_POSTGRES:
            conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            if IS_POSTGRES:
                conn.commit()
            else:
                conn.execute("COMMIT")
        except BaseException:
            if IS_POSTGRES:
                conn.rollback()
            else:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def fetch_all(sql: str, params: tuple | list = ()) -> list:
    conn = connect()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def fetch_one(sql: str, params: tuple | list = ()):
    conn = connect()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def scalar(sql: str, params: tuple | list = (), default=0):
    row = fetch_one(sql, params)
    if row is None:
        return default
    if isinstance(row, dict):
        value = next(iter(row.values()), default)
    else:
        value = row[0]
    return default if value is None else value


# ---------------------------------------------------------------- settings
_settings_lock = threading.Lock()
_settings_cache: dict[str, str] = {}
_settings_loaded_at = 0.0


def get_settings(force: bool = False) -> dict[str, str]:
    global _settings_loaded_at
    with _settings_lock:
        fresh = _settings_cache and (time.time() - _settings_loaded_at) < 5
        if fresh and not force:
            return dict(_settings_cache)
    data = dict(DEFAULT_SETTINGS)
    try:
        for row in fetch_all("SELECT key, value FROM settings"):
            data[row["key"]] = row["value"]
    except sqlite3.OperationalError:
        pass
    with _settings_lock:
        _settings_cache.clear()
        _settings_cache.update(data)
        _settings_loaded_at = time.time()
    return dict(data)


def save_settings(values: dict[str, str]) -> None:
    with tx() as conn:
        for key, value in values.items():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
    get_settings(force=True)


def next_seq(conn, name: str) -> int:
    conn.execute("INSERT INTO sequences(name, value) VALUES(?, 0) ON CONFLICT(name) DO NOTHING", (name,))
    conn.execute("UPDATE sequences SET value = value + 1 WHERE name = ?", (name,))
    row = conn.execute("SELECT value FROM sequences WHERE name = ?", (name,)).fetchone()
    return row["value"] if isinstance(row, dict) else row[0]


# ---------------------------------------------------------------- money
def to_cents(value) -> int:
    """Parse user input ('1,250.50') into integer cents."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    text = str(value).strip().replace(",", "").replace(" ", "")
    if not text:
        return 0
    try:
        amount = Decimal(text)
    except Exception:
        return 0
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fmt(cents: int | None) -> str:
    return f"{(cents or 0) / 100:,.2f}"


def pct_of(cents: int, percent: float) -> int:
    return int((Decimal(cents) * Decimal(str(percent)) / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------- bootstrap
def init_db(seed: bool = True) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        if IS_POSTGRES:
            conn.executescript(SCHEMA_PG)
            conn.commit()
        else:
            journal = os.environ.get("MX_SQLITE_JOURNAL", "wal").lower()
            if journal not in ("wal", "delete", "truncate", "persist", "memory", "off"):
                journal = "wal"
            conn.execute(f"PRAGMA journal_mode = {journal.upper()}")
            # WAL is unsafe on network shares (e.g. Azure Files), hence the override.
            conn.execute("PRAGMA synchronous = NORMAL" if journal == "wal" else "PRAGMA synchronous = FULL")
            conn.executescript(SCHEMA)
    finally:
        conn.close()
    with tx() as c:
        for key, value in DEFAULT_SETTINGS.items():
            c.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO NOTHING", (key, value))
    if seed:
        seed_products()
    get_settings(force=True)


def seed_products() -> None:
    if scalar("SELECT COUNT(*) FROM products") > 0:
        return
    if get_settings().get("business_initialised") == "1" and scalar("SELECT COUNT(*) FROM users") > 0:
        return
    stamp = now_str()
    with tx() as conn:
        for sku, name, category, brand, compatible, cost, price, qty, reorder in DEMO_PRODUCTS:
            search_text = " ".join([sku, name, category, brand, compatible]).lower()
            cur = conn.execute(
                "INSERT INTO products(sku, name, category, brand, compatible, description, cost_cents, "
                "price_cents, stock_qty, reorder_level, search_text, created_at, updated_at) "
                "VALUES(?,?,?,?,?,'',?,?,?,?,?,?,?)",
                (sku, name, category, brand, compatible, cost, price, qty, reorder, search_text, stamp, stamp),
            )
            conn.execute(
                "INSERT INTO stock_movements(product_id, kind, qty_delta, unit_cost_cents, stock_after, "
                "reference, note, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (cur.lastrowid, "initial", qty, cost, qty, "OPENING", "Opening stock", stamp),
            )
