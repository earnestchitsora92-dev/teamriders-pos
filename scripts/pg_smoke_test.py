"""End-to-end smoke test of the service layer against whichever backend DATABASE_URL selects.

Run against Postgres:
    DATABASE_URL=postgresql://postgres@127.0.0.1:55432/teamriders_test?sslmode=disable python scripts/pg_smoke_test.py
"""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, security, services  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def make_user(username: str, role: str, password: str = "Passw0rd!23") -> dict:
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO users(username, full_name, role, password_hash, is_active, created_at) VALUES(?,?,?,?,1,?)",
            (username, username.title(), role, security.hash_password(password), db.now_str()),
        )
        uid = int(cur.lastrowid)
    return db.fetch_one("SELECT * FROM users WHERE id = ?", (uid,))


def main() -> int:
    db.init_db()
    print("backend:", "postgres" if db.IS_POSTGRES else "sqlite")

    check("settings loaded", bool(db.get_settings().get("business_name")), db.get_settings().get("business_name", ""))
    check("seed products", db.scalar("SELECT COUNT(*) FROM products") > 0, str(db.scalar("SELECT COUNT(*) FROM products")))

    suffix = datetime.now().strftime("%H%M%S")
    admin = make_user("admin" + suffix, "admin")
    cashier = make_user("cash" + suffix, "cashier")

    check("authenticate admin", security.authenticate(admin["username"], "Passw0rd!23") is not None)
    check("authenticate rejects bad pw", security.authenticate(admin["username"], "wrong") is None)
    check("verify_admin_credentials", security.verify_admin_credentials(admin["username"], "Passw0rd!23") is not None)

    hits = services.search_products("brake", limit=5)
    check("search_products", len(hits) > 0, str(len(hits)))
    pid = hits[0]["id"]

    new_pid = services.save_product(
        {
            "sku": f"SMK-{suffix}",
            "name": f"Smoke Test Chain {suffix}",
            "category": "Chains",
            "bike_model": "CRF250",
            "price_cents": 45000,
            "cost_cents": 28000,
            "stock_qty": 0,
            "reorder_level": 2,
            "is_active": 1,
            "notes": "",
        },
        admin["id"],
    )
    check("create product", new_pid > 0, str(new_pid))

    services.save_product(
        {
            "sku": f"SMK-{suffix}",
            "name": f"Smoke Test Chain {suffix} (v2)",
            "category": "Chains",
            "bike_model": "CRF250",
            "price_cents": 46000,
            "cost_cents": 29000,
            "stock_qty": 0,
            "reorder_level": 2,
            "is_active": 1,
            "notes": "edited",
        },
        admin["id"],
        new_pid,
    )
    check("update product", "v2" in services.product(new_pid)["name"])

    services.restock(new_pid, 10, 30000, user_id=admin["id"], supplier="SmokeSupplier", note="smoke")
    prod = services.product(new_pid)
    check("restock qty", prod["stock_qty"] == 10, str(prod["stock_qty"]))
    check("moving-average cost", prod["cost_cents"] == 30000, str(prod["cost_cents"]))

    stock = services.stock_summary()
    check("stock_summary", stock["retail_value_cents"] > 0 and stock["skus"] > 0, str(stock))

    services.adjust_stock(new_pid, -1, "smoke shrink", admin["id"])
    check("adjust_stock", services.product(new_pid)["stock_qty"] == 9)

    pending = services.request_discount(
        cashier=cashier, kind="percent", value=10, subtotal_cents=46000, reason="smoke test request"
    )
    check("request_discount pending", pending["status"] == "pending", pending["code"])
    services.decide_discount(pending["id"], admin, True, "approved in smoke test")
    code = db.fetch_one("SELECT * FROM discounts WHERE id = ?", (pending["id"],))
    check("discount approved", code["status"] == "approved")

    denied = services.request_discount(
        cashier=cashier, kind="amount", value=5000, subtotal_cents=46000, reason="smoke deny"
    )
    services.decide_discount(denied["id"], admin, False, "no")
    check("discount denied", db.fetch_one("SELECT status FROM discounts WHERE id=?", (denied["id"],))["status"] == "denied")

    sale = services.create_sale(
        cashier,
        [{"product_id": new_pid, "qty": 2}],
        discount_code=pending["code"],
        customer_name="Smoke Customer",
        customer_phone="0770000000",
        payment_method="cash",
        tendered_cents=100000,
    )
    check("create_sale", sale["total_cents"] > 0, sale["receipt_no"])
    check("sale discount applied", sale["discount_cents"] == 4600, str(sale["discount_cents"]))
    check("stock after sale", services.product(new_pid)["stock_qty"] == 7, str(services.product(new_pid)["stock_qty"]))
    saved = db.fetch_one("SELECT profit_cents, cogs_cents, total_cents FROM sales WHERE id = ?", (sale["sale_id"],))
    check(
        "profit recorded",
        saved["profit_cents"] == sale["total_cents"] - saved["cogs_cents"],
        f"profit={saved['profit_cents']} cogs={saved['cogs_cents']}",
    )

    detail, items = services.sale_detail(sale["sale_id"])
    check("sale_detail items", detail is not None and len(items) == 1, str(len(items)))

    quote = services.create_quotation(
        cashier, [{"product_id": new_pid, "qty": 3}], customer_name="Smoke Quote", note="smoke"
    )
    check("create_quotation", quote["quotation_id"] > 0, quote["quote_no"])
    qdetail, qitems = services.quotation_detail(quote["quotation_id"])
    check("quotation_detail items", qdetail is not None and len(qitems) == 1)

    services.add_cash("expense", 20000, "cash", "smoke restock spend", admin["id"], reference="SMOKE")
    bal = services.cash_balance()
    check("cash_balance", "balance_cents" in bal, str(bal))

    today = datetime.now().strftime("%Y-%m-%d")
    month = datetime.now().strftime("%Y-%m")
    summary = services.period_summary(month)
    check("period_summary", summary["sales_count"] > 0, f"{summary['sales_count']} sales")

    perf = services.product_performance(today, "2999-12-31", limit=5)
    check("product_performance", isinstance(perf, list) and len(perf) > 0, str(len(perf)))

    cats = services.category_performance(today, "2999-12-31")
    check("category_performance", isinstance(cats, list) and len(cats) > 0, str(len(cats)))

    sales = services.sales_between(today, "2999-12-31")
    check("sales_between", len(sales) > 0, str(len(sales)))

    dstats = services.discount_stats(today, "2999-12-31")
    check("discount_stats", dstats["count"] >= 1, str(dstats))

    dash = services.dashboard_stats()
    check("dashboard_stats", dash["stock"]["skus"] > 0, str(dash["stock"]["skus"]))

    alert = services.add_stock_alert(new_pid, "smoke@example.com", customer_name="Smoke Alert")
    check("add_stock_alert", int(alert) > 0, str(alert))

    try:
        services.void_sale(sale["sale_id"], admin, "smoke test void")
        voided = db.fetch_one("SELECT status FROM sales WHERE id=?", (sale["sale_id"],))
        check("void_sale", voided["status"] == "voided", voided["status"])
        check("stock restored", services.product(new_pid)["stock_qty"] == 9, str(services.product(new_pid)["stock_qty"]))
    except Exception as exc:  # noqa: BLE001
        check("void_sale", False, repr(exc))

    # ---- stock requests (breakage / return) with admin approval
    req = services.request_stock_change(
        product_id=new_pid, kind="breakage", qty=2, reason="Dropped during fitting", user=cashier
    )
    check("stock request pending", req["id"] > 0 and req["step"] == -1, str(req["id"]))
    pending_count = services.pending_stock_requests()
    check("pending stock requests counted", pending_count >= 1, str(pending_count))
    result = services.decide_stock_change(req["id"], admin, True, "Approved, write it off")
    check("breakage applied", result["stock_qty"] == 7, str(result["stock_qty"]))
    check("breakage ledger entry", db.scalar(
        "SELECT COUNT(*) FROM stock_movements WHERE kind='breakage' AND product_id=?", (new_pid,)) == 1)

    ret = services.request_stock_change(
        product_id=new_pid, kind="return", qty=1, reason="Customer returned wrong size", user=cashier,
        refund_cents=46000,
    )
    services.decide_stock_change(ret["id"], admin, True, "Refunded at the counter")
    check("return restocked", services.product(new_pid)["stock_qty"] == 8, str(services.product(new_pid)["stock_qty"]))
    check("return refund left the cash", db.scalar(
        "SELECT COUNT(*) FROM cash_transactions WHERE kind='customer_refund' AND amount_cents < 0") == 1)

    refused = services.request_stock_change(
        product_id=new_pid, kind="correction", qty=1, reason="count check", user=cashier, direction=-1
    )
    services.decide_stock_change(refused["id"], admin, False, "count was right")
    check("denied request leaves stock", services.product(new_pid)["stock_qty"] == 8)

    # ---- salaries
    sal = services.pay_salary(
        user_id=cashier["id"], amount_cents=250000, period=db.current_period(), method="cash",
        note="smoke salary", admin_id=admin["id"],
    )
    check("salary paid", sal["amount_cents"] == 250000 and sal["reference"].startswith("SAL-"), sal["reference"])
    check("salary hit the cash ledger", db.scalar(
        "SELECT COUNT(*) FROM cash_transactions WHERE kind='salary' AND amount_cents = -250000") == 1)
    summary = services.salary_summary(db.current_period())
    check("salary summary", summary["total_cents"] >= 250000, str(summary["total_cents"]))
    check("salary history for user", len(services.salary_history_for(cashier["id"])) >= 1)

    # ---- cashier sees no business performance
    cstats = services.cashier_stats(cashier["id"])
    check(
        "cashier stats carry no money figures",
        not any(k in cstats for k in ("revenue", "profit", "cash", "stock_value", "today", "month")),
        str(sorted(cstats.keys())),
    )

    # ---- product delete / archive
    spare = services.save_product(
        {
            "sku": f"DEL-{suffix}", "name": f"Delete Me {suffix}", "category": "General", "bike_model": "",
            "price_cents": 1000, "cost_cents": 500, "stock_qty": 0, "reorder_level": 1, "is_active": 1, "notes": "",
        },
        admin["id"],
    )
    check("product hard delete", services.delete_product(spare) == "deleted")
    check("product gone", services.product(spare) is None)
    check("product with history is archived", services.delete_product(new_pid) == "archived")

    # ---- user delete
    temp_user = make_user("temp" + suffix, "cashier")
    check("unused user deleted", services.delete_user(temp_user["id"], admin) == "deleted")
    check("user row gone", db.fetch_one("SELECT * FROM users WHERE id = ?", (temp_user["id"],)) is None)
    used_user = make_user("used" + suffix, "cashier")
    services.create_sale(used_user, [{"product_id": 22, "qty": 1}] if services.product(22) else [{"product_id": new_pid, "qty": 1}],
                         payment_method="cash", tendered_cents=1000000)
    check("user with history anonymised", services.delete_user(used_user["id"], admin) == "anonymised")
    kept = db.fetch_one("SELECT * FROM users WHERE id = ?", (used_user["id"],))
    check("anonymised user cannot sign in", kept is not None and kept["is_deleted"] == 1 and kept["is_active"] == 0)
    check("anonymised user hidden from team", all(u["id"] != used_user["id"] for u in services.team_members()))

    closing = services.close_period(month, admin, "smoke closing")
    check("close_period", closing > 0, str(closing))

    check("audit log readable", db.scalar("SELECT COUNT(*) FROM audit_log") >= 0)
    check("returns int ids", isinstance(new_pid, int))

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if failed:
        for name, _, detail in failed:
            print("  failed:", name, detail)
        return 1
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        traceback.print_exc()
        code = 2
    finally:
        db._close_postgres_pool()
    sys.exit(code)


