"""Opt-in SQLite persistence and .txt export for conversations.

Nothing in this module is called unless the user explicitly enables it in
the UI (auto-save and history browsing are both off by default).
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from typing import Any

from toty_webui.client import gradio_content_to_text
from toty_webui.config import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "toty_history.db"

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


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(_SCHEMA)
    return conn


def _title_from_messages(messages: list[dict[str, Any]]) -> str:
    for m in messages:
        if m.get("role") == "user":
            text = gradio_content_to_text(m.get("content", "")).strip().replace("\n", " ")
            if text:
                return text[:60] + ("…" if len(text) > 60 else "")
    return "Untitled conversation"


def save_conversation(conv_id: int | None, model: str | None, messages: list[dict[str, Any]]) -> int:
    """Insert a new conversation, or update it if *conv_id* already exists. Returns the row id."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    title = _title_from_messages(messages)
    payload = json.dumps(messages, ensure_ascii=False)
    with _connect() as conn:
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


def list_conversation_choices() -> list[tuple[str, int]]:
    """(label, value) pairs ready for a gr.Dropdown, most recent first."""
    if not DB_PATH.exists():
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, model, updated_at FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
    return [
        (f"#{row[0]} · {row[1]} · {row[2] or '?'} · {row[3][:16].replace('T', ' ')}", row[0])
        for row in rows
    ]


def load_conversation(conv_id: int | None) -> list[dict[str, Any]]:
    if conv_id is None:
        return []
    with _connect() as conn:
        row = conn.execute(
            "SELECT messages_json FROM conversations WHERE id=?", (int(conv_id),)
        ).fetchone()
    if not row:
        return []
    return json.loads(row[0])


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
    return {
        "id": row[0],
        "title": row[1],
        "model": row[2],
        "messages": json.loads(row[3]),
        "created_at": row[4],
        "updated_at": row[5],
    }


def delete_conversation(conv_id: int | None) -> bool:
    """Delete a single conversation. Returns True if a row was removed."""
    if conv_id is None:
        return False
    with _connect() as conn:
        cur = conn.execute("DELETE FROM conversations WHERE id=?", (int(conv_id),))
        return cur.rowcount > 0


def wipe_all() -> int:
    """Permanently delete every saved conversation. Returns the number of rows removed."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM conversations")
        deleted = cur.rowcount
    # VACUUM cannot run inside a transaction, so it needs its own autocommit connection.
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()
    return deleted


def export_to_txt(messages: list[dict[str, Any]], model: str | None = None) -> str:
    """Write *messages* to a plain-text file and return its path."""
    lines: list[str] = []
    if model:
        lines.append(f"Model: {model}")
    lines.append(f"Exported: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append("=" * 60)
    for m in messages:
        role = str(m.get("role", "?")).upper()
        content = gradio_content_to_text(m.get("content", ""))
        lines.append(f"\n[{role}]\n{content}")
    text = "\n".join(lines)

    fd, path = tempfile.mkstemp(prefix="toty_conversation_", suffix=".txt")
    with open(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def export_conversation_to_txt(conv_id: int | None) -> str | None:
    """Export a saved conversation (by id) without loading it into the live chat."""
    convo = get_conversation(conv_id)
    if convo is None:
        return None
    return export_to_txt(convo["messages"], convo["model"])
