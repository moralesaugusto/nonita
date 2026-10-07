"""Environment-driven settings for Nonita Web UI.

Settings come from real environment variables, falling back to a ``.env`` file
in the project root (see ``.env.example``). Real environment variables win.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from *path* into os.environ without overriding existing variables."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


load_env_file(Path(os.environ.get("NONITA_ENV_FILE") or PROJECT_ROOT / ".env"))

# Fallbacks when OLLAMA_HOST / OLLAMA_PORT are unset (set them in .env).
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11434


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
    """Bind address (NONITA_HOST, legacy GRADIO_SERVER_NAME); default 127.0.0.1 (this machine only)."""
    return _env_first("NONITA_HOST", "GRADIO_SERVER_NAME") or "127.0.0.1"


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


def cert_paths() -> tuple[Path, Path]:
    """Certificate and key paths (NONITA_CERT / NONITA_KEY, default cert.pem / key.pem in the project root)."""
    cert = Path(os.environ.get("NONITA_CERT") or PROJECT_ROOT / "cert.pem")
    key = Path(os.environ.get("NONITA_KEY") or PROJECT_ROOT / "key.pem")
    return cert, key


def ssl_launch_kwargs() -> dict[str, str]:
    """uvicorn kwargs for HTTPS. The web UI is HTTPS-only (TLS 1.3, elliptic-curve key, no RSA).

    If neither file exists a self-signed ECDSA P-384 certificate is generated; if only one exists, refuse to start.
    """
    from nonita.tls import generate_self_signed, require_ec_key

    cert, key = cert_paths()
    if cert.is_file() != key.is_file():
        raise SystemExit(f"Found only one of {cert} / {key}. Provide both, or delete it to generate a new pair.")
    if not cert.is_file():
        generate_self_signed(cert, key)
        print(f"No certificate found: generated a self-signed ECDSA P-384 pair ({cert}, {key}). "
              "Run 'uv run nonita gen-cert --force --san <name-or-ip>' to add the names you browse to.")
    require_ec_key(key)
    return {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
