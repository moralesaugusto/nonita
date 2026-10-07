"""Extract text from user uploads for chat context."""

from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader

from nonita_webui.config import env_max_upload_bytes

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm", ".log"}
CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".rs",
    ".go",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".css",
    ".scss",
    ".sql",
    ".sh",
    ".toml",
    ".ini",
    ".env.example",
}


def _read_text_file(path: Path, max_chars: int) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if len(raw) > max_chars:
        return raw[:max_chars] + "\n\n[truncated…]"
    return raw


def _read_pdf(path: Path, max_chars: int) -> str:
    reader = PdfReader(str(path))
    chunks: list[str] = []
    total = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        chunks.append(text)
        total += len(text)
        if total >= max_chars:
            break
    joined = "\n\n".join(chunks)
    if len(joined) > max_chars:
        joined = joined[:max_chars] + "\n\n[truncated…]"
    return joined


def ingest_upload_paths(
    paths: list[str] | None,
    *,
    max_chars_per_file: int = 48_000,
    max_total_chars: int = 120_000,
) -> str:
    """Build a single context block from uploaded file paths (local paths)."""
    if not paths:
        return ""

    blocks: list[str] = []
    total = 0
    max_bytes = env_max_upload_bytes()

    for raw in paths:
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            logger.warning("Skip missing upload path: %s", raw)
            continue

        try:
            size = path.stat().st_size
        except OSError as exc:
            blocks.append(f"### {path.name}\n\n[Could not read file: {exc}]")
            continue
        if size > max_bytes:
            blocks.append(
                f"### {path.name}\n\n[Skipped: file is {size / 1_048_576:.1f} MB, "
                f"over the {max_bytes / 1_048_576:.0f} MB upload limit (NONITA_MAX_UPLOAD_MB).]"
            )
            continue

        ext = path.suffix.lower()
        try:
            if ext == ".pdf":
                body = _read_pdf(path, max_chars_per_file)
            elif ext in TEXT_EXTENSIONS or ext in CODE_EXTENSIONS:
                body = _read_text_file(path, max_chars_per_file)
            else:
                # Best-effort UTF-8 for unknown extensions
                body = _read_text_file(path, max_chars_per_file)
        except Exception as exc:  # noqa: BLE001 — surface per-file errors in UI context
            logger.exception("Failed to read %s", path)
            if ext == ".pdf":
                reason = f"PDF could not be parsed (encrypted, scanned or corrupt?): {exc}"
            else:
                reason = str(exc)
            blocks.append(f"### {path.name}\n\n[Could not read file: {reason}]")
            continue

        block = f"### {path.name}\n\n{body}"
        if total + len(block) > max_total_chars:
            blocks.append("[Further files omitted: total context limit reached.]")
            break
        blocks.append(block)
        total += len(block)

    if not blocks:
        return ""

    return (
        "The user attached the following files. Use them when answering.\n\n"
        + "\n\n---\n\n".join(blocks)
    )


def normalize_upload_paths(uploaded_files: list[str] | str | None) -> list[str] | None:
    """Normalize a path or list of paths to a list."""
    if uploaded_files is None:
        return None
    if isinstance(uploaded_files, str):
        return [uploaded_files] if uploaded_files else None
    return list(uploaded_files)


def summarize_upload_paths(
    paths: list[str] | str | None,
    *,
    max_chars_per_file: int = 48_000,
    max_total_chars: int = 120_000,
) -> str:
    """Short UI label describing current attachments."""
    normalized = normalize_upload_paths(paths)
    if not normalized:
        return "_No files attached — context applies to the next message only._"

    names: list[str] = []
    total_est = 0
    for raw in normalized:
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            continue
        names.append(path.name)
        try:
            if path.suffix.lower() == ".pdf":
                total_est += min(max_chars_per_file, path.stat().st_size // 4)
            else:
                total_est += min(max_chars_per_file, path.stat().st_size)
        except OSError:
            total_est += max_chars_per_file // 2

    if not names:
        return "_Attachments selected but files could not be read._"

    capped = min(total_est, max_total_chars)
    if capped >= 1000:
        size_note = f"~{capped // 1000}k chars max"
    else:
        size_note = f"~{capped} chars max"

    file_word = "file" if len(names) == 1 else "files"
    listing = ", ".join(f"**{n}**" for n in names[:5])
    if len(names) > 5:
        listing += f", +{len(names) - 5} more"
    return f"**{len(names)}** {file_word} attached ({size_note}): {listing}"
