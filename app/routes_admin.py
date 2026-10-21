"""Admin routes: catalogue, stock, alerts, cash, closings, reports, settings, users."""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import Response

from app import db, security, services
from app.deps import (
    NotFoundError,
    audit,
    redirect,
    render,
    require_admin,
    require_user,
    verify_csrf,
)

router = APIRouter()


def _page(request: Request) -> int:
    try:
        return max(1, int(request.query_params.get("page") or 1))
    except ValueError:
        return 1


# ----------------------------------------------------------------- products
@router.get("/products")
def products_page(request: Request, user=Depends(require_admin)):
    q = (request.query_params.get("q") or "").strip()
    category = request.query_params.get("category") or ""
    stock_filter = request.query_params.get("stock") or ""
    archived = request.query_params.get("archived") == "1" and user["role"] == "admin"
    page = _page(request)
    per_page = 20
    where = ["is_active = ?"]
    params: list = [0 if archived else 1]
    if q:
        like = f"%{q.lower()}%"
        where.append("(search_text LIKE ? OR sku LIKE ?)")
        params.extend([like, like.upper()])
    if category:
        where.append("category = ?")
        params.append(category)
    if stock_filter == "low":
        where.append("stock_qty > 0 AND stock_qty <= reorder_level")
    elif stock_filter == "out":
        where.append("stock_qty <= 0")
    clause = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) FROM products WHERE {clause}", tuple(params))
    rows = db.fetch_all(
        f"SELECT * FROM products WHERE {clause} ORDER BY name LIMIT ? OFFSET ?",
        tuple(params) + (per_page, (page - 1) * per_page),
    )
    categories = [r["category"] for r in db.fetch_all("SELECT DISTINCT category FROM products ORDER BY category")]
    return render(
        request,
        "products.html",
        products=[dict(r) for r in rows],
        categories=categories,
        q=q,
        category=category,
        stock_filter=stock_filter,
        archived=archived,
        page=page,
        pages=max(1, (total + per_page - 1) // per_page),
        total=total,
        summary=services.stock_summary(),
    )


@router.get("/products/new")
def product_new(request: Request, admin=Depends(require_admin)):
    categories = [r["category"] for r in db.fetch_all("SELECT DISTINCT category FROM products ORDER BY category")]
    return render(request, "product_form.html", product=None, categories=categories)


@router.post("/products/new")
def product_create(
    request: Request,
    sku: str = Form(...),
    name: str = Form(...),
    category: str = Form("General"),
    brand: str = Form(""),
    compatible: str = Form(""),
    description: str = Form(""),
    cost: str = Form("0"),
    price: str = Form("0"),
    reorder_level: int = Form(3),
    initial_qty: int = Form(0),
    csrf: str = Form(""),
    admin=Depends(require_admin),
):
    verify_csrf(request, csrf)
    try:
        product_id = services.save_product(
            {
                "sku": sku, "name": name, "category": category, "brand": brand, "compatible": compatible,
                "description": description, "cost_cents": db.to_cents(cost), "price_cents": db.to_cents(price),
                "reorder_level": reorder_level, "initial_qty": initial_qty,
            },
            admin["id"],
        )
    except ValueError as exc:
        return redirect("/products/new", str(exc), "err")
    audit(request, "product_create", "product", product_id, f"{sku} {name}")
    return redirect(f"/products/{product_id}", "Product created")


@router.get("/products/{product_id}")
def product_detail(request: Request, product_id: int, user=Depends(require_admin)):
    product = services.product(product_id)
    if not product:
        raise NotFoundError()
    movements = db.fetch_all(
        "SELECT m.*, u.full_name AS user_name FROM stock_movements m LEFT JOIN users u ON u.id = m.user_id "
        "WHERE m.product_id = ? ORDER BY m.id DESC LIMIT 40",
        (product_id,),
    )
    sales = db.fetch_all(
        "SELECT s.id, s.receipt_no, s.created_at, s.status, si.qty, si.unit_price_cents, si.line_total_cents "
        "FROM sale_items si JOIN sales s ON s.id = si.sale_id WHERE si.product_id = ? ORDER BY s.id DESC LIMIT 15",
        (product_id,),
    )
    alerts = db.fetch_all(
        "SELECT * FROM stock_alerts WHERE product_id = ? AND status = 'waiting' ORDER BY id DESC", (product_id,)
    )
    return render(
        request,
        "product_detail.html",
        product=dict(product),
        movements=[dict(r) for r in movements],
        sales=[dict(r) for r in sales],
        alerts=[dict(r) for r in alerts],
    )


@router.get("/products/{product_id}/edit")
def product_edit(request: Request, product_id: int, admin=Depends(require_admin)):
    product = services.product(product_id)
    if not product:
        raise NotFoundError()
    categories = [r["category"] for r in db.fetch_all("SELECT DISTINCT category FROM products ORDER BY category")]
    return render(request, "product_form.html", product=dict(product), categories=categories)


@router.post("/products/{product_id}/edit")
def product_update(
    request: Request,
    product_id: int,
    sku: str = Form(...),
    name: str = Form(...),
    category: str = Form("General"),
    brand: str = Form(""),
    compatible: str = Form(""),
    description: str = Form(""),
    cost: str = Form("0"),
    price: str = Form("0"),
    reorder_level: int = Form(3),
    csrf: str = Form(""),
    admin=Depends(require_admin),
):
    verify_csrf(request, csrf)
    try:
        services.save_product(
            {
                "sku": sku, "name": name, "category": category, "brand": brand, "compatible": compatible,
                "description": description, "cost_cents": db.to_cents(cost), "price_cents": db.to_cents(price),
                "reorder_level": reorder_level,
            },
            admin["id"],
            product_id=product_id,
        )
    except ValueError as exc:
        return redirect(f"/products/{product_id}/edit", str(exc), "err")
    audit(request, "product_update", "product", product_id, f"{sku} {name}")
    return redirect(f"/products/{product_id}", "Product updated")


@router.post("/products/{product_id}/toggle")
def product_toggle(request: Request, product_id: int, csrf: str = Form(""), admin=Depends(require_admin)):
    verify_csrf(request, csrf)
    product = services.product(product_id)
    if not product:
        raise NotFoundError()
    services.set_product_active(product_id, not product["is_active"])
    audit(request, "product_archive", "product", product_id, "archived" if product["is_active"] else "restored")
    return redirect("/products", "Product archived" if product["is_active"] else "Product restored")


# ----------------------------------------------------------------- stock
@router.get("/stock")
def stock_page(request: Request, admin=Depends(require_admin)):
    q = (request.query_params.get("q") or "").strip()
    stock_filter = request.query_params.get("stock") or ""
    where = ["is_active = 1"]
    params: list = []
    if q:
        like = f"%{q.lower()}%"
        where.append("(search_text LIKE ? OR sku LIKE ?)")
        params.extend([like, like.upper()])
    if stock_filter == "low":
        where.append("stock_qty > 0 AND stock_qty <= reorder_level")
    elif stock_filter == "out":
        where.append("stock_qty <= 0")
    clause = " AND ".join(where)
    rows = db.fetch_all(
        f"SELECT * FROM products WHERE {clause} ORDER BY (stock_qty <= reorder_level) DESC, name LIMIT 300",
        tuple(params),
    )
    return render(
        request,
        "stock.html",
        products=[dict(r) for r in rows],
        summary=services.stock_summary(),
        q=q,
        stock_filter=stock_filter,
    )


@router.get("/stock/movements")
def stock_movements(request: Request, admin=Depends(require_admin)):
    kind = request.query_params.get("kind") or ""
    page = _page(request)
    per_page = 40
    where, params = [], []
    if kind:
        where.append("m.kind = ?")
        params.append(kind)
    q = (request.query_params.get("q") or "").strip()
    if q:
        where.append("(p.name LIKE ? OR p.sku LIKE ? OR m.reference LIKE ?)")
        params.extend([f"%{q}%"] * 3)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = db.scalar(
        f"SELECT COUNT(*) FROM stock_movements m JOIN products p ON p.id = m.product_id{clause}", tuple(params)
    )
    rows = db.fetch_all(
        "SELECT m.*, p.name AS product_name, p.sku, u.full_name AS user_name FROM stock_movements m "
        "JOIN products p ON p.id = m.product_id LEFT JOIN users u ON u.id = m.user_id"
        f"{clause} ORDER BY m.id DESC LIMIT ? OFFSET ?",
        tuple(params) + (per_page, (page - 1) * per_page),
    )
    return render(
        request,
        "movements.html",
        rows=[dict(r) for r in rows],
        kind=kind,
        q=q,
        page=page,
        pages=max(1, (total + per_page - 1) // per_page),
        total=total,
    )


def _stock_products():
    return [dict(r) for r in db.fetch_all("SELECT id, sku, name, stock_qty, cost_cents FROM products WHERE is_active=1 ORDER BY name")]


@router.get("/stock/restock")
def restock_page(request: Request, admin=Depends(require_admin)):
    product_id = request.query_params.get("product_id")
    return render(
        request,
        "stock_form.html",
        products=_stock_products(),
        selected=int(product_id) if product_id and product_id.isdigit() else None,
        mode="restock",
    )


@router.post("/stock/restock")
def restock_submit(
    request: Request,
    background: BackgroundTasks,
    product_id: int = Form(...),
    qty: int = Form(...),
    unit_cost: str = Form("0"),
    supplier: str = Form(""),
    note: str = Form(""),
    pay_from_cash: str = Form(""),
    method: str = Form("cash"),
    csrf: str = Form(""),
    admin=Depends(require_admin),
):
    verify_csrf(request, csrf)
    try:
        result = services.restock(
            product_id,
            qty,
            db.to_cents(unit_cost),
            user_id=admin["id"],
            supplier=supplier,
            note=note,
            pay_from_cash=bool(pay_from_cash),
            method=method if method in ("cash", "bank", "mobile") else "cash",
        )
    except ValueError as exc:
        return redirect(f"/stock/restock?product_id={product_id}", str(exc), "err")
    audit(request, "restock", "product", product_id, f"qty={qty} cost={db.to_cents(unit_cost)} supplier={supplier}")
    message = f"Stock updated — {result['stock_qty']} units on hand."
    if result["waiting_alerts"]:
        alert_rows = db.fetch_all(
            "SELECT id FROM stock_alerts WHERE product_id=? AND status='waiting'", (product_id,)
        )
        if db.get_settings().get("smtp_host", "").strip():
            for row in alert_rows:
                background.add_task(services.notify_alert, row["id"])
            message += f" {len(alert_rows)} back-in-stock email(s) queued."
        else:
            message += f" {len(alert_rows)} customer(s) are waiting — notify them from Alerts."
    return redirect("/stock", message)


@router.get("/stock/adjust")
def adjust_page(request: Request, admin=Depends(require_admin)):
    product_id = request.query_params.get("product_id")
    return render(
        request,
        "stock_form.html",
        products=_stock_products(),
        selected=int(product_id) if product_id and product_id.isdigit() else None,
        mode="adjust",
    )


@router.post("/stock/adjust")
def adjust_submit(
    request: Request,
    product_id: int = Form(...),
    delta: int = Form(...),
    reason: str = Form(...),
    csrf: str = Form(""),
    admin=Depends(require_admin),
):
    verify_csrf(request, csrf)
    try:
        new_qty = services.adjust_stock(product_id, delta, reason, admin["id"])
    except ValueError as exc:
        return redirect(f"/stock/adjust?product_id={product_id}", str(exc), "err")
    audit(request, "stock_adjust", "product", product_id, f"delta={delta} reason={reason[:120]}")
    return redirect(f"/products/{product_id}", f"Stock adjusted — {new_qty} units on hand")


@router.post("/products/{product_id}/delete")
def product_delete(request: Request, product_id: int, csrf: str = Form(""), admin=Depends(require_admin)):
    verify_csrf(request, csrf)
    row = db.fetch_one("SELECT * FROM products WHERE id = ?", (product_id,))
    if not row:
        raise NotFoundError()
    try:
        outcome = services.delete_product(product_id)
    except ValueError as exc:
        return redirect(f"/products/{product_id}", str(exc), "err")
    audit(request, "product_delete", "product", product_id, outcome)
    if outcome == "deleted":
        return redirect("/products", f"{row['name']} deleted permanently")
    return redirect("/products", f"{row['name']} has sales history, so it was archived instead of deleted")


# ------------------------------------------------------------- stock requests
@router.get("/stock/requests")
def stock_requests_page(request: Request, user=Depends(require_user)):
    status = request.query_params.get("status") or ("pending" if user["role"] == "admin" else "all")
    if status not in ("pending", "approved", "denied", "all"):
        status = "all"
    rows = services.stock_requests_listing(
        status, None if user["role"] == "admin" else user["id"]
    )
    products = db.fetch_all(
        "SELECT id, sku, name, stock_qty, reorder_level FROM products WHERE is_active = 1 ORDER BY name LIMIT 500"
    )
    return render(
        request,
        "stock_requests.html",
        rows=rows,
        status=status,
        products=[dict(p) for p in products],
        stats=services.stock_request_stats(),
        labels=services.STOCK_REQUEST_LABELS,
    )


@router.post("/stock/requests")
def stock_request_create(
    request: Request,
    product_id: int = Form(...),
    kind: str = Form(...),
    qty: int = Form(...),
    direction: str = Form("increase"),
    refund: str = Form("0"),
    reason: str = Form(...),
    csrf: str = Form(""),
    user=Depends(require_user),
):
    verify_csrf(request, csrf)
    try:
        result = services.request_stock_change(
            product_id=product_id,
            kind=kind,
            qty=qty,
            reason=reason,
            user=user,
            refund_cents=db.to_cents(refund),
            direction=-1 if direction == "decrease" else 1,
        )
    except ValueError as exc:
        return redirect("/stock/requests", str(exc), "err")
    audit(request, "stock_request", "stock_request", result["id"], f"{kind} {qty} product={product_id}")
    return redirect("/stock/requests", "Request sent to the administrator for approval")


@router.post("/stock/requests/{request_id}/decide")
def stock_request_decide(
    request: Request,
    request_id: int,
    decision: str = Form(...),
    note: str = Form(""),
    refund: str = Form(""),
    csrf: str = Form(""),
    admin=Depends(require_admin),
):
    verify_csrf(request, csrf)
    refund_cents = db.to_cents(refund) if refund.strip() else None
    try:
        result = services.decide_stock_change(
            request_id, admin, decision == "approve", note, refund_cents
        )
    except ValueError as exc:
        return redirect("/stock/requests?status=pending", str(exc), "err")
    audit(request, "stock_request_decision", "stock_request", request_id, decision)
    if result["status"] == "denied":
        return redirect("/stock/requests?status=pending", "Request refused")
    message = f"Approved - stock is now {result['stock_qty']} unit(s)"
    if result.get("refund_cents"):
        message += f", {db.fmt(result['refund_cents'])} refunded from the business cash"
    return redirect("/stock/requests?status=pending", message)


# ----------------------------------------------------------------- alerts
@router.get("/alerts")
def alerts_page(request: Request, admin=Depends(require_admin)):
    status = request.query_params.get("status") or "waiting"
    where = "" if status == "all" else "WHERE a.status = ?"
    params = () if status == "all" else (status,)
    rows = db.fetch_all(
        "SELECT a.*, p.name AS product_name, p.sku, p.stock_qty, p.price_cents FROM stock_alerts a "
        f"JOIN products p ON p.id = a.product_id {where} ORDER BY a.id DESC LIMIT 300",
        params,
    )
    counts = db.fetch_all("SELECT status, COUNT(*) AS c FROM stock_alerts GROUP BY status")
    return render(
        request,
        "alerts.html",
        rows=[dict(r) for r in rows],
        status=status,
        counts={r["status"]: r["c"] for r in counts},
    )


@router.post("/alerts/{alert_id}/notify")
def alert_notify(request: Request, alert_id: int, csrf: str = Form(""), admin=Depends(require_admin)):
    verify_csrf(request, csrf)
    ok, message = services.notify_alert(alert_id)
    audit(request, "alert_notify", "stock_alert", alert_id, message)
    return redirect("/alerts", message if ok else message, "ok" if ok else "err")


@router.post("/alerts/{alert_id}/cancel")
def alert_cancel(request: Request, alert_id: int, csrf: str = Form(""), admin=Depends(require_admin)):
    verify_csrf(request, csrf)
    with db.tx() as conn:
        conn.execute("UPDATE stock_alerts SET status='cancelled' WHERE id=?", (alert_id,))
    audit(request, "alert_cancel", "stock_alert", alert_id, "cancelled")
    return redirect("/alerts", "Alert cancelled")


@router.post("/alerts/notify-ready")
def alerts_notify_ready(request: Request, csrf: str = Form(""), admin=Depends(require_admin)):
    verify_csrf(request, csrf)
    rows = db.fetch_all(
        "SELECT a.id FROM stock_alerts a JOIN products p ON p.id = a.product_id "
        "WHERE a.status='waiting' AND p.stock_qty > 0 LIMIT 50"
    )
    sent = 0
    errors = 0
    for row in rows:
        ok, _ = services.notify_alert(row["id"])
        sent += 1 if ok else 0
        errors += 0 if ok else 1
    audit(request, "alert_notify_bulk", "", "", f"sent={sent} failed={errors}")
    if not rows:
        return redirect("/alerts", "No customers waiting on in-stock items", "err")
    return redirect("/alerts", f"{sent} notification(s) sent, {errors} failed")


# ----------------------------------------------------------------- cash
CASH_LABELS = {
    "sale_income": "Sale income",
    "restock_payment": "Stock purchase",
    "expense": "Operating expense",
    "owner_withdrawal": "Owner withdrawal",
    "capital_injection": "Capital injection",
    "refund": "Refund (voided sale)",
    "adjustment": "Adjustment",
    "salary": "Salary paid",
    "customer_refund": "Customer return refund",
    "breakage": "Breakage write-off",
}


@router.get("/cash")
def cash_page(request: Request, admin=Depends(require_admin)):
    balance = services.cash_balance()
    rows = db.fetch_all(
        "SELECT c.*, u.full_name AS user_name FROM cash_transactions c LEFT JOIN users u ON u.id = c.user_id "
        "ORDER BY c.id DESC LIMIT 200"
    )
    return render(request, "cash.html", balance=balance, rows=[dict(r) for r in rows], labels=CASH_LABELS)


@router.post("/cash/add")
def cash_add(
    request: Request,
    kind: str = Form(...),
    amount: str = Form(...),
    method: str = Form("cash"),
    note: str = Form(""),
    csrf: str = Form(""),
    admin=Depends(require_admin),
):
    verify_csrf(request, csrf)
    try:
        amount_cents = db.to_cents(amount)
        services.add_cash(kind, amount_cents, method, note, admin["id"])
    except ValueError as exc:
        return redirect("/cash", str(exc), "err")
    audit(request, "cash_entry", "cash", kind, f"{amount} {method} {note[:120]}")
    return redirect("/cash", "Cash entry recorded")


@router.post("/cash/{tx_id}/delete")
def cash_delete(request: Request, tx_id: int, csrf: str = Form(""), admin=Depends(require_admin)):
    verify_csrf(request, csrf)
    row = db.fetch_one("SELECT * FROM cash_transactions WHERE id = ?", (tx_id,))
    if not row:
        raise NotFoundError()
    if row["kind"] in ("sale_income", "restock_payment", "refund"):
        return redirect("/cash", "System-generated entries cannot be deleted", "err")
    with db.tx() as conn:
        conn.execute("DELETE FROM cash_transactions WHERE id = ?", (tx_id,))
    audit(request, "cash_delete", "cash", tx_id, f"{row['kind']} {row['amount_cents']}")
    return redirect("/cash", "Cash entry removed")


# ----------------------------------------------------------------- closings
@router.get("/closings")
def closings_page(request: Request, admin=Depends(require_admin)):
    closed = [dict(r) for r in db.fetch_all(
        "SELECT c.*, u.full_name AS closed_by_name FROM closings c JOIN users u ON u.id = c.closed_by ORDER BY c.period DESC"
    )]
    current = db.current_period()
    closed_periods = {r["period"] for r in closed}
    options = []
    for i in range(12):
        period = db.shift_period(current, -i)
        if period not in closed_periods:
            options.append(period)
    preview = None
    if options:
        preview = services.period_summary(options[0])
        preview["has_activity"] = preview["sales_count"] > 0 or preview["purchases_cents"] > 0
    return render(request, "closings.html", closed=closed, options=options, preview=preview)


@router.post("/closings/close")
def closing_close(
    request: Request,
    period: str = Form(...),
    notes: str = Form(""),
    csrf: str = Form(""),
    admin=Depends(require_admin),
):
    verify_csrf(request, csrf)
    try:
        closing_id = services.close_period(period, admin, notes)
    except ValueError as exc:
        return redirect("/closings", str(exc), "err")
    audit(request, "period_close", "closing", closing_id, f"period={period}")
    return redirect(f"/closings/{closing_id}", f"{period} closed successfully")


@router.get("/closings/{closing_id}")
def closing_detail(request: Request, closing_id: int, admin=Depends(require_admin)):
    import json

    row = db.fetch_one(
        "SELECT c.*, u.full_name AS closed_by_name FROM closings c JOIN users u ON u.id = c.closed_by WHERE c.id = ?",
        (closing_id,),
    )
    if not row:
        raise NotFoundError()
    data = dict(row)
    try:
        snapshot = json.loads(row["snapshot"])
    except Exception:
        snapshot = {}
    return render(request, "closing_detail.html", closing=data, snapshot=snapshot)


# ----------------------------------------------------------------- reports
def _range_summary(start: str, end: str) -> dict:
    sales = db.fetch_one(
        "SELECT COUNT(*) AS count, COALESCE(SUM(total_cents),0) AS revenue, COALESCE(SUM(discount_cents),0) AS discounts, "
        "COALESCE(SUM(cogs_cents),0) AS cogs, COALESCE(SUM(profit_cents),0) AS gross, COALESCE(SUM(tax_cents),0) AS tax "
        "FROM sales WHERE status='completed' AND created_at >= ? AND created_at < ?",
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
    return {
        "count": sales["count"],
        "revenue_cents": sales["revenue"],
        "discounts_cents": sales["discounts"],
        "cogs_cents": sales["cogs"],
        "gross_profit_cents": sales["revenue"] - sales["cogs"],
        "tax_cents": sales["tax"],
        "expenses_cents": expenses,
        "salaries_cents": salaries,
        "net_profit_cents": sales["revenue"] - sales["cogs"] - expenses,
        "breakage_units": breakage["units"],
        "breakage_cents": breakage["value"],
        "return_units": returns["units"],
        "return_value_cents": returns["value"],
        "cash_in_cents": cash["inflow"],
        "cash_out_cents": cash["outflow"],
        "purchases_cents": purchases,
        "margin_percent": round((sales["revenue"] - sales["cogs"]) / sales["revenue"] * 100, 1) if sales["revenue"] else 0.0,
        "average_sale_cents": int(sales["revenue"] / sales["count"]) if sales["count"] else 0,
    }


def _report_range(request: Request) -> tuple[str, str, str, str]:
    today = date.today()
    default_from = today.replace(day=1)
    frm = request.query_params.get("from") or default_from.isoformat()
    to = request.query_params.get("to") or today.isoformat()
    try:
        start_d = date.fromisoformat(frm)
        end_d = date.fromisoformat(to)
    except ValueError:
        start_d, end_d = default_from, today
    return start_d.isoformat(), end_d.isoformat(), start_d.isoformat() + " 00:00:00", (end_d + timedelta(days=1)).isoformat() + " 00:00:00"


@router.get("/reports")
def reports_page(request: Request, admin=Depends(require_admin)):
    frm, to, start, end = _report_range(request)
    summary = _range_summary(start, end)
    top = services.product_performance(start, end, limit=15)
    categories = services.category_performance(start, end)
    cash_by_kind = [
        dict(r)
        for r in db.fetch_all(
            "SELECT kind, COALESCE(SUM(amount_cents),0) AS total, COUNT(*) AS n FROM cash_transactions "
            "WHERE created_at >= ? AND created_at < ? GROUP BY kind ORDER BY total DESC",
            (start, end),
        )
    ]
    top_discounts = [
        dict(r)
        for r in db.fetch_all(
            "SELECT d.*, cu.full_name AS cashier_name, au.full_name AS approver_name, s.receipt_no FROM discounts d "
            "JOIN users cu ON cu.id = d.requested_by LEFT JOIN users au ON au.id = d.approved_by "
            "LEFT JOIN sales s ON s.id = d.sale_id "
            "WHERE d.status='used' AND d.created_at >= ? AND d.created_at < ? ORDER BY d.discount_cents DESC LIMIT 10",
            (start, end),
        )
    ]
    daily = [
        dict(r)
        for r in db.fetch_all(
            "SELECT substr(created_at,1,10) AS day, COUNT(*) AS count, COALESCE(SUM(total_cents),0) AS revenue, "
            "COALESCE(SUM(profit_cents),0) AS profit FROM sales WHERE status='completed' "
            "AND created_at >= ? AND created_at < ? GROUP BY day ORDER BY day",
            (start, end),
        )
    ]
    max_daily = max([d["revenue"] for d in daily], default=0)
    by_cashier = [
        dict(r)
        for r in db.fetch_all(
            "SELECT u.full_name AS cashier, COUNT(*) AS count, COALESCE(SUM(s.total_cents),0) AS revenue, "
            "COALESCE(SUM(s.profit_cents),0) AS profit, COALESCE(SUM(s.discount_cents),0) AS discounts "
            "FROM sales s JOIN users u ON u.id = s.cashier_id WHERE s.status='completed' "
            "AND s.created_at >= ? AND s.created_at < ? GROUP BY u.id, u.full_name ORDER BY revenue DESC",
            (start, end),
        )
    ]
    return render(
        request,
        "reports.html",
        frm=frm,
        to=to,
        summary=summary,
        top=top,
        categories=categories,
        cash_by_kind=cash_by_kind,
        top_discounts=top_discounts,
        daily=daily,
        max_daily=max_daily,
        by_cashier=by_cashier,
        stock=services.stock_summary(),
        labels=CASH_LABELS,
    )


@router.get("/reports/export/{kind}.csv")
def report_export(kind: str, request: Request, admin=Depends(require_admin)):
    frm, to, start, end = _report_range(request)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if kind == "sales":
        writer.writerow(["Date", "Receipt", "Cashier", "Customer", "Subtotal", "Discount", "Tax", "Total", "Cost", "Profit", "Status"])
        for r in services.sales_between(start, end, limit=5000):
            writer.writerow([
                r["created_at"], r["receipt_no"], r["cashier_name"], r["customer_name"],
                f"{r['subtotal_cents'] / 100:.2f}", f"{r['discount_cents'] / 100:.2f}", f"{r['tax_cents'] / 100:.2f}",
                f"{r['total_cents'] / 100:.2f}", f"{r['cogs_cents'] / 100:.2f}", f"{r['profit_cents'] / 100:.2f}", r["status"],
            ])
    elif kind == "products":
        writer.writerow(["Date", "SKU", "Product", "Qty sold", "Revenue", "Cost", "Profit", "Margin %"])
        for r in services.product_performance(start, end, limit=1000):
            writer.writerow([
                f"{frm} to {to}", r["sku"], r["name"], r["qty"], f"{r['revenue_cents'] / 100:.2f}",
                f"{r['cogs_cents'] / 100:.2f}", f"{r['profit_cents'] / 100:.2f}", r["margin_percent"],
            ])
    elif kind == "stock":
        writer.writerow(["SKU", "Product", "Category", "Stock", "Reorder level", "Unit cost", "Stock value", "Unit price", "Retail value"])
        for r in db.fetch_all("SELECT * FROM products WHERE is_active=1 ORDER BY name"):
            writer.writerow([
                r["sku"], r["name"], r["category"], r["stock_qty"], r["reorder_level"],
                f"{r['cost_cents'] / 100:.2f}", f"{r['stock_qty'] * r['cost_cents'] / 100:.2f}",
                f"{r['price_cents'] / 100:.2f}", f"{r['stock_qty'] * r['price_cents'] / 100:.2f}",
            ])
    elif kind == "discounts":
        writer.writerow(["Date", "Code", "Cashier", "Approved by", "Type", "Value", "Amount", "Status", "Receipt", "Reason"])
        for r in db.fetch_all(
            "SELECT d.*, cu.full_name AS cashier_name, au.full_name AS approver_name, s.receipt_no FROM discounts d "
            "JOIN users cu ON cu.id = d.requested_by LEFT JOIN users au ON au.id = d.approved_by "
            "LEFT JOIN sales s ON s.id = d.sale_id WHERE d.created_at >= ? AND d.created_at < ? ORDER BY d.id DESC",
            (start, end),
        ):
            value = f"{r['value'] / 100:.2f}" if r["kind"] == "amount" else str(r["value"])
            writer.writerow([
                r["created_at"], r["code"], r["cashier_name"], r["approver_name"] or "", r["kind"], value,
                f"{r['discount_cents'] / 100:.2f}", r["status"], r["receipt_no"] or "", r["reason"],
            ])
    else:
        raise NotFoundError()
    filename = f"{kind}-{frm}-to-{to}.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ----------------------------------------------------------------- settings
SETTING_FIELDS = [
    "business_name", "business_tagline", "business_address", "business_phone", "business_email", "currency",
    "tax_rate_percent", "tax_label", "max_discount_percent", "low_stock_threshold", "receipt_footer",
    "quotation_valid_days", "quotation_terms", "smtp_host", "smtp_port", "smtp_user", "smtp_from", "smtp_tls",
]


@router.get("/settings")
def settings_page(request: Request, admin=Depends(require_admin)):
    return render(request, "settings.html")


@router.post("/settings")
async def settings_save(request: Request, admin=Depends(require_admin)):
    form = await request.form()
    verify_csrf(request, form.get("csrf"))
    values: dict[str, str] = {}
    for field in SETTING_FIELDS:
        if field in form:
            values[field] = str(form.get(field) or "").strip()
    values["smtp_tls"] = "1" if form.get("smtp_tls") else "0"
    try:
        tax = float(values.get("tax_rate_percent") or 0)
        max_discount = float(values.get("max_discount_percent") or 0)
        valid_days = int(values.get("quotation_valid_days") or 14)
        low = int(values.get("low_stock_threshold") or 0)
    except ValueError:
        return redirect("/settings", "Numeric fields contain invalid values", "err")
    if not 0 <= tax <= 100:
        return redirect("/settings", "Tax rate must be between 0 and 100", "err")
    if not 0 <= max_discount <= 100:
        return redirect("/settings", "Maximum discount must be between 0 and 100", "err")
    if not 1 <= valid_days <= 365:
        return redirect("/settings", "Quotation validity must be between 1 and 365 days", "err")
    if low < 0:
        return redirect("/settings", "Low stock threshold cannot be negative", "err")
    if not values.get("currency"):
        values["currency"] = "$"
    if len(values["currency"]) > 6:
        return redirect("/settings", "Currency symbol is too long", "err")
    password = str(form.get("smtp_password") or "")
    if password:
        values["smtp_password"] = password
    db.save_settings(values)
    audit(request, "settings_update", "settings", "", ", ".join(values.keys()))
    return redirect("/settings", "Settings saved")


@router.post("/settings/test-email")
def settings_test_email(request: Request, to: str = Form(...), csrf: str = Form(""), admin=Depends(require_admin)):
    verify_csrf(request, csrf)
    if not services.EMAIL_RE.match(to.strip()):
        return redirect("/settings", "Enter a valid email address", "err")
    settings = db.get_settings()
    ok, message = services.send_email(
        to.strip(),
        f"{settings.get('business_name')} — test email",
        f"This is a test message from your {settings.get('business_name')} POS. Email notifications are working.",
    )
    audit(request, "settings_test_email", "settings", to, message)
    return redirect("/settings", "Test email sent" if ok else message, "ok" if ok else "err")


# ----------------------------------------------------------------- users
@router.get("/users")
def users_page(request: Request, admin=Depends(require_admin)):
    rows = db.fetch_all(
        "SELECT u.*, (SELECT COUNT(*) FROM sales s WHERE s.cashier_id = u.id) AS sales_count FROM users u WHERE u.is_deleted = 0 ORDER BY u.role, u.username"
    )
    return render(request, "users.html", users=[dict(r) for r in rows])


@router.post("/users/new")
def user_create(
    request: Request,
    full_name: str = Form(...),
    username: str = Form(...),
    role: str = Form(...),
    password: str = Form(...),
    csrf: str = Form(""),
    admin=Depends(require_admin),
):
    verify_csrf(request, csrf)
    username = username.strip()
    if role not in ("admin", "cashier"):
        return redirect("/users", "Invalid role", "err")
    if len(username) < 3:
        return redirect("/users", "Username must be at least 3 characters", "err")
    if len(password) < 6:
        return redirect("/users", "Password must be at least 6 characters", "err")
    try:
        with db.tx() as conn:
            cur = conn.execute(
                "INSERT INTO users(username, full_name, role, password_hash, created_at) VALUES(?,?,?,?,?)",
                (username, full_name.strip() or username, role, security.hash_password(password), db.now_str()),
            )
            new_id = int(cur.lastrowid)
    except Exception:
        return redirect("/users", f"Username {username} is already taken", "err")
    audit(request, "user_create", "user", new_id, f"{username} role={role}")
    return redirect("/users", f"User {username} created")


@router.post("/users/{user_id}/password")
def user_reset_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    csrf: str = Form(""),
    admin=Depends(require_admin),
):
    verify_csrf(request, csrf)
    if len(new_password) < 6:
        return redirect("/users", "Password must be at least 6 characters", "err")
    with db.tx() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (security.hash_password(new_password), user_id))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    audit(request, "user_password_reset", "user", user_id, "")
    return redirect("/users", "Password reset — user must sign in again")


@router.post("/users/{user_id}/toggle")
def user_toggle(request: Request, user_id: int, csrf: str = Form(""), admin=Depends(require_admin)):
    verify_csrf(request, csrf)
    if user_id == admin["id"]:
        return redirect("/users", "You cannot deactivate your own account", "err")
    row = db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not row:
        raise NotFoundError()
    if row["is_active"] and row["role"] == "admin":
        active_admins = db.scalar("SELECT COUNT(*) FROM users WHERE role='admin' AND is_active=1")
        if active_admins <= 1:
            return redirect("/users", "At least one active administrator is required", "err")
    with db.tx() as conn:
        conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (0 if row["is_active"] else 1, user_id))
        if row["is_active"]:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    audit(request, "user_toggle", "user", user_id, "deactivated" if row["is_active"] else "activated")
    return redirect("/users", f"{row['username']} {'deactivated' if row['is_active'] else 'activated'}")


@router.post("/users/{user_id}/delete")
def user_delete(request: Request, user_id: int, csrf: str = Form(""), admin=Depends(require_admin)):
    verify_csrf(request, csrf)
    try:
        outcome = services.delete_user(user_id, admin)
    except ValueError as exc:
        return redirect("/users", str(exc), "err")
    audit(request, "user_delete", "user", user_id, outcome)
    message = (
        "User deleted"
        if outcome == "deleted"
        else "User removed from the team; their past sales and salary records are kept for the books"
    )
    return redirect("/users", message)


# ----------------------------------------------------------------- salaries
@router.get("/salaries")
def salaries_page(request: Request, admin=Depends(require_admin)):
    period = request.query_params.get("period") or db.current_period()
    if not services.PERIOD_RE.match(period):
        period = db.current_period()
    team = services.team_members()
    return render(
        request,
        "salaries.html",
        period=period,
        team=team,
        payments=services.salary_payments(),
        summary=services.salary_summary(period),
        balance=services.cash_balance(),
        methods=services.SALARY_METHODS,
    )


@router.post("/salaries/pay")
def salary_pay(
    request: Request,
    user_id: int = Form(...),
    amount: str = Form(...),
    period: str = Form(...),
    method: str = Form("cash"),
    note: str = Form(""),
    csrf: str = Form(""),
    admin=Depends(require_admin),
):
    verify_csrf(request, csrf)
    try:
        result = services.pay_salary(
            user_id=user_id,
            amount_cents=db.to_cents(amount),
            period=period,
            method=method,
            note=note,
            admin_id=admin["id"],
        )
    except ValueError as exc:
        return redirect(f"/salaries?period={period}", str(exc), "err")
    audit(request, "salary_paid", "payroll", result["id"], f"{result['user']} {result['amount_cents']} {method}")
    return redirect(
        f"/salaries?period={period}",
        f"Paid {db.fmt(result['amount_cents'])} to {result['user']} ({result['reference']}) - cash balance updated",
    )


# ----------------------------------------------------------------- audit
@router.get("/audit")
def audit_page(request: Request, admin=Depends(require_admin)):
    action = request.query_params.get("action") or ""
    page = _page(request)
    per_page = 50
    where, params = "", []
    if action:
        where = " WHERE a.action LIKE ?"
        params.append(f"{action}%")
    total = db.scalar(f"SELECT COUNT(*) FROM audit_log a{where}", tuple(params))
    rows = db.fetch_all(
        "SELECT a.*, u.full_name AS user_name, u.role FROM audit_log a LEFT JOIN users u ON u.id = a.user_id"
        f"{where} ORDER BY a.id DESC LIMIT ? OFFSET ?",
        tuple(params) + (per_page, (page - 1) * per_page),
    )
    actions = [r["action"] for r in db.fetch_all("SELECT DISTINCT action FROM audit_log ORDER BY action")]
    return render(
        request,
        "audit.html",
        rows=[dict(r) for r in rows],
        action=action,
        actions=actions,
        page=page,
        pages=max(1, (total + per_page - 1) // per_page),
        total=total,
    )
