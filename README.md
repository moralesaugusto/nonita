# Nonita

A small, self-hosted web UI for chatting with models on an [Ollama](https://ollama.com) server. FastAPI backend, plain HTML/JS frontend, no build step. Named after the noni fruit (*Morinda citrifolia*).

![Nonita chat, light theme](docs/screenshots/chat-light.png)

<p>
  <img src="docs/screenshots/chat-dark.png" alt="Dark theme" width="64%">
  <img src="docs/screenshots/mobile.png" alt="Mobile layout" width="22%">
</p>

## Features

- Sign-in with local accounts; the default `admin` / `admin` must be changed on first login
- Streaming chat with any model pulled on your Ollama host, with a Stop button
- Thinking-mode control for models that support it
- Saved conversations (local SQLite), export to text / file
- Attachments: text files and PDFs (per-file size limit)
- Optional DuckDuckGo web search; the reply shows how many results were used
- Token / speed metrics, model and server info, loaded-model memory view
- Light and dark themes, responsive layout, keyboard and screen-reader friendly
- HTTPS with elliptic-curve certificates only (ECDSA P-384), strong ciphers, security headers

## Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- A reachable Ollama server (local or remote)

## Quick start

```bash
git clone https://github.com/<your-user>/nonita.git
cd nonita
cp .env.example .env          # then edit OLLAMA_HOST etc.
uv run nonita gen-cert        # optional but recommended: HTTPS certificate
uv run nonita
```

Open the printed address (default `https://localhost:7860/`, or `http://` if you skipped the certificate; browsers warn about self-signed certificates).

### First login

![Sign-in](docs/screenshots/login.png)

1. Sign in with **`admin`** / **`admin`**.
2. You are immediately required to choose a new password (no length, complexity or "must differ" rules; it just can't be empty). Nothing else works until you do.
3. Later, use **Change password** in the top bar. Changing a password signs out all other sessions.

![Forced password change](docs/screenshots/change-password.png)

Forgot the password? On the machine running Nonita: `uv run nonita reset-password` resets `admin` to `admin` (and forces a change again).

## Configuration

Everything lives in `.env` (copy [`.env.example`](.env.example)); real environment variables override it. `.env` is git-ignored, so your server's IP and ports stay private.

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_HOST` | `127.0.0.1` | Hostname/IP or full `http(s)://` URL of Ollama |
| `OLLAMA_PORT` | `11434` | Port 1–65535 (ignored if host is a full URL) |
| `OLLAMA_TIMEOUT` | `120` | Request timeout, seconds |
| `NONITA_HOST` | `127.0.0.1` | Address the UI binds to. `0.0.0.0` exposes it to your whole network |
| `NONITA_PORT` | `7860` | UI port |
| `NONITA_MAX_UPLOAD_MB` | `20` | Per-file attachment size limit |
| `NONITA_DEBUG` | off | Show the debug panel by default |
| `NONITA_CERT` / `NONITA_KEY` | `cert.pem` / `key.pem` | HTTPS certificate and key paths |

Host and port can also be changed in the Connection panel and saved per browser (stored in that browser's `localStorage` only).

### HTTPS (elliptic curve only)

```bash
uv run nonita gen-cert --san nonita.lan --san 192.168.1.50   # add the names/IPs you will browse to
```

- Creates a self-signed **ECDSA P-384 / SHA-384** certificate and key (`key.pem` mode 600), valid 365 days.
- **RSA keys are refused at startup.** Bring your own certificate if you prefer (e.g. from a private CA), but its key must be elliptic-curve.
- TLS 1.2 or newer, with only ECDHE + AES-GCM / ChaCha20-Poly1305 suites (forward secrecy; TLS 1.3 suites are always available). HSTS is sent over HTTPS.
- Without a certificate the app runs over plain HTTP **and passwords travel unencrypted**; it logs a warning.

## Security model

- All `/api/*` routes require a login session (HttpOnly, `SameSite=Strict`, `Secure` over HTTPS cookie, 12 h lifetime). Cross-origin POSTs are rejected.
- Passwords are stored as salted scrypt hashes in `nonita_auth.db` (git-ignored, mode 600). Five failed logins lock that user and client for 60 s.
- A fresh install always has the well-known `admin`/`admin` account until its first login changes it, so **do not expose a new install to an untrusted network before you have signed in once.** The default bind address is `127.0.0.1` for this reason.

## Limitations

- One account (`admin`) and one shared history database (`nonita_history.db`): everyone who signs in sees all saved conversations. There is no user management UI beyond changing the password, and no 2FA.
- Login throttling is in memory and per process; sessions persist across restarts.
- Self-signed certificates produce a browser warning until you trust the certificate.
- Attachments are limited to text and PDFs; scanned PDFs are not OCR'd. Images are not supported.
- Web search uses the unofficial DuckDuckGo scraper (`ddgs`) and can be rate-limited or fail; the UI tells you when it returns nothing.
- Only talks to Ollama's HTTP API (which itself has no auth; the connection from Nonita to Ollama is whatever scheme you configure).
- Context length and quality depend on the model and your Ollama settings.
- Markdown rendering uses vendored `marked` and `DOMPurify`; no CDN access is needed.

## Development

```bash
uv run --extra dev pytest
```

Design notes: [`doc/decisions.md`](doc/decisions.md). Changes: [`doc/CHANGELOG.md`](doc/CHANGELOG.md).

## License

[MIT](LICENSE) © 2026 AM. Contact: Telegram [@augustmd](https://t.me/augustmd).
