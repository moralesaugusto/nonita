# Nonita Web UI

Web frontend (FastAPI + plain HTML/JS) for a remote Ollama server. Current version: see `nonita/__init__.py` (also shown at the bottom of the page). Contact: Telegram [@augustmd](https://t.me/augustmd).

## Run

```
uv sync
uv run nonita
```
Then open `http(s)://<host>:7860/`.

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_HOST` | `127.0.0.1` | Hostname/IP or full `http(s)://` URL |
| `OLLAMA_PORT` | `11434` | Port 1–65535 (ignored if host is a full URL) |
| `OLLAMA_TIMEOUT` | `120` | Request timeout, seconds |
| `NONITA_MAX_UPLOAD_MB` | `20` | Per-file attachment size limit |
| `NONITA_HOST` | `0.0.0.0` | Bind address (legacy: `GRADIO_SERVER_NAME`) |
| `NONITA_PORT` | `7860` | UI port (legacy: `GRADIO_SERVER_PORT`) |
| `NONITA_DEBUG` | off | Show the debug panel by default |

Host/port entered in the Connection panel can be saved per browser (Save / Reset / Forget); they live in that browser's `localStorage` only.

## Security notes

- The UI binds to `0.0.0.0` and has **no authentication**, so anyone on the network can use it. Run it on a trusted LAN only.
- `cert.pem` / `key.pem` in the project root enable HTTPS. The key is private and git-ignored.

## Docs and tests

- Design decisions: `doc/decisions.md`; changelog: `doc/CHANGELOG.md`.
- Tests: `uv run --extra dev pytest`.
