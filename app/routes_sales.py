"""POS, sales, receipts, quotations and discount approval routes."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, Response

from app import db, pdf, security, services
from app.deps import (
    NotFoundError,
    audit,
    json_csrf,
    read_json,
    redirect,
    render,
    require_admin,
    require_user,
    verify_csrf,
)

router = APIRouter()

PAYMENT_METHODS = ("cash", "mobile", "card", "bank")


def _date_range(request: Request, default_days: int = 30) -> tuple[str, str, str, str]:
    today = date.today()
    frm = request.query_params.get("from") or (today - timedelta(days=default_days)).isoformat()
    to = request.query_params.get("to") or today.isoformat()
    try:
        start = date.fromisoformat(frm)
        end = date.fromisoformat(to)
    except ValueError:
        start, end = today - timedelta(days=default_days), today
    return start.isoformat(), end.isoformat(), start.isoformat() + " 00:00:00", (end + timedelta(days=1)).isoformat() + " 00:00:00"


# ----------------------------------------------------------------- PO
@router.get("/pos")
def pos_page(request: Request, user=Depends(require_user)):
    mode = request.query_params.get("mode", "sale")
    quote_id = request.query_params.get("quote")
    prefill = None
    if quote_id:
        quote, items = services.quotation_detail(int(quote_id)) if quote_id.isdigit() else (None, [])
        if quote:
            prefill = {
                "customer_name": quote["customer_name"],
                "customer_phone": quote["customer_phone"],
                "customer_email": quote["customer_email"],
                "items": [{"product_id": i["product_id"], "qty": i["qty"], "name": i["name"], "sku": i["sku"],
                           "price_cents": i["unit_price_cents"]} for i in items],
            }
    categories = [
        r["category"] for r in db.fetch_all("SELECT DISTINCT category FROM products WHERE is_active=1 ORDER BY category")
    ]
    tax_rate = float(db.get_settings().get("tax_rate_percent", "0") or 0)
    return render(
        request,
        "pos.html",
        mode="quote" if mode == "quote" else "sale",
        categories=categories,
        prefill=prefill,
        tax_rate=tax_rate,
        payment_methods=PAYMENT_METHODS,
    )


@router.get("/api/products/search")
def api_product_search(q: str = "", user=Depends(require_user)):
    return {"items": services.search_products(q, 10)}


@router.get("/api/products/{product_id}/alerts")
def api_alert_required(product_id: int, user=Depends(require_user)):
    row = db.fetch_one("SELECT COUNT(*) AS c FROM stock_alerts WHERE product_id=? AND status='waiting'", (product_id,))
    return {"waiting": row["c"] if row else 0}


@router.post("/api/discounts")
async def api_create_discount(request: Request, user=Depends(require_user), _=Depends(json_csrf)):
    payload = await read_json(request)
    try:
        subtotal = int(payload.get("subtotal_cents") or 0)
        kind = (payload.get("kind") or "percent").lower()
        raw_value = payload.get("value")
        value = db.to_cents(raw_value) if kind == "amount" else int(float(raw_value or 0))
        reason = str(payload.get("reason") or "")
        admin_username = str(payload.get("admin_username") or "").strip()
        admin_password = str(payload.get("admin_password") or "")
    except (TypeError, ValueError):
        return JSONResponse({"error": "Invalid discount details"}, status_code=400)

    approver = None
    if admin_username:
        if not admin_password:
            return JSONResponse({"error": "Enter the administrator password to authorise this discount."}, status_code=400)
        approver = security.verify_admin_credentials(admin_username, admin_password)
        if not approver:
            security.register_failure(admin_username)
            audit(request, "discount_auth_failed", "user", admin_username, "bad admin credentials")
            return JSONResponse({"error": "Administrator username or password is incorrect."}, status_code=403)
        security.clear_failures(admin_username)
    elif user["role"] == "admin":
        approver = user

    try:
        result = services.request_discount(
            cashier=user, kind=kind, value=value, subtotal_cents=subtotal, reason=reason, approved_by=approver
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    audit(
        request,
        "discount_approved" if approver else "discount_requested",
        "discount",
        result["id"],
        f"code={result['code']} amount={result['discount_cents']} reason={reason[:120]}",
    )
    result["message"] = (
        "Discount approved and ready to apply." if approver else "Discount request sent to the administrator."
    )
    return result


@router.get("/api/discounts/{code}")
def api_discount_lookup(code: str, user=Depends(require_user)):
    row = db.fetch_one(
        "SELECT d.*, u.full_name AS approver_name FROM discounts d LEFT JOIN users u ON u.id = d.approved_by "
        "WHERE d.code = ?",
        (code.strip().upper(),),
    )
    if not row:
        return JSONResponse({"error": "Discount code not found."}, status_code=404)
    if row["status"] != "approved":
        return JSONResponse({"error": f"Discount {row['code']} is {row['status']}."}, status_code=400)
    if row["expires_at"] < db.now_str():
        with db.tx() as conn:
            conn.execute("UPDATE discounts SET status='expired' WHERE id = ?", (row["id"],))
        return JSONResponse({"error": "This discount approval has expired."}, status_code=400)
    return {
        "code": row["code"],
        "discount_cents": row["discount_cents"],
        "kind": row["kind"],
        "value": row["value"],
        "expires_at": row["expires_at"],
        "approved_by": row["approver_name"] or "",
    }


@router.post("/api/sales")
async def api_create_sale(request: Request, user=Depends(require_user), _=Depends(json_csrf)):
    payload = await read_json(request)
    method = (payload.get("payment_method") or "cash").lower()
    if method not in PAYMENT_METHODS:
        return JSONResponse({"error": "Unsupported payment method"}, status_code=400)
    try:
        result = services.create_sale(
            user,
            payload.get("items") or [],
            discount_code=(payload.get("discount_code") or "").strip() or None,
            customer_name=str(payload.get("customer_name") or ""),
            customer_phone=str(payload.get("customer_phone") or ""),
            customer_email=str(payload.get("customer_email") or ""),
            payment_method=method,
            tendered_cents=db.to_cents(payload.get("tendered")) if payload.get("tendered") else 0,
            note=str(payload.get("note") or ""),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    audit(
        request,
        "sale",
        "sale",
        result["sale_id"],
        f"{result['receipt_no']} total={result['total_cents']} discount={result['discount_cents']}",
    )
    result["receipt_url"] = f"/sales/{result['sale_id']}/receipt"
    result["pdf_url"] = f"/sales/{result['sale_id']}/receipt.pdf"
    result["view_url"] = f"/sales/{result['sale_id']}"
    return result


@router.post("/api/quotations")
async def api_create_quotation(request: Request, user=Depends(require_user), _=Depends(json_csrf)):
    payload = await read_json(request)
    try:
        result = services.create_quotation(
            user,
            payload.get("items") or [],
            discount_code=(payload.get("discount_code") or "").strip() or None,
            customer_name=str(payload.get("customer_name") or ""),
            customer_email=str(payload.get("customer_email") or ""),
            customer_phone=str(payload.get("customer_phone") or ""),
            note=str(payload.get("note") or ""),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    audit(request, "quotation", "quotation", result["quotation_id"], f"{result['quote_no']} total={result['total_cents']}")
    result["view_url"] = f"/quotations/{result['quotation_id']}"
    result["pdf_url"] = f"/quotations/{result['quotation_id']}.pdf"
    return result


@router.post("/api/alerts")
async def api_create_alert(request: Request, user=Depends(require_user), _=Depends(json_csrf)):
    payload = await read_json(request)
    try:
        alert_id = services.add_stock_alert(
            int(payload.get("product_id") or 0),
            str(payload.get("email") or ""),
            customer_name=str(payload.get("customer_name") or ""),
            phone=str(payload.get("phone") or ""),
            created_by=user["id"],
        )
    except (ValueError, TypeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    audit(request, "stock_alert", "product", payload.get("product_id"), f"alert={alert_id} email={payload.get('email')}")
    return {"ok": True, "id": alert_id, "message": "Customer added to the back-in-stock waiting list."}


# ----------------------------------------------------------------- sales
@router.get("/sales")
def sales_list(request: Request, user=Depends(require_user)):
    frm, to, start, end = _date_range(request)
    status = request.query_params.get("status") or "all"
    q = (request.query_params.get("q") or "").strip()
    page = max(1, int(request.query_params.get("page") or 1))
    per_page = 25
    where = ["s.created_at >= ?", "s.created_at < ?"]
    params: list = [start, end]
    if status in ("completed", "voided"):
        where.append("s.status = ?")
        params.append(status)
    if q:
        where.append("(s.receipt_no LIKE ? OR s.customer_name LIKE ? OR s.customer_phone LIKE ?)")
        params.extend([f"%{q}%"] * 3)
    if user["role"] != "admin":
        where.append("s.cashier_id = ?")
        params.append(user["id"])
    clause = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) FROM sales s WHERE {clause}", tuple(params))
    rows = db.fetch_all(
        f"SELECT s.*, u.full_name AS cashier_name FROM sales s JOIN users u ON u.id = s.cashier_id "
        f"WHERE {clause} ORDER BY s.created_at DESC, s.id DESC LIMIT ? OFFSET ?",
        tuple(params) + (per_page, (page - 1) * per_page),
    )
    totals = db.fetch_one(
        f"SELECT COALESCE(SUM(CASE WHEN s.status='completed' THEN s.total_cents ELSE 0 END),0) AS revenue, "
        f"COALESCE(SUM(CASE WHEN s.status='completed' THEN s.profit_cents ELSE 0 END),0) AS profit "
        f"FROM sales s WHERE {clause}",
        tuple(params),
    )
    return render(
        request,
        "sales.html",
        sales=[dict(r) for r in rows],
        total=total,
        page=page,
        pages=max(1, (total + per_page - 1) // per_page),
        frm=frm,
        to=to,
        status=status,
        q=q,
        totals=dict(totals),
    )


@router.get("/sales/{sale_id}")
def sale_page(request: Request, sale_id: int, user=Depends(require_user)):
    sale, items = services.sale_detail(sale_id)
    if not sale:
        raise NotFoundError()
    if user["role"] != "admin" and sale["cashier_id"] != user["id"]:
        raise NotFoundError()
    return render(request, "sale_detail.html", sale=dict(sale), items=[dict(i) for i in items])


@router.get("/sales/{sale_id}/receipt")
def sale_receipt(request: Request, sale_id: int, user=Depends(require_user)):
    sale, items = services.sale_detail(sale_id)
    if not sale or (user["role"] != "admin" and sale["cashier_id"] != user["id"]):
        raise NotFoundError()
    autoprint = request.query_params.get("autoprint") == "1"
    return render(request, "receipt.html", sale=dict(sale), items=[dict(i) for i in items], autoprint=autoprint)


@router.get("/sales/{sale_id}/receipt.pdf")
def sale_receipt_pdf(request: Request, sale_id: int, user=Depends(require_user)):
    sale, items = services.sale_detail(sale_id)
    if not sale or (user["role"] != "admin" and sale["cashier_id"] != user["id"]):
        raise NotFoundError()
    size = request.query_params.get("size", "thermal")
    content = pdf.receipt_pdf(sale, items, size="a4" if size == "a4" else "thermal")
    filename = f"{sale['receipt_no']}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.post("/sales/{sale_id}/void")
def sale_void(
    request: Request,
    sale_id: int,
    reason: str = Form(...),
    csrf: str = Form(""),
    admin=Depends(require_admin),
):
    verify_csrf(request, csrf)
    try:
        services.void_sale(sale_id, admin, reason)
    except ValueError as exc:
        return redirect(f"/sales/{sale_id}", str(exc), "err")
    audit(request, "sale_void", "sale", sale_id, f"reason={reason[:160]}")
    return redirect(f"/sales/{sale_id}", "Sale voided and stock returned")


# ----------------------------------------------------------------- quotations
@router.get("/quotations")
def quotations_list(request: Request, user=Depends(require_user)):
    status = request.query_params.get("status") or "all"
    q = (request.query_params.get("q") or "").strip()
    where, params = [], []
    if status != "all":
        where.append("q.status = ?")
        params.append(status)
    if q:
        where.append("(q.quote_no LIKE ? OR q.customer_name LIKE ? OR q.customer_email LIKE ?)")
        params.extend([f"%{q}%"] * 3)
    if user["role"] != "admin":
        where.append("q.created_by = ?")
        params.append(user["id"])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = db.fetch_all(
        "SELECT q.*, u.full_name AS created_by_name FROM quotations q JOIN users u ON u.id = q.created_by"
        f"{clause} ORDER BY q.id DESC LIMIT 200",
        tuple(params),
    )
    return render(request, "quotations.html", quotes=[dict(r) for r in rows], status=status, q=q)


@router.get("/quotations/{quote_id}.pdf")
def quotation_pdf(quote_id: int, user=Depends(require_user)):
    quote, items = services.quotation_detail(quote_id)
    if not quote:
        raise NotFoundError()
    content = pdf.quotation_pdf(quote, items)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{quote["quote_no"]}.pdf"'},
    )


@router.get("/quotations/{quote_id}")
def quotation_page(request: Request, quote_id: int, user=Depends(require_user)):
    quote, items = services.quotation_detail(quote_id)
    if not quote:
        raise NotFoundError()
    return render(request, "quotation_detail.html", quote=dict(quote), items=[dict(i) for i in items])


@router.post("/quotations/{quote_id}/status")
def quotation_status(
    request: Request,
    quote_id: int,
    status: str = Form(...),
    csrf: str = Form(""),
    user=Depends(require_user),
):
    verify_csrf(request, csrf)
    if status not in ("draft", "sent", "accepted", "rejected", "cancelled", "expired"):
        return redirect(f"/quotations/{quote_id}", "Unknown status", "err")
    with db.tx() as conn:
        conn.execute("UPDATE quotations SET status = ? WHERE id = ?", (status, quote_id))
    audit(request, "quotation_status", "quotation", quote_id, status)
    return redirect(f"/quotations/{quote_id}", f"Quotation marked {status}")


@router.post("/quotations/{quote_id}/delete")
def quotation_delete(request: Request, quote_id: int, csrf: str = Form(""), admin=Depends(require_admin)):
    verify_csrf(request, csrf)
    with db.tx() as conn:
        conn.execute("DELETE FROM quotations WHERE id = ?", (quote_id,))
    audit(request, "quotation_delete", "quotation", quote_id, "deleted")
    return redirect("/quotations", "Quotation deleted")


# ----------------------------------------------------------------- discounts
@router.get("/discounts")
def discounts_page(request: Request, admin=Depends(require_admin)):
    services.expire_discounts()
    status = request.query_params.get("status") or "all"
    frm, to, start, end = _date_range(request, default_days=90)
    rows = db.fetch_all(
        "SELECT d.*, cu.full_name AS cashier_name, au.full_name AS approver_name, s.receipt_no "
        "FROM discounts d JOIN users cu ON cu.id = d.requested_by LEFT JOIN users au ON au.id = d.approved_by "
        "LEFT JOIN sales s ON s.id = d.sale_id WHERE d.created_at >= ? AND d.created_at < ? "
        + ("AND d.status = ? " if status != "all" else "")
        + "ORDER BY d.id DESC LIMIT 500",
        (start, end, status) if status != "all" else (start, end),
    )
    stats = services.discount_stats(start, end)
    used_total = db.scalar(
        "SELECT COALESCE(SUM(discount_cents),0) FROM sales WHERE status='completed' AND created_at >= ? AND created_at < ?",
        (start, end),
    )
    pending = db.scalar("SELECT COUNT(*) FROM discounts WHERE status='pending'")
    revenue = db.scalar(
        "SELECT COALESCE(SUM(subtotal_cents),0) FROM sales WHERE status='completed' AND created_at >= ? AND created_at < ?",
        (start, end),
    )
    return render(
        request,
        "discounts.html",
        rows=[dict(r) for r in rows],
        stats=stats,
        status=status,
        frm=frm,
        to=to,
        used_total=used_total,
        pending_count=pending,
        discount_rate=round(used_total / revenue * 100, 2) if revenue else 0.0,
    )


@router.post("/discounts/{discount_id}/decide")
def discount_decide(
    request: Request,
    discount_id: int,
    decision: str = Form(...),
    note: str = Form(""),
    csrf: str = Form(""),
    admin=Depends(require_admin),
):
    verify_csrf(request, csrf)
    try:
        services.decide_discount(discount_id, admin, decision == "approve", note)
    except ValueError as exc:
        return redirect("/discounts", str(exc), "err")
    audit(request, "discount_decision", "discount", discount_id, f"{decision} {note[:120]}")
    return redirect("/discounts", f"Discount request {decision}d")
