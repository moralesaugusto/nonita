"""Local user accounts, password hashing and login sessions (SQLite).

A default ``admin`` / ``admin`` account is created on first use and must change
its password before it can use anything else.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone

from nonita.config import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "nonita_auth.db"
DEFAULT_USER = "admin"
DEFAULT_PASSWORD = "admin"
SESSION_SECONDS = 12 * 3600
COOKIE_NAME = "nonita_session"

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**15, 8, 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    pw_hash TEXT NOT NULL,
    must_change INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    expires REAL NOT NULL
);
"""


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, maxmem=128 * 1024 * 1024, dklen=32
    )
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
            maxmem=128 * 1024 * 1024, dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


_DUMMY_HASH = hash_password("nonita-dummy")  # equalises timing for unknown usernames


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO users (username, pw_hash, must_change, updated_at) VALUES (?, ?, 1, ?)",
            (DEFAULT_USER, hash_password(DEFAULT_PASSWORD), _now()),
        )
        conn.commit()
    try:
        DB_PATH.chmod(0o600)
    except OSError:
        pass
    return conn


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_session(conn: sqlite3.Connection, username: str) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
    conn.execute(
        "INSERT INTO sessions (token_hash, username, expires) VALUES (?, ?, ?)",
        (_token_hash(token), username, time.time() + SESSION_SECONDS),
    )
    return token


# ── brute-force throttle (per client + username, in memory) ───────────────
_MAX_FAILS = 5
_LOCK_SECONDS = 60
_fails: dict[str, tuple[int, float]] = {}
_fails_lock = threading.Lock()


def retry_after(key: str) -> int:
    """Seconds the caller must still wait before another login attempt (0 = allowed)."""
    with _fails_lock:
        count, until = _fails.get(key, (0, 0.0))
    return max(0, int(until - time.time())) if count >= _MAX_FAILS else 0


def _record(key: str, ok: bool) -> None:
    with _fails_lock:
        if ok:
            _fails.pop(key, None)
            return
        count, until = _fails.get(key, (0, 0.0))
        if count >= _MAX_FAILS and until < time.time():
            count = 0  # lock expired
        count += 1
        _fails[key] = (count, time.time() + _LOCK_SECONDS if count >= _MAX_FAILS else 0.0)


def login(username: str, password: str, client: str = "") -> tuple[str | None, bool]:
    """Return (session token, must_change) on success, or (None, False)."""
    key = f"{client}|{username.lower()}"
    with _connect() as conn:
        row = conn.execute("SELECT pw_hash, must_change FROM users WHERE username = ?", (username,)).fetchone()
        ok = verify_password(password, row[0] if row else _DUMMY_HASH) and row is not None
        _record(key, ok)
        if not ok:
            return None, False
        return _new_session(conn, username), bool(row[1])


def session_user(token: str | None) -> dict | None:
    """Return {"username", "must_change"} for a valid session token, else None."""
    if not token:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT u.username, u.must_change FROM sessions s JOIN users u ON u.username = s.username "
            "WHERE s.token_hash = ? AND s.expires > ?",
            (_token_hash(token), time.time()),
        ).fetchone()
    return {"username": row[0], "must_change": bool(row[1])} if row else None


def logout(token: str | None) -> None:
    if token:
        with _connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def password_problem(username: str, current: str, new: str) -> str | None:
    """No strength rules: the new password only has to be non-empty (it may even equal the current one)."""
    if not new:
        return "New password cannot be empty."
    return None


def change_password(username: str, current: str, new: str) -> tuple[str | None, str | None]:
    """Change a password. Returns (new session token, None) or (None, error). Other sessions are revoked."""
    with _connect() as conn:
        row = conn.execute("SELECT pw_hash FROM users WHERE username = ?", (username,)).fetchone()
        if row is None or not verify_password(current, row[0]):
            return None, "Current password is incorrect."
        problem = password_problem(username, current, new)
        if problem:
            return None, problem
        conn.execute(
            "UPDATE users SET pw_hash = ?, must_change = 0, updated_at = ? WHERE username = ?",
            (hash_password(new), _now(), username),
        )
        conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
        return _new_session(conn, username), None


def reset_to_default(username: str = DEFAULT_USER) -> None:
    """Recovery from the command line: reset *username* to admin's default password (change forced at next login)."""
    with _connect() as conn:
        conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.execute(
            "INSERT INTO users (username, pw_hash, must_change, updated_at) VALUES (?, ?, 1, ?)",
            (username, hash_password(DEFAULT_PASSWORD), _now()),
        )
        conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
