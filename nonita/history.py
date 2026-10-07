"""Opt-in encrypted SQLite persistence and .txt export for conversations.

Nothing here runs unless the user explicitly saves. Titles and messages are encrypted with AES-256-GCM
under a key kept in a separate owner-only file (``nonita_history.key``). Deleting a conversation or wiping
the history rebuilds the database without the removed data and shreds the old database and key files
(NIST SP 800-88 Rev.1: overwrite = Clear, destroying the key = cryptographic-erase Purge).
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from nonita.client import content_to_text
from nonita.config import PROJECT_ROOT
from nonita.secure_delete import shred_file

DB_PATH = PROJECT_ROOT / "nonita_history.db"
_PREFIX = "enc1:"
_lock = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    model TEXT,
    messages_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _locked(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)

    return wrapper


# ── key handling ──────────────────────────────────────────────────────────
def key_path() -> Path:
    return DB_PATH.with_suffix(".key")


def _write_key_file(path: Path, key: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
        fh.flush()
        os.fsync(fh.fileno())


def _load_key(create: bool = True) -> bytes | None:
    path = key_path()
    if path.is_file():
        key = path.read_bytes()
        if len(key) != 32:
            raise RuntimeError(f"{path} is not a valid history key (expected 32 bytes).")
        return key
    if not create:
        return None
    key = secrets.token_bytes(32)
    _write_key_file(path, key)
    return key


def _encrypt(key: bytes, column: str, plaintext: str) -> str:
    nonce = secrets.token_bytes(12)
    blob = nonce + AESGCM(key).encrypt(nonce, plaintext.encode(), column.encode())
    return _PREFIX + base64.b64encode(blob).decode()


def _decrypt(key: bytes | None, column: str, stored: str) -> str:
    if not stored.startswith(_PREFIX):  # legacy plaintext row (migrated on first connect)
        return stored
    if key is None:
        raise RuntimeError("History key file is missing, so saved conversations cannot be read.")
    blob = base64.b64decode(stored[len(_PREFIX):])
    try:
        return AESGCM(key).decrypt(blob[:12], blob[12:], column.encode()).decode()
    except InvalidTag as exc:
        raise RuntimeError("History key does not match the database (wrong or replaced key file).") from exc


# ── database access ───────────────────────────────────────────────────────
def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA secure_delete=ON")   # zero deleted content inside the file
    conn.execute("PRAGMA journal_mode=DELETE")  # no long-lived -wal file holding old pages
    conn.executescript(_SCHEMA)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return conn


def _connect() -> sqlite3.Connection:
    conn = _open(DB_PATH)
    plain = conn.execute("SELECT 1 FROM conversations WHERE messages_json NOT LIKE ? LIMIT 1", (_PREFIX + "%",)).fetchone()
    if plain:
        conn.close()
        _rebuild(None)  # encrypt existing plaintext conversations and shred the plaintext file
        conn = _open(DB_PATH)
    return conn


def _read_rows(conn: sqlite3.Connection, key: bytes | None) -> list[tuple]:
    rows = conn.execute(
        "SELECT id, title, model, messages_json, created_at, updated_at FROM conversations ORDER BY id"
    ).fetchall()
    return [
        (r[0], _decrypt(key, "title", r[1]), r[2], _decrypt(key, "messages", r[3]), r[4], r[5]) for r in rows
    ]


def _rebuild(drop_id: int | None) -> int:
    """Re-create the database with a fresh key, without *drop_id*, and shred the old database and key files.

    Returns the number of rows that were dropped (0 or 1). ``drop_id=None`` keeps every row (used to migrate
    legacy plaintext databases and to rotate the key).
    """
    old_key = _load_key(create=False)
    with _open(DB_PATH) as old:
        rows = _read_rows(old, old_key)
    old.close()
    kept = [r for r in rows if r[0] != drop_id]
    new_key = secrets.token_bytes(32)
    new_db = DB_PATH.with_name(DB_PATH.name + ".new")
    new_key_path = key_path().with_name(key_path().name + ".new")
    shred_file(new_db)
    conn = _open(new_db)
    with conn:
        conn.executemany(
            "INSERT INTO conversations (id, title, model, messages_json, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            [(r[0], _encrypt(new_key, "title", r[1]), r[2], _encrypt(new_key, "messages", r[3]), r[4], r[5]) for r in kept],
        )
    conn.close()
    _write_key_file(new_key_path, new_key)
    # Swap in the new files; the old ones are set aside under random names, then shredded.
    tag = secrets.token_hex(6)
    aside_db, aside_key = DB_PATH.with_name(f".old-{tag}.db"), key_path().with_name(f".old-{tag}.key")
    os.replace(DB_PATH, aside_db)
    if key_path().exists():
        os.replace(key_path(), aside_key)
    os.replace(new_db, DB_PATH)
    os.replace(new_key_path, key_path())
    for leftover in (aside_db, aside_key, DB_PATH.with_name(DB_PATH.name + "-journal"), DB_PATH.with_name(DB_PATH.name + "-wal"),
                     DB_PATH.with_name(DB_PATH.name + "-shm")):
        shred_file(leftover)
    return len(rows) - len(kept)


def _title_from_messages(messages: list[dict[str, Any]]) -> str:
    for m in messages:
        if m.get("role") == "user":
            text = content_to_text(m.get("content", "")).strip().replace("\n", " ")
            if text:
                return text[:60] + ("…" if len(text) > 60 else "")
    return "Untitled conversation"


@_locked
def save_conversation(conv_id: int | None, model: str | None, messages: list[dict[str, Any]]) -> int:
    """Insert a new conversation, or update it if *conv_id* already exists. Returns the row id."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect() as conn:
        key = _load_key()
        title = _encrypt(key, "title", _title_from_messages(messages))
        payload = _encrypt(key, "messages", json.dumps(messages, ensure_ascii=False))
        if conv_id is not None:
            cur = conn.execute(
                "UPDATE conversations SET title=?, model=?, messages_json=?, updated_at=? WHERE id=?",
                (title, model, payload, now, conv_id),
            )
            if cur.rowcount:
                return conv_id
        cur = conn.execute(
            "INSERT INTO conversations (title, model, messages_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (title, model, payload, now, now),
        )
        return int(cur.lastrowid)


@_locked
def list_conversation_choices() -> list[tuple[str, int]]:
    """(label, value) pairs most recent first."""
    if not DB_PATH.exists():
        return []
    with _connect() as conn:
        rows = conn.execute("SELECT id, title, model, updated_at FROM conversations ORDER BY updated_at DESC").fetchall()
        key = _load_key(create=False)
        return [
            (f"#{r[0]} · {_decrypt(key, 'title', r[1])} · {r[2] or '?'} · {r[3][:16].replace('T', ' ')}", r[0])
            for r in rows
        ]


@_locked
def load_conversation(conv_id: int | None) -> list[dict[str, Any]]:
    convo = get_conversation(conv_id)
    return convo["messages"] if convo else []


@_locked
def get_conversation(conv_id: int | None) -> dict[str, Any] | None:
    if conv_id is None:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, title, model, messages_json, created_at, updated_at FROM conversations WHERE id=?",
            (int(conv_id),),
        ).fetchone()
        if not row:
            return None
        key = _load_key(create=False)
        return {
            "id": row[0],
            "title": _decrypt(key, "title", row[1]),
            "model": row[2],
            "messages": json.loads(_decrypt(key, "messages", row[3])),
            "created_at": row[4],
            "updated_at": row[5],
        }


@_locked
def delete_conversation(conv_id: int | None) -> bool:
    """Securely delete one conversation (rebuild + shred + key rotation). Returns True if it existed."""
    if conv_id is None or not DB_PATH.exists():
        return False
    with _connect() as conn:
        exists = conn.execute("SELECT 1 FROM conversations WHERE id=?", (int(conv_id),)).fetchone() is not None
    conn.close()
    return exists and _rebuild(int(conv_id)) == 1


@_locked
def wipe_all() -> int:
    """Securely destroy every saved conversation: shred the database and its key. Returns the row count."""
    if not DB_PATH.exists():
        shred_file(key_path())
        return 0
    with _open(DB_PATH) as conn:
        count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    conn.close()
    for path in (DB_PATH, key_path(), *(DB_PATH.with_name(DB_PATH.name + sfx) for sfx in ("-journal", "-wal", "-shm"))):
        shred_file(path)
    return count


def export_to_txt(messages: list[dict[str, Any]], model: str | None = None) -> str:
    """Write *messages* to a plain-text file and return its path."""
    lines: list[str] = []
    if model:
        lines.append(f"Model: {model}")
    lines.append(f"Exported: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append("=" * 60)
    for m in messages:
        role = str(m.get("role", "?")).upper()
        content = content_to_text(m.get("content", ""))
        lines.append(f"\n[{role}]\n{content}")
    text = "\n".join(lines)

    fd, path = tempfile.mkstemp(prefix="nonita_conversation_", suffix=".txt")
    with open(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def export_conversation_to_txt(conv_id: int | None) -> str | None:
    """Export a saved conversation (by id) without loading it into the live chat."""
    convo = get_conversation(conv_id)
    if convo is None:
        return None
    return export_to_txt(convo["messages"], convo["model"])
