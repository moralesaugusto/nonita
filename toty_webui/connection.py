"""Shared Ollama host/port parsing and validation."""

from __future__ import annotations

MIN_PORT = 1
MAX_PORT = 65535


def parse_port(port: object) -> tuple[int | None, str | None]:
    """Return (port, error). Accepts ints, numeric floats and numeric strings."""
    if isinstance(port, bool) or port is None or (isinstance(port, str) and not port.strip()):
        return None, "Port must be a number from 1 to 65535."
    try:
        as_float = float(port)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None, "Port must be a number from 1 to 65535."
    if as_float != int(as_float):
        return None, "Port must be a whole number from 1 to 65535."
    value = int(as_float)
    if not MIN_PORT <= value <= MAX_PORT:
        return None, f"Port {value} is out of range; use 1 to 65535."
    return value, None


def parse_connection(host: object, port: object) -> tuple[str | None, str | None]:
    """Return (base_url, error). Exactly one of the two is None.

    A full ``http(s)://`` host is used as-is (no second port appended); a bare
    hostname/IP gets ``http://host:port``.
    """
    raw = "" if host is None else str(host).strip()
    if raw.lower() in ("http:", "https:", "http:/", "https:/", "http://", "https://"):
        return None, "Host URL is missing a hostname."
    text = raw.rstrip("/")
    if not text:
        return None, "Host is required (hostname, IP, or http(s):// URL)."
    if any(ch.isspace() for ch in text):
        return None, "Host must not contain spaces."

    lowered = text.lower()
    if lowered.startswith(("http://", "https://")):
        return text, None

    if "://" in text:
        return None, "Only http:// and https:// URLs are supported."

    port_int, err = parse_port(port)
    if err:
        return None, err
    return f"http://{text}:{port_int}", None
