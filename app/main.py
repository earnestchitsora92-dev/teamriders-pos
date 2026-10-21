"""Application entry point: middleware, authentication, dashboard."""
from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import db, security, services
from app.deps import (
    AdminRequired,
    LoginRequired,
    NotFoundError,
    audit,
    redirect,
    render,
    require_user,
    verify_csrf,
)
from app.routes_admin import router as admin_router
from app.routes_sales import router as sales_router

db.init_db()

app = FastAPI(title="Team Riders POS", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=800)
app.mount("/static", StaticFiles(directory=str(db.APP_DIR / "static")), name="static")


# --------------------------------------------------------------- middleware
@app.middleware("http")
async def load_session(request: Request, call_next):
    request.state.user = None
    request.state.csrf = ""
    token = request.cookies.get(security.COOKIE_NAME)
    if token:
        row = db.fetch_one(
            "SELECT u.*, s.csrf AS session_csrf, s.expires_at AS session_expires, s.last_seen_at AS session_seen "
            "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
            (security.token_hash(token),),
        )
        if row and row["is_active"] and row["session_expires"] > db.now_str():
            request.state.user = row
            request.state.csrf = row["session_csrf"]
            try:
                last_seen = datetime.strptime(row["session_seen"], db.TS_FORMAT)
                if datetime.now() - last_seen > timedelta(minutes=5):
                    with db.tx() as conn:
                        conn.execute(
                            "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
                            (
                                db.now_str(),
                                (datetime.now() + timedelta(hours=security.SESSION_HOURS)).strftime(db.TS_FORMAT),
                                security.token_hash(token),
                            ),
                        )
            except ValueError:
                pass
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


# --------------------------------------------------------------- errors
@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc: LoginRequired):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Your session has expired. Please sign in again."}, status_code=401)
    return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)


@app.exception_handler(AdminRequired)
async def _admin_required(request: Request, exc: AdminRequired):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Administrator access is required."}, status_code=403)
    return render(request, "error.html", 403, title="Administrator only", message="This area is restricted to administrators.")


@app.exception_handler(NotFoundError)
async def _not_found(request: Request, exc: NotFoundError):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return render(request, "error.html", 404, title="Not found", message="The page you are looking for does not exist.")


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    return render(request, "error.html", exc.status_code, title="Something went wrong", message=str(exc.detail))


# --------------------------------------------------------------- auth routes
def _safe_next(target: str | None) -> str:
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return "/dashboard"


def _cookie_secure(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip() == "https"


@app.get("/healthz")
def healthz():
    # Touches the database on purpose: the keep-alive ping then keeps Neon's compute awake as well.
    database = "ok"
    try:
        db.scalar("SELECT 1")
    except Exception:
        database = "unavailable"
    return {"status": "ok", "database": database, "time": db.now_str()}


@app.get("/login")
def login_page(request: Request, next: str = "/dashboard"):
    if getattr(request.state, "user", None):
        return RedirectResponse("/dashboard", status_code=303)
    if db.scalar("SELECT COUNT(*) FROM users") == 0:
        return RedirectResponse("/setup", status_code=303)
    return render(request, "login.html", next=_safe_next(next))


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/dashboard"),
):
    blocked = security.login_blocked(username)
    if blocked:
        return render(request, "login.html", 429, error=f"Too many failed attempts. Try again in {blocked}s.", next=_safe_next(next))
    user = security.authenticate(username, password)
    if not user:
        security.register_failure(username)
        audit(request, "login_failed", "user", username, f"username={username}")
        return render(request, "login.html", 401, error="Invalid username or password.", next=_safe_next(next))
    security.clear_failures(username)
    token, _csrf = security.create_session(user["id"])
    with db.tx() as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (db.now_str(), user["id"]))
    audit(request, "login", "user", user["id"], f"role={user['role']}", user_id=user["id"])
    response = RedirectResponse(_safe_next(next), status_code=303)
    response.set_cookie(
        security.COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        max_age=security.SESSION_HOURS * 3600,
        path="/",
    )
    return response


@app.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    verify_csrf(request, csrf)
    token = request.cookies.get(security.COOKIE_NAME, "")
    user = getattr(request.state, "user", None)
    security.destroy_session(token)
    if user:
        audit(request, "logout", "user", user["id"])
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(security.COOKIE_NAME, path="/")
    return response


@app.get("/setup")
def setup_page(request: Request):
    if db.scalar("SELECT COUNT(*) FROM users") > 0:
        return RedirectResponse("/login", status_code=303)
    return render(request, "setup.html")


@app.post("/setup")
def setup_submit(
    request: Request,
    full_name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
    business_name: str = Form(""),
):
    if db.scalar("SELECT COUNT(*) FROM users") > 0:
        return RedirectResponse("/login", status_code=303)
    username = username.strip()
    if len(username) < 3:
        return render(request, "setup.html", 400, error="Username must be at least 3 characters.")
    if len(password) < 6:
        return render(request, "setup.html", 400, error="Password must be at least 6 characters.")
    if password != confirm:
        return render(request, "setup.html", 400, error="Passwords do not match.")
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO users(username, full_name, role, password_hash, created_at) VALUES(?,?,?,?,?)",
            (username, full_name.strip() or username, "admin", security.hash_password(password), db.now_str()),
        )
        admin_id = int(cur.lastrowid)
    values = {"business_initialised": "1"}
    if business_name.strip():
        values["business_name"] = business_name.strip()
    db.save_settings(values)
    db.seed_products()
    audit(request, "setup", "user", admin_id, "Owner account created", user_id=admin_id)
    token, _ = security.create_session(admin_id)
    response = RedirectResponse("/dashboard?msg=Welcome+aboard", status_code=303)
    response.set_cookie(security.COOKIE_NAME, token, httponly=True, samesite="lax",
                        secure=_cookie_secure(request), max_age=security.SESSION_HOURS * 3600, path="/")
    return response


@app.get("/account")
def account_page(request: Request, user=Depends(require_user)):
    return render(request, "account.html", salaries=services.salary_history_for(user["id"]))


@app.post("/account/password")
def account_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm: str = Form(...),
    csrf: str = Form(""),
    user=Depends(require_user),
):
    verify_csrf(request, csrf)
    if not security.verify_password(current_password, user["password_hash"]):
        return redirect("/account", "Current password is incorrect", "err")
    if len(new_password) < 6:
        return redirect("/account", "New password must be at least 6 characters", "err")
    if new_password != confirm:
        return redirect("/account", "New passwords do not match", "err")
    with db.tx() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (security.hash_password(new_password), user["id"]))
    audit(request, "password_change", "user", user["id"], "self-service")
    return redirect("/account", "Password updated")


# --------------------------------------------------------------- dashboard
@app.get("/")
def home(request: Request):
    if not getattr(request.state, "user", None):
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/dashboard")
def dashboard(request: Request, user=Depends(require_user)):
    if user["role"] == "admin":
        stats = services.dashboard_stats()
    else:
        stats = services.cashier_stats(user["id"])
    return render(request, "dashboard.html", stats=stats, is_admin=user["role"] == "admin")


app.include_router(sales_router)
app.include_router(admin_router)
