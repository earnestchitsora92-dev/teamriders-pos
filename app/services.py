"""Business rules: catalogue, stock ledger, discounts, sales, cash, closings."""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta

from app import db, security

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LOW_STOCK_SQL = "stock_qty <= reorder_level"
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# --------------------------------------------------------------- catalogue
def product(product_id: int):
    return db.fetch_one("SELECT * FROM products WHERE id = ?", (product_id,))


def search_products(term: str, limit: int = 8) -> list[dict]:
    term = (term or "").strip().lower()
    if not term:
        rows = db.fetch_all(
            "SELECT * FROM products WHERE is_active = 1 ORDER BY (stock_qty > 0) DESC, name LIMIT ?", (limit,)
        )
    else:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        prefix = f"{escaped}%"
        rows = db.fetch_all(
            "SELECT * FROM products WHERE is_active = 1 AND (search_text LIKE ? ESCAPE '\\' OR sku LIKE ? ESCAPE '\\') "
            "ORDER BY (lower(sku) = ?) DESC, (sku LIKE ? ESCAPE '\\') DESC, (stock_qty > 0) DESC, name LIMIT ?",
            (like, like, term, prefix, limit),
        )
    return [
        {
            "id": r["id"],
            "sku": r["sku"],
            "name": r["name"],
            "category": r["category"],
            "brand": r["brand"],
            "compatible": r["compatible"],
            "price": r["price_cents"] / 100,
            "price_cents": r["price_cents"],
            "stock": r["stock_qty"],
            "reorder_level": r["reorder_level"],
        }
        for r in rows
    ]


def save_product(data: dict, user_id: int, product_id: int | None = None) -> int:
    sku = (data.get("sku") or "").strip().upper()
    name = (data.get("name") or "").strip()
    if not sku:
        raise ValueError("SKU is required")
    if not name:
        raise ValueError("Product name is required")
    category = (data.get("category") or "General").strip() or "General"
    brand = (data.get("brand") or "").strip()
    compatible = (data.get("compatible") or "").strip()
    description = (data.get("description") or "").strip()
    cost = max(0, int(data.get("cost_cents") or 0))
    price = max(0, int(data.get("price_cents") or 0))
    reorder = max(0, int(data.get("reorder_level") or 0))
    search_text = " ".join([sku, name, category, brand, compatible]).lower()
    stamp = db.now_str()

    with db.tx() as conn:
        if product_id:
            exists = conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
            if not exists:
                raise ValueError("Product not found")
            conn.execute(
                "UPDATE products SET sku=?, name=?, category=?, brand=?, compatible=?, description=?, "
                "cost_cents=?, price_cents=?, reorder_level=?, search_text=?, updated_at=? WHERE id=?",
                (sku, name, category, brand, compatible, description, cost, price, reorder, search_text, stamp, product_id),
            )
            return product_id

        initial_qty = max(0, int(data.get("initial_qty") or 0))
        try:
            cur = conn.execute(
                "INSERT INTO products(sku, name, category, brand, compatible, description, cost_cents, price_cents, "
                "stock_qty, reorder_level, search_text, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sku, name, category, brand, compatible, description, cost, price, initial_qty, reorder, search_text, stamp, stamp),
            )
        except db.IntegrityError:
            raise ValueError(f"SKU {sku} already exists") from None
        new_id = int(cur.lastrowid)
        if initial_qty:
            conn.execute(
                "INSERT INTO stock_movements(product_id, kind, qty_delta, unit_cost_cents, stock_after, reference, "
                "note, user_id, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (new_id, "initial", initial_qty, cost, initial_qty, "OPENING", "Opening stock", user_id, stamp),
            )
        return new_id


def set_product_active(product_id: int, active: bool) -> None:
    with db.tx() as conn:
        conn.execute("UPDATE products SET is_active = ?, updated_at = ? WHERE id = ?", (1 if active else 0, db.now_str(), product_id))


def delete_product(product_id: int) -> str:
    """Delete a part outright when it has no history; otherwise archive it so receipts stay readable."""
    row = product(product_id)
    if not row:
        raise ValueError("Product not found")
    history = db.scalar(
        "SELECT (SELECT COUNT(*) FROM sale_items WHERE product_id = ?) "
        "+ (SELECT COUNT(*) FROM quotation_items WHERE product_id = ?) "
        "+ (SELECT COUNT(*) FROM stock_alerts WHERE product_id = ?)",
        (product_id, product_id, product_id),
    )
    if history:
        set_product_active(product_id, False)
        return "archived"
    with db.tx() as conn:
        conn.execute("DELETE FROM stock_movements WHERE product_id = ?", (product_id,))
        conn.execute("DELETE FROM stock_requests WHERE product_id = ?", (product_id,))
        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    return "deleted"


# --------------------------------------------------------------- stock
def restock(product_id: int, qty: int, unit_cost_cents: int, *, user_id: int, supplier: str = "", note: str = "",
            pay_from_cash: bool = True, method: str = "cash") -> dict:
    if qty <= 0:
        raise ValueError("Quantity must be greater than zero")
    if unit_cost_cents < 0:
        raise ValueError("Unit cost cannot be negative")
    stamp = db.now_str()
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if not row:
            raise ValueError("Product not found")
        old_qty = row["stock_qty"]
        old_cost = row["cost_cents"]
        new_qty = old_qty + qty
        new_cost = unit_cost_cents if old_qty <= 0 else round((old_qty * old_cost + qty * unit_cost_cents) / new_qty)
        conn.execute(
            "UPDATE products SET stock_qty = ?, cost_cents = ?, updated_at = ? WHERE id = ?",
            (new_qty, new_cost, stamp, product_id),
        )
        conn.execute(
            "INSERT INTO stock_movements(product_id, kind, qty_delta, unit_cost_cents, stock_after, reference, note, "
            "user_id, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (product_id, "restock", qty, unit_cost_cents, new_qty, supplier.upper()[:24] or "RESTOCK", note, user_id, stamp),
        )
        total_cost = qty * unit_cost_cents
        if pay_from_cash and total_cost:
            conn.execute(
                "INSERT INTO cash_transactions(kind, amount_cents, method, reference, note, user_id, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                ("restock_payment", -total_cost, method, f"PO-{product_id}-{new_qty}", f"Restock: {row['name']} x{qty}", user_id, stamp),
            )
        waiting = conn.execute(
            "SELECT COUNT(*) FROM stock_alerts WHERE product_id = ? AND status = 'waiting'", (product_id,)
        ).fetchone()[0]
    return {"stock_qty": new_qty, "cost_cents": new_cost, "waiting_alerts": waiting if old_qty <= 0 else 0}


def adjust_stock(product_id: int, delta: int, reason: str, user_id: int) -> int:
    if delta == 0:
        raise ValueError("Adjustment cannot be zero")
    stamp = db.now_str()
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if not row:
            raise ValueError("Product not found")
        new_qty = row["stock_qty"] + delta
        if new_qty < 0:
            raise ValueError(f"Adjustment would make stock negative (current {row['stock_qty']})")
        conn.execute("UPDATE products SET stock_qty = ?, updated_at = ? WHERE id = ?", (new_qty, stamp, product_id))
        conn.execute(
            "INSERT INTO stock_movements(product_id, kind, qty_delta, unit_cost_cents, stock_after, reference, note, "
            "user_id, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (product_id, "adjustment", delta, row["cost_cents"], new_qty, "ADJUST", reason, user_id, stamp),
        )
        return new_qty


def stock_summary() -> dict:
    row = db.fetch_one(
        "SELECT COALESCE(SUM(stock_qty * cost_cents), 0) AS cost_value, "
        "COALESCE(SUM(stock_qty * price_cents), 0) AS retail_value, "
        "COALESCE(SUM(stock_qty), 0) AS units, COUNT(*) AS skus "
        "FROM products WHERE is_active = 1 AND stock_qty > 0"
    )
    low = db.scalar("SELECT COUNT(*) FROM products WHERE is_active = 1 AND stock_qty > 0 AND stock_qty <= reorder_level")
    out = db.scalar("SELECT COUNT(*) FROM products WHERE is_active = 1 AND stock_qty <= 0")
    return {
        "cost_value_cents": row["cost_value"],
        "retail_value_cents": row["retail_value"],
        "units": row["units"],
        "skus": row["skus"],
        "low_count": low,
        "out_count": out,
        "potential_margin_cents": row["retail_value"] - row["cost_value"],
    }


# --------------------------------------------------------------- discounts
def _calc_discount(subtotal_cents: int, kind: str, value: int) -> int:
    if subtotal_cents <= 0:
        return 0
    if kind == "percent":
        return min(subtotal_cents, db.pct_of(subtotal_cents, value))
    return min(subtotal_cents, max(0, value))


def request_discount(*, cashier, kind: str, value: int, subtotal_cents: int, reason: str, approved_by=None,
                     admin_note: str = "") -> dict:
    if kind not in ("percent", "amount"):
        raise ValueError("Discount type must be percentage or fixed amount")
    if kind == "percent" and (value <= 0 or value > 100):
        raise ValueError("Percentage must be between 1 and 100")
    if kind == "amount" and value <= 0:
        raise ValueError("Discount amount must be greater than zero")
    if not reason.strip():
        raise ValueError("A reason is required for every discount")
    if subtotal_cents <= 0:
        raise ValueError("Add items to the cart before applying a discount")

    settings = db.get_settings()
    max_percent = float(settings.get("max_discount_percent", "60") or 60)
    if kind == "percent" and value > max_percent:
        raise ValueError(f"Discounts above {max_percent:g}% are not permitted by business policy")

    amount = _calc_discount(subtotal_cents, kind, value)
    if amount <= 0:
        raise ValueError("Discount works out to zero")
    if amount > subtotal_cents:
        raise ValueError("Discount cannot exceed the sale subtotal")

    status = "approved" if approved_by else "pending"
    stamp = datetime.now()
    ttl = timedelta(minutes=30) if approved_by else timedelta(hours=24)
    code = "DSC-" + secrets.token_hex(3).upper()
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO discounts(code, requested_by, approved_by, status, kind, value, subtotal_cents, discount_cents, "
            "max_cents, reason, admin_note, created_at, expires_at, decided_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                code,
                cashier["id"],
                approved_by["id"] if approved_by else None,
                status,
                kind,
                value,
                subtotal_cents,
                amount,
                amount,
                reason.strip(),
                admin_note.strip(),
                stamp.strftime(db.TS_FORMAT),
                (stamp + ttl).strftime(db.TS_FORMAT),
                stamp.strftime(db.TS_FORMAT) if approved_by else None,
            ),
        )
        discount_id = int(cur.lastrowid)
    return {
        "id": discount_id,
        "code": code,
        "status": status,
        "discount_cents": amount,
        "kind": kind,
        "value": value,
        "expires_at": (stamp + ttl).strftime(db.TS_FORMAT),
    }


def decide_discount(discount_id: int, admin, approve: bool, note: str = "") -> None:
    stamp = db.now_str()
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM discounts WHERE id = ?", (discount_id,)).fetchone()
        if not row:
            raise ValueError("Discount not found")
        if row["status"] != "pending":
            raise ValueError("Only pending discount requests can be decided")
        if approve:
            conn.execute(
                "UPDATE discounts SET status='approved', approved_by=?, decided_at=?, admin_note=?, expires_at=? WHERE id=?",
                (admin["id"], stamp, note.strip(), (datetime.now() + timedelta(minutes=30)).strftime(db.TS_FORMAT), discount_id),
            )
        else:
            conn.execute(
                "UPDATE discounts SET status='denied', approved_by=?, decided_at=?, admin_note=? WHERE id=?",
                (admin["id"], stamp, note.strip(), discount_id),
            )


def expire_discounts() -> None:
    now = db.now_str()
    with db.tx() as conn:
        conn.execute(
            "UPDATE discounts SET status='expired' WHERE status IN ('pending','approved') AND expires_at < ?", (now,)
        )


def discount_stats(start: str, end: str) -> dict:
    row = db.fetch_one(
        "SELECT COUNT(*) AS count, COALESCE(SUM(discount_cents),0) AS total FROM sales "
        "WHERE status='completed' AND created_at >= ? AND created_at < ?",
        (start, end),
    )
    by_status = db.fetch_all(
        "SELECT status, COUNT(*) AS count, COALESCE(SUM(discount_cents),0) AS total FROM discounts "
        "WHERE created_at >= ? AND created_at < ? GROUP BY status",
        (start, end),
    )
    return {
        "count": row["count"],
        "total_cents": row["total"],
        "by_status": [dict(r) for r in by_status],
    }


# --------------------------------------------------------------- sales
def create_sale(user, items: list[dict], *, discount_code: str | None = None, customer_name: str = "",
                customer_phone: str = "", customer_email: str = "", payment_method: str = "cash",
                tendered_cents: int = 0, note: str = "") -> dict:
    cart: dict[int, int] = {}
    for item in items:
        try:
            pid = int(item.get("product_id") or 0)
            qty = int(item.get("qty") or 0)
        except (TypeError, ValueError):
            raise ValueError("Invalid cart item") from None
        if pid <= 0 or qty <= 0:
            raise ValueError("Invalid cart item")
        if qty > 999:
            raise ValueError("Quantity per item is limited to 999")
        cart[pid] = cart.get(pid, 0) + qty
    if not cart:
        raise ValueError("Cart is empty")

    stamp = datetime.now()
    stamp_str = stamp.strftime(db.TS_FORMAT)
    with db.tx() as conn:
        products = {}
        for pid, qty in cart.items():
            row = conn.execute("SELECT * FROM products WHERE id = ? AND is_active = 1", (pid,)).fetchone()
            if not row:
                raise ValueError("One of the items is no longer available")
            if row["stock_qty"] < qty:
                raise ValueError(f"Not enough stock for {row['name']} (only {row['stock_qty']} left)")
            products[pid] = row

        subtotal = sum(products[pid]["price_cents"] * qty for pid, qty in cart.items())
        discount_cents = 0
        discount_row = None
        if discount_code:
            discount_row = conn.execute("SELECT * FROM discounts WHERE code = ?", (discount_code.upper(),)).fetchone()
            if not discount_row:
                raise ValueError("Unknown discount code")
            if discount_row["status"] != "approved":
                raise ValueError(f"Discount {discount_code} is {discount_row['status']} and cannot be used")
            if discount_row["expires_at"] < stamp_str:
                conn.execute("UPDATE discounts SET status='expired' WHERE id=?", (discount_row["id"],))
                raise ValueError("This discount approval has expired")
            discount_cents = min(discount_row["max_cents"], subtotal)

        settings = db.get_settings()
        tax_rate = float(settings.get("tax_rate_percent", "0") or 0)
        taxable = max(0, subtotal - discount_cents)
        tax_cents = db.pct_of(taxable, tax_rate) if tax_rate > 0 else 0
        total_cents = taxable + tax_cents
        cogs = sum(products[pid]["cost_cents"] * qty for pid, qty in cart.items())
        profit = taxable - cogs

        tendered = tendered_cents if tendered_cents > 0 else total_cents
        if payment_method == "cash" and tendered < total_cents:
            raise ValueError("Amount tendered is less than the total")
        change = max(0, tendered - total_cents) if payment_method == "cash" else 0

        seq = db.next_seq(conn, f"receipt-{stamp:%Y%m}")
        receipt_no = f"RCP-{stamp:%Y%m}-{seq:04d}"

        cur = conn.execute(
            "INSERT INTO sales(receipt_no, cashier_id, customer_name, customer_phone, customer_email, subtotal_cents, "
            "discount_cents, discount_id, tax_cents, total_cents, cogs_cents, profit_cents, payment_method, "
            "tendered_cents, change_cents, note, status, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                receipt_no,
                user["id"],
                customer_name.strip(),
                customer_phone.strip(),
                customer_email.strip(),
                subtotal,
                discount_cents,
                discount_row["id"] if discount_row else None,
                tax_cents,
                total_cents,
                cogs,
                profit,
                payment_method,
                tendered,
                change,
                note.strip(),
                "completed",
                stamp_str,
            ),
        )
        sale_id = int(cur.lastrowid)

        for pid, qty in cart.items():
            row = products[pid]
            conn.execute(
                "INSERT INTO sale_items(sale_id, product_id, sku, name, qty, unit_price_cents, unit_cost_cents, "
                "line_total_cents) VALUES(?,?,?,?,?,?,?,?)",
                (sale_id, pid, row["sku"], row["name"], qty, row["price_cents"], row["cost_cents"], row["price_cents"] * qty),
            )
            new_stock = row["stock_qty"] - qty
            conn.execute("UPDATE products SET stock_qty = ?, updated_at = ? WHERE id = ?", (new_stock, stamp_str, pid))
            conn.execute(
                "INSERT INTO stock_movements(product_id, kind, qty_delta, unit_cost_cents, stock_after, reference, note, "
                "user_id, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (pid, "sale", -qty, row["cost_cents"], new_stock, receipt_no, "", user["id"], stamp_str),
            )

        if total_cents:
            conn.execute(
                "INSERT INTO cash_transactions(kind, amount_cents, method, reference, note, user_id, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                ("sale_income", total_cents, payment_method, receipt_no, customer_name.strip(), user["id"], stamp_str),
            )

        if discount_row:
            conn.execute(
                "UPDATE discounts SET status='used', sale_id=?, used_at=? WHERE id=?",
                (sale_id, stamp_str, discount_row["id"]),
            )

    return {
        "sale_id": sale_id,
        "receipt_no": receipt_no,
        "subtotal_cents": subtotal,
        "discount_cents": discount_cents,
        "tax_cents": tax_cents,
        "total_cents": total_cents,
        "change_cents": change,
        "payment_method": payment_method,
    }


def sale_detail(sale_id: int):
    sale = db.fetch_one(
        "SELECT s.*, u.full_name AS cashier_name, d.code AS discount_code, d.reason AS discount_reason, "
        "d.approved_by AS discount_approved_by, au.full_name AS discount_approved_by_name "
        "FROM sales s JOIN users u ON u.id = s.cashier_id "
        "LEFT JOIN discounts d ON d.id = s.discount_id LEFT JOIN users au ON au.id = d.approved_by WHERE s.id = ?",
        (sale_id,),
    )
    if not sale:
        return None, []
    items = db.fetch_all("SELECT * FROM sale_items WHERE sale_id = ? ORDER BY id", (sale_id,))
    return sale, items


def void_sale(sale_id: int, admin, reason: str) -> None:
    stamp = db.now_str()
    with db.tx() as conn:
        sale = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
        if not sale:
            raise ValueError("Sale not found")
        if sale["status"] != "completed":
            raise ValueError("Only completed sales can be voided")
        period = sale["created_at"][:7]
        if conn.execute("SELECT 1 FROM closings WHERE period = ?", (period,)).fetchone():
            raise ValueError(f"Period {period} is closed — this sale can no longer be voided")
        items = conn.execute("SELECT * FROM sale_items WHERE sale_id = ?", (sale_id,)).fetchall()
        for item in items:
            prod = conn.execute("SELECT stock_qty FROM products WHERE id = ?", (item["product_id"],)).fetchone()
            new_stock = prod["stock_qty"] + item["qty"]
            conn.execute("UPDATE products SET stock_qty = ?, updated_at = ? WHERE id = ?", (new_stock, stamp, item["product_id"]))
            conn.execute(
                "INSERT INTO stock_movements(product_id, kind, qty_delta, unit_cost_cents, stock_after, reference, note, "
                "user_id, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (item["product_id"], "void", item["qty"], item["unit_cost_cents"], new_stock, sale["receipt_no"],
                 f"Sale voided: {reason}"[:200], admin["id"], stamp),
            )
        conn.execute(
            "INSERT INTO cash_transactions(kind, amount_cents, method, reference, note, user_id, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("refund", -sale["total_cents"], sale["payment_method"], sale["receipt_no"], f"Refund — {reason}"[:200], admin["id"], stamp),
        )
        conn.execute(
            "UPDATE sales SET status='voided', voided_by=?, voided_at=?, void_reason=? WHERE id=?",
            (admin["id"], stamp, reason.strip(), sale_id),
        )


# --------------------------------------------------------------- quotations
def create_quotation(user, items: list[dict], *, discount_code: str | None = None, customer_name: str = "",
                     customer_email: str = "", customer_phone: str = "", note: str = "") -> dict:
    cart: dict[int, int] = {}
    for item in items:
        pid = int(item.get("product_id") or 0)
        qty = int(item.get("qty") or 0)
        if pid <= 0 or qty <= 0:
            raise ValueError("Invalid quote item")
        cart[pid] = cart.get(pid, 0) + qty
    if not cart:
        raise ValueError("Add at least one item to the quotation")
    if not customer_name.strip():
        raise ValueError("Customer name is required for a quotation")

    stamp = datetime.now()
    stamp_str = stamp.strftime(db.TS_FORMAT)
    settings = db.get_settings()
    with db.tx() as conn:
        products = {}
        for pid in cart:
            row = conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
            if not row:
                raise ValueError("One of the items is no longer available")
            products[pid] = row
        subtotal = sum(products[pid]["price_cents"] * qty for pid, qty in cart.items())
        discount_cents = 0
        if discount_code:
            disc = conn.execute("SELECT * FROM discounts WHERE code = ?", (discount_code.upper(),)).fetchone()
            if not disc or disc["status"] != "approved" or disc["expires_at"] < stamp_str:
                raise ValueError("Discount approval is not valid")
            discount_cents = min(disc["max_cents"], subtotal)
            conn.execute("UPDATE discounts SET status='used', used_at=? WHERE id=?", (stamp_str, disc["id"]))
        tax_rate = float(settings.get("tax_rate_percent", "0") or 0)
        taxable = max(0, subtotal - discount_cents)
        tax_cents = db.pct_of(taxable, tax_rate) if tax_rate else 0
        total_cents = taxable + tax_cents
        days = int(settings.get("quotation_valid_days", "14") or 14)
        valid_until = (stamp + timedelta(days=days)).date().isoformat()
        seq = db.next_seq(conn, f"quote-{stamp:%Y%m}")
        quote_no = f"QTN-{stamp:%Y%m}-{seq:04d}"
        cur = conn.execute(
            "INSERT INTO quotations(quote_no, created_by, customer_name, customer_email, customer_phone, subtotal_cents, "
            "discount_cents, tax_cents, total_cents, valid_until, status, note, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (quote_no, user["id"], customer_name.strip(), customer_email.strip(), customer_phone.strip(), subtotal,
             discount_cents, tax_cents, total_cents, valid_until, "sent", note.strip(), stamp_str),
        )
        quote_id = int(cur.lastrowid)
        for pid, qty in cart.items():
            row = products[pid]
            conn.execute(
                "INSERT INTO quotation_items(quotation_id, product_id, sku, name, qty, unit_price_cents, line_total_cents) "
                "VALUES(?,?,?,?,?,?,?)",
                (quote_id, pid, row["sku"], row["name"], qty, row["price_cents"], row["price_cents"] * qty),
            )
    return {"quotation_id": quote_id, "quote_no": quote_no, "total_cents": total_cents}


def quotation_detail(quote_id: int):
    quote = db.fetch_one(
        "SELECT q.*, u.full_name AS created_by_name FROM quotations q JOIN users u ON u.id = q.created_by WHERE q.id = ?",
        (quote_id,),
    )
    if not quote:
        return None, []
    items = db.fetch_all("SELECT * FROM quotation_items WHERE quotation_id = ? ORDER BY id", (quote_id,))
    return quote, items


# --------------------------------------------------------------- cash
CASH_KINDS = {
    "expense": -1,
    "owner_withdrawal": -1,
    "capital_injection": 1,
    "adjustment": 1,
    "refund": -1,
    "salary": -1,
    "customer_refund": -1,
}


def add_cash(kind: str, amount_cents: int, method: str, note: str, user_id: int, reference: str = "") -> int:
    if kind not in CASH_KINDS:
        raise ValueError("Unknown cash transaction type")
    if amount_cents <= 0:
        raise ValueError("Amount must be greater than zero")
    signed = amount_cents * CASH_KINDS[kind]
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO cash_transactions(kind, amount_cents, method, reference, note, user_id, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (kind, signed, method, reference.strip(), note.strip(), user_id, db.now_str()),
        )
        return int(cur.lastrowid)


def cash_balance() -> dict:
    row = db.fetch_one(
        "SELECT COALESCE(SUM(amount_cents),0) AS balance, "
        "COALESCE(SUM(CASE WHEN amount_cents > 0 THEN amount_cents ELSE 0 END),0) AS inflow, "
        "COALESCE(SUM(CASE WHEN amount_cents < 0 THEN -amount_cents ELSE 0 END),0) AS outflow "
        "FROM cash_transactions"
    )
    by_method = db.fetch_all(
        "SELECT method, COALESCE(SUM(amount_cents),0) AS balance FROM cash_transactions GROUP BY method ORDER BY method"
    )
    return {
        "balance_cents": row["balance"],
        "inflow_cents": row["inflow"],
        "outflow_cents": row["outflow"],
        "by_method": [dict(r) for r in by_method],
    }


# --------------------------------------------------------------- closing
def period_summary(period: str) -> dict:
    start, end = db.period_bounds(period)
    sales = db.fetch_one(
        "SELECT COUNT(*) AS count, COALESCE(SUM(total_cents),0) AS revenue, COALESCE(SUM(discount_cents),0) AS discounts, "
        "COALESCE(SUM(cogs_cents),0) AS cogs, COALESCE(SUM(profit_cents),0) AS gross, COALESCE(SUM(tax_cents),0) AS tax "
        "FROM sales WHERE status='completed' AND created_at >= ? AND created_at < ?",
        (start, end),
    )
    voided = db.fetch_one(
        "SELECT COUNT(*) AS count, COALESCE(SUM(total_cents),0) AS total FROM sales "
        "WHERE status='voided' AND created_at >= ? AND created_at < ?",
        (start, end),
    )
    expenses = db.scalar(
        "SELECT COALESCE(-SUM(amount_cents),0) FROM cash_transactions WHERE kind IN ('expense','salary') "
        "AND amount_cents < 0 AND created_at >= ? AND created_at < ?",
        (start, end),
    )
    salaries = db.scalar(
        "SELECT COALESCE(SUM(amount_cents),0) FROM payroll WHERE created_at >= ? AND created_at < ?", (start, end)
    )
    breakage = db.fetch_one(
        "SELECT COALESCE(SUM(-qty_delta),0) AS units, COALESCE(SUM(-qty_delta * unit_cost_cents),0) AS value "
        "FROM stock_movements WHERE kind='breakage' AND created_at >= ? AND created_at < ?",
        (start, end),
    )
    returns = db.fetch_one(
        "SELECT COALESCE(SUM(qty_delta),0) AS units, COALESCE(SUM(qty_delta * unit_cost_cents),0) AS value "
        "FROM stock_movements WHERE kind='return' AND created_at >= ? AND created_at < ?",
        (start, end),
    )
    cash = db.fetch_one(
        "SELECT COALESCE(SUM(CASE WHEN amount_cents > 0 THEN amount_cents ELSE 0 END),0) AS inflow, "
        "COALESCE(SUM(CASE WHEN amount_cents < 0 THEN -amount_cents ELSE 0 END),0) AS outflow "
        "FROM cash_transactions WHERE created_at >= ? AND created_at < ?",
        (start, end),
    )
    purchases = db.scalar(
        "SELECT COALESCE(SUM(qty_delta * unit_cost_cents),0) FROM stock_movements "
        "WHERE kind='restock' AND created_at >= ? AND created_at < ?",
        (start, end),
    )
    closing_value = db.scalar(
        "SELECT COALESCE(SUM(CASE WHEN qty_end > 0 THEN qty_end * cost_cents ELSE 0 END),0) FROM ("
        "  SELECT p.cost_cents AS cost_cents, p.stock_qty - COALESCE(("
        "    SELECT SUM(m.qty_delta) FROM stock_movements m WHERE m.product_id = p.id AND m.created_at >= ?"
        "  ), 0) AS qty_end FROM products p WHERE p.is_active = 1"
        ") AS stock_at_end",
        (end,),
    )
    opening_value = closing_value + sales["cogs"] - purchases
    gross_profit = sales["revenue"] - sales["cogs"]
    net_profit = gross_profit - expenses
    return {
        "period": period,
        "start": start,
        "end": end,
        "sales_count": sales["count"],
        "revenue_cents": sales["revenue"],
        "discounts_cents": sales["discounts"],
        "tax_cents": sales["tax"],
        "cogs_cents": sales["cogs"],
        "gross_profit_cents": gross_profit,
        "expenses_cents": expenses,
        "salaries_cents": salaries,
        "net_profit_cents": net_profit,
        "breakage_units": breakage["units"],
        "breakage_cents": breakage["value"],
        "return_units": returns["units"],
        "return_value_cents": returns["value"],
        "voided_count": voided["count"],
        "voided_cents": voided["total"],
        "purchases_cents": purchases,
        "opening_stock_value_cents": opening_value,
        "closing_stock_value_cents": closing_value,
        "cash_in_cents": cash["inflow"],
        "cash_out_cents": cash["outflow"],
        "cash_balance_cents": cash_balance()["balance_cents"],
        "margin_percent": round((gross_profit / sales["revenue"] * 100), 2) if sales["revenue"] else 0.0,
    }


def close_period(period: str, admin, notes: str = "") -> int:
    try:
        datetime.strptime(period, "%Y-%m")
    except ValueError:
        raise ValueError("Invalid period, expected YYYY-MM") from None
    if period > db.current_period():
        raise ValueError("You cannot close a month that has not started yet")
    if db.fetch_one("SELECT 1 FROM closings WHERE period = ?", (period,)):
        raise ValueError(f"{period} is already closed")
    summary = period_summary(period)
    start, end = summary["start"], summary["end"]
    top = product_performance(start, end, limit=10)
    snapshot = json.dumps(
        {
            "summary": summary,
            "top_products": [
                {
                    "sku": r["sku"],
                    "name": r["name"],
                    "qty": r["qty"],
                    "revenue_cents": r["revenue_cents"],
                    "profit_cents": r["profit_cents"],
                }
                for r in top
            ],
        }
    )
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO closings(period, opening_stock_value_cents, closing_stock_value_cents, purchases_cents, "
            "revenue_cents, discounts_cents, cogs_cents, gross_profit_cents, expenses_cents, net_profit_cents, "
            "sales_count, cash_in_cents, cash_out_cents, cash_balance_cents, closed_by, closed_at, notes, snapshot) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                period,
                summary["opening_stock_value_cents"],
                summary["closing_stock_value_cents"],
                summary["purchases_cents"],
                summary["revenue_cents"],
                summary["discounts_cents"],
                summary["cogs_cents"],
                summary["gross_profit_cents"],
                summary["expenses_cents"],
                summary["net_profit_cents"],
                summary["sales_count"],
                summary["cash_in_cents"],
                summary["cash_out_cents"],
                summary["cash_balance_cents"],
                admin["id"],
                db.now_str(),
                notes.strip(),
                snapshot,
            ),
        )
        return int(cur.lastrowid)


# --------------------------------------------------------------- analytics
def product_performance(start: str, end: str, limit: int = 20) -> list[dict]:
    rows = db.fetch_all(
        "SELECT * FROM ("
        "SELECT si.product_id, si.sku, si.name, SUM(si.qty) AS qty, "
        "COALESCE(SUM(si.line_total_cents * (1.0 - COALESCE(s.discount_cents * 1.0 / NULLIF(s.subtotal_cents,0), 0))),0) AS revenue, "
        "COALESCE(SUM(si.unit_cost_cents * si.qty),0) AS cogs "
        "FROM sale_items si JOIN sales s ON s.id = si.sale_id "
        "WHERE s.status='completed' AND s.created_at >= ? AND s.created_at < ? "
        "GROUP BY si.product_id, si.sku, si.name"
        ") AS perf ORDER BY (revenue - cogs) DESC LIMIT ?",
        (start, end, limit),
    )
    out = []
    for r in rows:
        revenue = int(round(r["revenue"]))
        cogs = int(round(r["cogs"]))
        out.append(
            {
                "product_id": r["product_id"],
                "sku": r["sku"],
                "name": r["name"],
                "qty": r["qty"],
                "revenue_cents": revenue,
                "cogs_cents": cogs,
                "profit_cents": revenue - cogs,
                "margin_percent": round((revenue - cogs) / revenue * 100, 1) if revenue else 0.0,
            }
        )
    return out


def category_performance(start: str, end: str) -> list[dict]:
    rows = db.fetch_all(
        "SELECT p.category AS category, SUM(si.qty) AS qty, "
        "COALESCE(SUM(si.line_total_cents * (1.0 - COALESCE(s.discount_cents * 1.0 / NULLIF(s.subtotal_cents,0), 0))),0) AS revenue, "
        "COALESCE(SUM(si.unit_cost_cents * si.qty),0) AS cogs "
        "FROM sale_items si JOIN sales s ON s.id = si.sale_id JOIN products p ON p.id = si.product_id "
        "WHERE s.status='completed' AND s.created_at >= ? AND s.created_at < ? GROUP BY p.category ORDER BY revenue DESC",
        (start, end),
    )
    out = []
    for r in rows:
        revenue = int(round(r["revenue"]))
        cogs = int(round(r["cogs"]))
        out.append(
            {
                "category": r["category"],
                "qty": r["qty"],
                "revenue_cents": revenue,
                "profit_cents": revenue - cogs,
                "margin_percent": round((revenue - cogs) / revenue * 100, 1) if revenue else 0.0,
            }
        )
    return out


def sales_between(start: str, end: str, limit: int = 500, cashier_id: int | None = None) -> list:
    if cashier_id:
        return db.fetch_all(
            "SELECT s.*, u.full_name AS cashier_name FROM sales s JOIN users u ON u.id = s.cashier_id "
            "WHERE s.created_at >= ? AND s.created_at < ? AND s.cashier_id = ? ORDER BY s.created_at DESC LIMIT ?",
            (start, end, cashier_id, limit),
        )
    return db.fetch_all(
        "SELECT s.*, u.full_name AS cashier_name FROM sales s JOIN users u ON u.id = s.cashier_id "
        "WHERE s.created_at >= ? AND s.created_at < ? ORDER BY s.created_at DESC LIMIT ?",
        (start, end, limit),
    )


def dashboard_stats() -> dict:
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    month_start, month_end = db.period_bounds(db.current_period())
    today_stats = db.fetch_one(
        "SELECT COUNT(*) AS count, COALESCE(SUM(total_cents),0) AS revenue, COALESCE(SUM(profit_cents),0) AS profit "
        "FROM sales WHERE status='completed' AND created_at >= ? AND created_at < ?",
        (today + " 00:00:00", tomorrow + " 00:00:00"),
    )
    month_stats = db.fetch_one(
        "SELECT COUNT(*) AS count, COALESCE(SUM(total_cents),0) AS revenue, COALESCE(SUM(profit_cents),0) AS profit, "
        "COALESCE(SUM(discount_cents),0) AS discounts FROM sales "
        "WHERE status='completed' AND created_at >= ? AND created_at < ?",
        (month_start, month_end),
    )
    top = product_performance(month_start, month_end, limit=5)
    low_stock = db.fetch_all(
        "SELECT id, sku, name, stock_qty, reorder_level FROM products WHERE is_active=1 AND stock_qty <= reorder_level "
        "ORDER BY (stock_qty * 1.0) / (reorder_level + 1), name LIMIT 8"
    )
    recent = db.fetch_all(
        "SELECT s.id, s.receipt_no, s.total_cents, s.status, s.created_at, s.customer_name, "
        "u.full_name AS cashier_name FROM sales s JOIN users u ON u.id = s.cashier_id ORDER BY s.id DESC LIMIT 8"
    )
    waiting_alerts = db.scalar("SELECT COUNT(*) FROM stock_alerts WHERE status='waiting'")
    pending_discounts = db.scalar("SELECT COUNT(*) FROM discounts WHERE status='pending'")
    return {
        "today": dict(today_stats),
        "month": dict(month_stats),
        "top_products": top,
        "low_stock": [dict(r) for r in low_stock],
        "recent_sales": [dict(r) for r in recent],
        "stock": stock_summary(),
        "cash": cash_balance(),
        "waiting_alerts": waiting_alerts,
        "pending_discounts": pending_discounts,
    }


# --------------------------------------------------------------- alerts
def add_stock_alert(product_id: int, email: str, *, customer_name: str = "", phone: str = "", note: str = "",
                    created_by: int | None = None) -> int:
    email = email.strip()
    if not EMAIL_RE.match(email):
        raise ValueError("Please enter a valid email address")
    product = db.fetch_one("SELECT id, name, stock_qty FROM products WHERE id = ?", (product_id,))
    if not product:
        raise ValueError("Product not found")
    with db.tx() as conn:
        existing = conn.execute(
            "SELECT id FROM stock_alerts WHERE product_id = ? AND lower(email) = ? AND status = 'waiting'",
            (product_id, email.lower()),
        ).fetchone()
        if existing:
            raise ValueError("This email is already on the waiting list for this item")
        cur = conn.execute(
            "INSERT INTO stock_alerts(product_id, email, customer_name, phone, status, note, created_by, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (product_id, email, customer_name.strip(), phone.strip(), "waiting", note.strip(), created_by, db.now_str()),
        )
        return int(cur.lastrowid)


def send_email(to: str, subject: str, body: str) -> tuple[bool, str]:
    import smtplib
    from email.message import EmailMessage

    settings = db.get_settings()
    host = settings.get("smtp_host", "").strip()
    if not host:
        return False, "Email is not configured. Add SMTP details in Settings."
    port_raw = settings.get("smtp_port", "587") or "587"
    try:
        port = int(port_raw)
    except ValueError:
        port = 587
    sender = settings.get("smtp_from", "").strip() or settings.get("smtp_user", "").strip() or settings.get("business_email", "")
    if not sender:
        return False, "No sender address configured."
    msg = EmailMessage()
    msg["From"] = f"{settings.get('business_name', 'Shop')} <{sender}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=12) as server:
            if settings.get("smtp_tls", "1") == "1":
                server.starttls()
            user = settings.get("smtp_user", "").strip()
            if user:
                server.login(user, settings.get("smtp_password", ""))
            server.send_message(msg)
        return True, "sent"
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        return False, f"{type(exc).__name__}: {exc}"


def notify_alert(alert_id: int) -> tuple[bool, str]:
    row = db.fetch_one(
        "SELECT a.*, p.name AS product_name, p.stock_qty, p.price_cents FROM stock_alerts a "
        "JOIN products p ON p.id = a.product_id WHERE a.id = ?",
        (alert_id,),
    )
    if not row:
        return False, "Alert not found"
    if row["status"] == "notified":
        return False, "Already notified"
    if row["stock_qty"] <= 0:
        return False, "Still out of stock — wait until the item is restocked"
    settings = db.get_settings()
    symbol = settings.get("currency", "$")
    body = (
        f"Hello {row['customer_name'] or 'there'},\n\n"
        f"Good news — {row['product_name']} is back in stock at {settings.get('business_name')}.\n"
        f"Price: {symbol}{row['price_cents'] / 100:,.2f}\n"
        f"Visit us: {settings.get('business_address')}\n"
        f"Call: {settings.get('business_phone')}\n\n"
        f"Stock is limited, so please come by soon.\n\n"
        f"{settings.get('receipt_footer')}"
    )
    ok, message = send_email(row["email"], f"{row['product_name']} is back in stock", body)
    if ok:
        with db.tx() as conn:
            conn.execute("UPDATE stock_alerts SET status='notified', notified_at=? WHERE id=?", (db.now_str(), alert_id))
    return ok, message

# --------------------------------------------------------------- people
def delete_user(user_id: int, admin) -> str:
    """Remove a user. Accounts with history are anonymised so receipts and payroll stay intact."""
    if user_id == admin["id"]:
        raise ValueError("You cannot delete your own account")
    row = db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not row or row["is_deleted"]:
        raise ValueError("User not found")
    if row["role"] == "admin":
        remaining = db.scalar(
            "SELECT COUNT(*) FROM users WHERE role='admin' AND is_active=1 AND is_deleted=0 AND id <> ?", (user_id,)
        )
        if remaining < 1:
            raise ValueError("At least one administrator must remain")
    references = db.scalar(
        "SELECT (SELECT COUNT(*) FROM sales WHERE cashier_id = ?) "
        "+ (SELECT COUNT(*) FROM quotations WHERE created_by = ?) "
        "+ (SELECT COUNT(*) FROM discounts WHERE requested_by = ? OR approved_by = ?) "
        "+ (SELECT COUNT(*) FROM payroll WHERE user_id = ?) "
        "+ (SELECT COUNT(*) FROM stock_requests WHERE requested_by = ? OR decided_by = ?) "
        "+ (SELECT COUNT(*) FROM cash_transactions WHERE user_id = ?) "
        "+ (SELECT COUNT(*) FROM stock_movements WHERE user_id = ?) "
        "+ (SELECT COUNT(*) FROM audit_log WHERE user_id = ?)",
        (user_id,) * 10,
    )
    with db.tx() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        if references == 0:
            conn.execute("UPDATE stock_alerts SET created_by = NULL WHERE created_by = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return "deleted"
        # Keep the row (and therefore every historical reference) but take the account out of service.
        conn.execute(
            "UPDATE users SET is_deleted = 1, is_active = 0, password_hash = ?, username = ? WHERE id = ?",
            (security.hash_password(secrets.token_urlsafe(32)), f"{row['username']}~deleted{user_id}", user_id),
        )
    return "anonymised"


def team_members(include_deleted: bool = False) -> list[dict]:
    clause = "" if include_deleted else "WHERE u.is_deleted = 0"
    rows = db.fetch_all(
        "SELECT u.*, (SELECT COUNT(*) FROM sales s WHERE s.cashier_id = u.id) AS sales_count FROM users u "
        f"{clause} ORDER BY u.role, u.username"
    )
    return [dict(r) for r in rows]


# --------------------------------------------------------------- payroll
SALARY_METHODS = ("cash", "bank", "mobile")


def pay_salary(*, user_id: int, amount_cents: int, period: str, method: str, note: str, admin_id: int) -> dict:
    if amount_cents <= 0:
        raise ValueError("Salary amount must be greater than zero")
    if not PERIOD_RE.match(period or ""):
        raise ValueError("Choose the month the salary is for")
    if method not in SALARY_METHODS:
        raise ValueError("Choose cash, bank or mobile money")
    target = db.fetch_one("SELECT * FROM users WHERE id = ? AND is_deleted = 0", (user_id,))
    if not target:
        raise ValueError("Choose a team member to pay")
    stamp = db.now_str()
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO payroll(user_id, period, amount_cents, method, reference, note, paid_by, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (user_id, period, amount_cents, method, "", note.strip(), admin_id, stamp),
        )
        payroll_id = int(cur.lastrowid)
        reference = f"SAL-{period.replace('-', '')}-{payroll_id:03d}"
        conn.execute("UPDATE payroll SET reference = ? WHERE id = ?", (reference, payroll_id))
        conn.execute(
            "INSERT INTO cash_transactions(kind, amount_cents, method, reference, note, user_id, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                "salary",
                -amount_cents,
                method,
                reference,
                f"Salary {period} - {target['full_name']}"[:200],
                admin_id,
                stamp,
            ),
        )
    return {"id": payroll_id, "reference": reference, "user": target["full_name"], "amount_cents": amount_cents}


def salary_payments(limit: int = 200) -> list[dict]:
    rows = db.fetch_all(
        "SELECT p.*, u.full_name AS user_name, u.username, u.role, a.full_name AS paid_by_name FROM payroll p "
        "JOIN users u ON u.id = p.user_id LEFT JOIN users a ON a.id = p.paid_by "
        "ORDER BY p.created_at DESC, p.id DESC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]


def salary_summary(period: str | None = None) -> dict:
    period = period or db.current_period()
    row = db.fetch_one(
        "SELECT COALESCE(SUM(amount_cents),0) AS total_cents, COUNT(*) AS payments FROM payroll WHERE period = ?",
        (period,),
    )
    by_person = db.fetch_all(
        "SELECT u.full_name AS user_name, u.role, COALESCE(SUM(p.amount_cents),0) AS total_cents, COUNT(*) AS payments "
        "FROM payroll p JOIN users u ON u.id = p.user_id WHERE p.period = ? GROUP BY u.id, u.full_name, u.role "
        "ORDER BY total_cents DESC",
        (period,),
    )
    return {
        "period": period,
        "total_cents": row["total_cents"],
        "payments": row["payments"],
        "by_person": [dict(r) for r in by_person],
    }


def salary_history_for(user_id: int, limit: int = 24) -> list[dict]:
    rows = db.fetch_all(
        "SELECT * FROM payroll WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?", (user_id, limit)
    )
    return [dict(r) for r in rows]


# --------------------------------------------------------------- stock requests
STOCK_REQUEST_KINDS = {
    "breakage": -1,
    "return": 1,
    "correction": 0,
}
STOCK_REQUEST_LABELS = {
    "breakage": "Breakage / write-off",
    "return": "Customer return",
    "correction": "Stock count correction",
}


def request_stock_change(*, product_id: int, kind: str, qty: int, reason: str, user, refund_cents: int = 0,
                         direction: int = 0) -> dict:
    if kind not in STOCK_REQUEST_KINDS:
        raise ValueError("Choose breakage, return or correction")
    if qty <= 0 or qty > 99999:
        raise ValueError("Quantity must be between 1 and 99,999")
    if not reason.strip():
        raise ValueError("A reason is required for every stock change")
    if refund_cents < 0:
        raise ValueError("Refund cannot be negative")
    if kind == "return" and refund_cents > 0 and not reason.strip():
        raise ValueError("A reason is required for every stock change")
    if kind != "return":
        refund_cents = 0
    row = product(product_id)
    if not row:
        raise ValueError("Choose a product")
    if kind == "breakage":
        step = -1
    elif kind == "return":
        step = 1
    else:
        step = 1 if direction >= 0 else -1
    if step < 0 and row["stock_qty"] < qty:
        raise ValueError(f"{row['name']} only has {row['stock_qty']} unit(s) on hand")
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO stock_requests(kind, product_id, qty, direction, unit_cost_cents, refund_cents, reason, "
            "status, requested_by, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (kind, product_id, qty, step, row["cost_cents"], refund_cents, reason.strip(), "pending", user["id"],
             db.now_str()),
        )
        return {"id": int(cur.lastrowid), "step": step}


def stock_requests_listing(status: str | None = "pending", user_id: int | None = None, limit: int = 100) -> list[dict]:
    clause = []
    params: list = []
    if status and status != "all":
        clause.append("r.status = ?")
        params.append(status)
    if user_id:
        clause.append("r.requested_by = ?")
        params.append(user_id)
    where = f"WHERE {' AND '.join(clause)}" if clause else ""
    rows = db.fetch_all(
        "SELECT r.*, p.name AS product_name, p.sku, p.stock_qty, u.full_name AS requested_by_name, "
        "a.full_name AS decided_by_name FROM stock_requests r JOIN products p ON p.id = r.product_id "
        "LEFT JOIN users u ON u.id = r.requested_by LEFT JOIN users a ON a.id = r.decided_by "
        f"{where} ORDER BY CASE WHEN r.status = 'pending' THEN 0 ELSE 1 END, r.created_at DESC, r.id DESC LIMIT ?",
        tuple(params + [limit]),
    )
    return [dict(r) for r in rows]


def pending_stock_requests() -> int:
    return db.scalar("SELECT COUNT(*) FROM stock_requests WHERE status = 'pending'")


def decide_stock_change(request_id: int, admin, approve: bool, note: str = "", refund_cents: int | None = None) -> dict:
    stamp = db.now_str()
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM stock_requests WHERE id = ?", (request_id,)).fetchone()
        if not row:
            raise ValueError("Request not found")
        if row["status"] != "pending":
            raise ValueError("Only pending requests can be decided")
        if not approve:
            conn.execute(
                "UPDATE stock_requests SET status='denied', decided_by=?, decided_at=?, admin_note=? WHERE id=?",
                (admin["id"], stamp, note.strip(), request_id),
            )
            return {"status": "denied", "stock_qty": None}
        prod = conn.execute("SELECT * FROM products WHERE id = ?", (row["product_id"],)).fetchone()
        if not prod:
            raise ValueError("The product no longer exists")
        delta = row["qty"] * row["direction"]
        new_qty = prod["stock_qty"] + delta
        if new_qty < 0:
            raise ValueError(f"That would take stock below zero ({prod['stock_qty']} on hand)")
        movement_kind = {"breakage": "breakage", "return": "return"}.get(row["kind"], "adjustment")
        reference = f"SR-{request_id:04d}"
        conn.execute(
            "INSERT INTO stock_movements(product_id, kind, qty_delta, unit_cost_cents, stock_after, reference, note, "
            "user_id, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (row["product_id"], movement_kind, delta, row["unit_cost_cents"], new_qty, reference,
             row["reason"][:200], admin["id"], stamp),
        )
        conn.execute(
            "UPDATE products SET stock_qty = ?, updated_at = ? WHERE id = ?", (new_qty, stamp, row["product_id"])
        )
        refund = row["refund_cents"] if refund_cents is None else max(0, refund_cents)
        if row["kind"] != "return":
            refund = 0
        if refund:
            conn.execute(
                "INSERT INTO cash_transactions(kind, amount_cents, method, reference, note, user_id, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                ("customer_refund", -refund, "cash", reference, f"Customer return - {prod['name']}"[:200],
                 admin["id"], stamp),
            )
        conn.execute(
            "UPDATE stock_requests SET status='approved', decided_by=?, decided_at=?, admin_note=?, refund_cents=? "
            "WHERE id=?",
            (admin["id"], stamp, note.strip(), refund, request_id),
        )
        return {"status": "approved", "stock_qty": new_qty, "refund_cents": refund}


def stock_request_stats() -> dict:
    row = db.fetch_one(
        "SELECT COALESCE(SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),0) AS pending, "
        "COALESCE(SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END),0) AS approved, "
        "COALESCE(SUM(CASE WHEN status='denied' THEN 1 ELSE 0 END),0) AS denied FROM stock_requests"
    )
    return {"pending": row["pending"], "approved": row["approved"], "denied": row["denied"]}


# --------------------------------------------------------------- cashier view
def cashier_stats(user_id: int) -> dict:
    """Only what a cashier needs to serve customers — no revenue, profit, stock value or cash figures."""
    today = db.today_str()
    own_today = db.scalar(
        "SELECT COUNT(*) FROM sales WHERE cashier_id = ? AND status='completed' AND created_at >= ?",
        (user_id, today + " 00:00:00"),
    )
    recent = db.fetch_all(
        "SELECT s.* FROM sales s WHERE s.cashier_id = ? ORDER BY s.created_at DESC, s.id DESC LIMIT 8", (user_id,)
    )
    discounts = db.fetch_all(
        "SELECT d.*, a.full_name AS approved_by_name FROM discounts d LEFT JOIN users a ON a.id = d.approved_by "
        "WHERE d.requested_by = ? ORDER BY d.created_at DESC, d.id DESC LIMIT 6",
        (user_id,),
    )
    requests = db.fetch_all(
        "SELECT r.*, p.name AS product_name, p.sku FROM stock_requests r JOIN products p ON p.id = r.product_id "
        "WHERE r.requested_by = ? ORDER BY r.created_at DESC, r.id DESC LIMIT 6",
        (user_id,),
    )
    return {
        "my_sales_today": own_today,
        "recent_sales": [dict(r) for r in recent],
        "my_discounts": [dict(r) for r in discounts],
        "my_stock_requests": [dict(r) for r in requests],
    }
