"""HTTP smoke test for the Team Riders POS: logins, permissions and the admin-only flows.

    python scripts/http_smoke_test.py http://127.0.0.1:8078

Uses a throw-away database (MX_DATA_DIR / DATABASE_URL decide which) and creates the owner,
a cashier, a stock request, a salary payment and a deletion through the real web forms.
"""
from __future__ import annotations

import http.cookiejar
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8078").rstrip("/")
CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep the 303 so the test can tell a redirect apart from a rendered page."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Session:
    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), NoRedirect
        )

    def raw(self, path: str, data: bytes | None = None, headers: dict | None = None):
        request = urllib.request.Request(BASE + path, data=data, headers=headers or {})
        try:
            with self.opener.open(request, timeout=60) as response:
                return response.status, response.read().decode("utf-8", "replace"), response.geturl()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace"), path

    def get(self, path: str):
        return self.raw(path)

    def post_form(self, path: str, fields: dict, csrf: str | None = None):
        if csrf:
            fields = {**fields, "csrf": csrf}
        body = urllib.parse.urlencode(fields).encode()
        return self.raw(path, body, {"Content-Type": "application/x-www-form-urlencoded"})

    def post_json(self, path: str, payload: str, csrf: str):
        return self.raw(path, payload.encode(), {"Content-Type": "application/json", "X-CSRF-Token": csrf})

    def csrf(self, path: str = "/pos") -> str:
        _status, html, _url = self.get(path)
        match = re.search(r'name="csrf"\s+value="([^"]+)"', html) or re.search(r'"csrf"\s*:\s*"([^"]+)"', html)
        return match.group(1) if match else ""


def status_of(result) -> int:
    return result[0]


def main() -> int:
    owner = Session()
    check("healthz", status_of(owner.get("/healthz")) == 200)
    owner.post_form("/setup", {
        "full_name": "HTTP Owner", "username": "owner", "password": "OwnerPass123",
        "confirm": "OwnerPass123", "business_name": "Team Riders",
    })
    check("setup created the owner", status_of(owner.get("/dashboard")) == 200)

    csrf = owner.csrf()
    check("owner csrf", bool(csrf))
    owner.post_form("/users/new", {
        "full_name": "HTTP Cashier", "username": "cashier", "role": "cashier", "password": "Cashier123",
    }, csrf)
    _s, users_html, _u = owner.get("/users")
    check("cashier listed", "HTTP Cashier" in users_html)
    cashier_row = next((row for row in users_html.split("<tr>") if "HTTP Cashier" in row), "")
    match = re.search(r"/users/(\d+)/password", cashier_row)
    check("cashier id found", match is not None, match.group(1) if match else "")
    cashier_id = match.group(1)

    # ------------------------------------------------------------------ cashier
    till = Session()
    till.post_form("/login", {"username": "cashier", "password": "Cashier123", "next": "/dashboard"})
    status, dash, _ = till.get("/dashboard")
    check("cashier dashboard loads", status == 200)
    for word in ("Revenue", "Profit", "profit", "Business cash", "Stock value", "Money in the business"):
        check(f"cashier dashboard hides '{word}'", word not in dash)
    check("cashier dashboard shows their receipts", "Your recent receipts" in dash)
    check("cashier nav has no Products", 'href="/products"' not in dash)
    check("cashier nav has no Salaries", 'href="/salaries"' not in dash)
    for admin_path in ("/products", "/users", "/salaries", "/cash", "/reports", "/closings", "/audit", "/settings"):
        check(f"cashier forbidden on {admin_path}", status_of(till.get(admin_path)) == 403)
    check("cashier may open stock requests", status_of(till.get("/stock/requests")) == 200)

    till_csrf = till.csrf()
    result = till.post_form("/stock/requests", {
        "product_id": "1", "kind": "breakage", "qty": "2", "reason": "Dropped in the workshop",
    }, till_csrf)
    check("cashier raised a breakage request", status_of(result) == 303, str(status_of(result)))
    _s, req_html, _u = till.get("/stock/requests?status=all")
    check("request visible to cashier", "Dropped in the workshop" in req_html)

    # -------------------------------------------------------------------- admin
    result = owner.post_form("/stock/requests/1/decide", {"decision": "deny", "note": "check first"}, owner.csrf("/stock/requests"))
    check("admin can refuse a request", status_of(result) == 303)
    till.post_form("/stock/requests", {
        "product_id": "1", "kind": "breakage", "qty": "2", "reason": "Dropped in the workshop",
    }, till.csrf())
    result = owner.post_form("/stock/requests/2/decide", {"decision": "approve", "note": "ok"}, owner.csrf("/stock/requests"))
    check("admin can approve a request", status_of(result) == 303)
    _s, ledger, _u = owner.get("/stock/movements")
    check("breakage appears in the ledger", "SR-0002" in ledger or "breakage" in ledger.lower())

    owner_csrf = owner.csrf("/salaries")
    result = owner.post_form("/salaries/pay", {
        "user_id": cashier_id, "amount": "1500.00", "period": "2026-10", "method": "cash",
        "note": "October salary",
    }, owner_csrf)
    check("admin paid a salary", status_of(result) == 303)
    _s, salaries_html, _u = owner.get("/salaries")
    check("salary in the history", "October salary" in salaries_html or "SAL-202610" in salaries_html)
    _s, cash_html, _u = owner.get("/cash")
    check("salary in the cash ledger", "Salary paid" in cash_html)

    _s, account_html, _u = till.get("/account")
    check("cashier sees their own salary payments", "Your salary payments" in account_html and "SAL-" in account_html)

    # ------------------------------------------------------------- deletions
    _s, products_html, _u = owner.get("/products")
    product_id = re.search(r"/products/(\d+)/edit", products_html)
    check("product id found", product_id is not None)
    result = owner.post_form(f"/products/{product_id.group(1)}/delete", {}, owner.csrf(f"/products/{product_id.group(1)}"))
    check("admin deleted a product", status_of(result) == 303)

    result = owner.post_form(f"/users/{cashier_id}/delete", {}, owner.csrf("/users"))
    check("admin deleted a user", status_of(result) == 303)
    _s, users_after, _u = owner.get("/users")
    check("deleted user gone from the team list", "HTTP Cashier" not in users_after)
    check("deleted user cannot sign in", status_of(Session().post_form(
        "/login", {"username": "cashier", "password": "Cashier123", "next": "/dashboard"})) == 401)

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if failed:
        for name, _ok, detail in failed:
            print("  failed:", name, detail)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
