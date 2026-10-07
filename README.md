# Toty Web UI

Gradio frontend for a remote Ollama server. Run with `python -m toty_webui`.

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_HOST` | `127.0.0.1` | Hostname/IP or full `http(s)://` URL |
| `OLLAMA_PORT` | `11434` | Port 1–65535 (ignored if host is a full URL) |
| `OLLAMA_TIMEOUT` | `120` | Request timeout, seconds |
| `TOTY_MAX_UPLOAD_MB` | `20` | Per-file attachment size limit |
| `GRADIO_SERVER_NAME` | `0.0.0.0` | Bind address |
| `GRADIO_SERVER_PORT` | first free from 7860 | UI port |

Host/port entered in the Connection panel can be saved per browser (Save / Reset / Forget); they live in that browser's `localStorage` only.

## Security notes

- The UI binds to `0.0.0.0` and has **no authentication**, so anyone on the network can use it. Run it on a trusted LAN only.
- `cert.pem` / `key.pem` in the project root enable HTTPS. The key is private: do not commit or share it.

## Tests

`pip install -e .[dev] && pytest`
