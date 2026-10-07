"""Environment-driven defaults for Nonita Web UI."""

from __future__ import annotations

import os
from pathlib import Path

# Fallback when OLLAMA_HOST / OLLAMA_PORT are unset (override via env at runtime).
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11434

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def env_host() -> str:
    return os.environ.get("OLLAMA_HOST", DEFAULT_HOST).strip()


def env_port() -> int:
    raw = os.environ.get("OLLAMA_PORT", str(DEFAULT_PORT)).strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_PORT
    return value if 1 <= value <= 65535 else DEFAULT_PORT


DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_UPLOAD_MB = 20


def env_timeout() -> float:
    """Request timeout in seconds from OLLAMA_TIMEOUT (positive number), else 120."""
    raw = os.environ.get("OLLAMA_TIMEOUT", "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


def env_max_upload_bytes() -> int:
    """Per-file upload cap from NONITA_MAX_UPLOAD_MB (default 20 MB)."""
    raw = os.environ.get("NONITA_MAX_UPLOAD_MB", "").strip()
    try:
        mb = float(raw)
    except ValueError:
        mb = DEFAULT_MAX_UPLOAD_MB
    if mb <= 0:
        mb = DEFAULT_MAX_UPLOAD_MB
    return int(mb * 1024 * 1024)


DEFAULT_SERVER_PORT = 7860


def _env_first(*names: str) -> str:
    for name in names:
        raw = os.environ.get(name, "").strip()
        if raw:
            return raw
    return ""


def server_host() -> str:
    """Bind address (NONITA_HOST, legacy GRADIO_SERVER_NAME); default 0.0.0.0 (LAN-wide)."""
    return _env_first("NONITA_HOST", "GRADIO_SERVER_NAME") or "0.0.0.0"


def server_port() -> int:
    """UI port (NONITA_PORT, legacy GRADIO_SERVER_PORT); default 7860."""
    raw = _env_first("NONITA_PORT", "GRADIO_SERVER_PORT")
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_SERVER_PORT
    return value if 1 <= value <= 65535 else DEFAULT_SERVER_PORT


def env_debug_mode() -> bool:
    return os.environ.get("NONITA_DEBUG", "").lower() in ("1", "true", "yes")


def ssl_launch_kwargs() -> dict[str, str]:
    """Enable HTTPS (uvicorn kwargs) when cert.pem and key.pem exist beside the project root."""
    cert = PROJECT_ROOT / "cert.pem"
    key = PROJECT_ROOT / "key.pem"
    if cert.is_file() and key.is_file():
        return {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    return {}
