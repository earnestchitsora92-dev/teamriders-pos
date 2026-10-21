"""Password hashing, session handling and login throttling."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from datetime import datetime, timedelta

from app import db

COOKIE_NAME = "mx_session"
SESSION_HOURS = 12
SCRYPT_N = 2 ** 14


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=8, p=1, dklen=32)
    return f"scrypt${SCRYPT_N}$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    now = datetime.now()
    with db.tx() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.strftime(db.TS_FORMAT),))
        conn.execute(
            "INSERT INTO sessions(token_hash, user_id, csrf, created_at, expires_at, last_seen_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                token_hash(token),
                user_id,
                csrf,
                now.strftime(db.TS_FORMAT),
                (now + timedelta(hours=SESSION_HOURS)).strftime(db.TS_FORMAT),
                now.strftime(db.TS_FORMAT),
            ),
        )
    return token, csrf


def destroy_session(token: str) -> None:
    if not token:
        return
    with db.tx() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))


_fail_lock = threading.Lock()
_failures: dict[str, tuple[int, float]] = {}
MAX_FAILURES = 5
LOCK_SECONDS = 120


def login_blocked(username: str) -> int:
    key = username.strip().lower()
    now = time.time()
    with _fail_lock:
        count, until = _failures.get(key, (0, 0.0))
        if until > now:
            return int(until - now) + 1
        if until and until <= now:
            _failures.pop(key, None)
    return 0


def register_failure(username: str) -> None:
    key = username.strip().lower()
    now = time.time()
    with _fail_lock:
        count, _ = _failures.get(key, (0, 0.0))
        count += 1
        until = now + LOCK_SECONDS if count >= MAX_FAILURES else 0.0
        _failures[key] = (count, until)
        if count >= MAX_FAILURES:
            _failures[key] = (0, until)


def clear_failures(username: str) -> None:
    with _fail_lock:
        _failures.pop(username.strip().lower(), None)


def authenticate(username: str, password: str):
    row = db.fetch_one(
        "SELECT * FROM users WHERE lower(username) = lower(?) AND is_active = 1", (username.strip(),)
    )
    if not row:
        # Constant-ish work factor so missing users are not instantly distinguishable.
        verify_password(password, hash_password("timing-guard"))
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return row


def verify_admin_credentials(username: str, password: str):
    user = authenticate(username, password)
    if not user or user["role"] != "admin":
        return None
    return user
