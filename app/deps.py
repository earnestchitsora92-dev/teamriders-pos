"""Shared request helpers: template rendering, auth dependencies, CSRF, audit."""
from __future__ import annotations

import secrets
from datetime import datetime
from urllib.parse import quote_plus

from fastapi import Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import db
from app.db import APP_DIR


class LoginRequired(Exception):
    pass


class AdminRequired(Exception):
    pass


class NotFoundError(Exception):
    pass


templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def _money(value) -> str:
    symbol = db.get_settings().get("currency", "$")
    return f"{symbol}{(value or 0) / 100:,.2f}"


def _datefmt(value, with_time: bool = False) -> str:
    if not value:
        return "—"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(str(value)[:19], fmt)
            return parsed.strftime("%d %b %Y, %H:%M" if with_time else "%d %b %Y")
        except ValueError:
            continue
    return str(value)


def _qty(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


templates.env.filters["money"] = _money
templates.env.filters["datefmt"] = _datefmt
templates.env.filters["qty"] = _qty
templates.env.globals["now"] = lambda: datetime.now()


def render(request: Request, name: str, status_code: int = 200, **ctx):
    settings = db.get_settings()
    user = getattr(request.state, "user", None)
    nav_discounts = nav_alerts = nav_stock_requests = 0
    if user is not None and user["role"] == "admin":
        try:
            nav_discounts = db.scalar("SELECT COUNT(*) FROM discounts WHERE status='pending'")
            nav_alerts = db.scalar("SELECT COUNT(*) FROM stock_alerts WHERE status='waiting'")
            nav_stock_requests = db.scalar("SELECT COUNT(*) FROM stock_requests WHERE status='pending'")
        except Exception:
            pass
    context = {
        "request": request,
        "user": user,
        "csrf": getattr(request.state, "csrf", ""),
        "settings": settings,
        "currency": settings.get("currency", "$"),
        "path": request.url.path,
        "msg": request.query_params.get("msg"),
        "msg_kind": request.query_params.get("kind", "ok"),
        "nav_discounts": nav_discounts,
        "nav_alerts": nav_alerts,
        "nav_stock_requests": nav_stock_requests,
    }
    context.update(ctx)
    return templates.TemplateResponse(request=request, name=name, context=context, status_code=status_code)


# ------------------------------------------------------------------ auth
def require_user(request: Request):
    user = getattr(request.state, "user", None)
    if user is None:
        raise LoginRequired()
    return user


def require_admin(request: Request):
    user = require_user(request)
    if user["role"] != "admin":
        raise AdminRequired()
    return user


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


# ------------------------------------------------------------------ csrf
def verify_csrf(request: Request, token: str | None) -> None:
    expected = getattr(request.state, "csrf", "")
    if not expected or not token or not secrets.compare_digest(str(token), expected):
        raise HTTPException(status_code=403, detail="Invalid or missing security token. Reload the page and try again.")


async def form_csrf(request: Request, csrf: str = Form("")) -> None:
    verify_csrf(request, csrf)


async def json_csrf(request: Request) -> None:
    verify_csrf(request, request.headers.get("x-csrf-token"))


async def read_json(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")
    return payload


# ------------------------------------------------------------------ misc
def redirect(url: str, msg: str | None = None, kind: str = "ok"):
    if msg:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}msg={quote_plus(msg)}&kind={kind}"
    return RedirectResponse(url, status_code=303)


def audit(request: Request | None, action: str, entity: str = "", entity_id: str = "", detail: str = "", user_id: int | None = None) -> None:
    if user_id is None and request is not None:
        user = getattr(request.state, "user", None)
        user_id = user["id"] if user else None
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO audit_log(user_id, action, entity, entity_id, detail, ip, created_at) VALUES(?,?,?,?,?,?,?)",
            (
                user_id,
                action,
                entity,
                str(entity_id),
                detail,
                client_ip(request) if request is not None else "",
                db.now_str(),
            ),
        )
