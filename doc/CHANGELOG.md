# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/); versions are git tags.

## [0.4.0] — 2026-10-07
### Added
- **Authentication:** local accounts in `nonita_auth.db` (scrypt hashes), HttpOnly/SameSite=Strict session cookies, login throttle, same-origin check on writes. All `/api/*` except login/meta require a session.
- Default `admin` / `admin` account that **must** change its password at first login (no length, complexity or must-differ rules; the new password just can't be empty); "Change password" and "Sign out" in the header; `nonita reset-password` for recovery.
- `nonita gen-cert` creates an ECDSA P-384 / SHA-384 self-signed certificate; RSA keys are refused at startup. TLS limited to ECDHE + AEAD ciphers; HSTS over HTTPS.
- `.env` loading (`.env.example` provided); `NONITA_CERT` / `NONITA_KEY`.
- Dependency: `cryptography`.

### Changed
- Private defaults removed from the code: the Ollama host now defaults to `127.0.0.1` and the UI binds to `127.0.0.1` (was `0.0.0.0`). Put your values in `.env`. `nonita/config.txt` removed.

## [Earlier, unreleased]
### Changed
- **Renamed Toty → Nonita.** Package `toty_webui` is now `nonita`, the distribution/command `toty-webui` is now `nonita`.
- Env vars `TOTY_*` are now `NONITA_*`; `toty_history.db` and `toty_conversation*` files are now `nonita_*`; browser storage keys `toty-webui.*` are now `nonita.*` (saved theme/settings reset once). Rename existing `.env` entries and the history DB to keep them.
- **Theme changed from watermelon to noni** (*Morinda citrifolia*, the fruit Nonita is named after): glossy leaf-green primary (`#2f6b3a`), seed-brown accents (`#8a5a22`), cream-green backgrounds, matching dark theme, and a new favicon/brand icon (lumpy pale-green fruit with seed-dotted "eyes" and a leaf). CSS variables `--rind*` renamed `--skin*`.
- Project is run with uv only (`uv sync`, `uv run nonita`); `nonita.sh` fixed to do this.

## [0.3.1] — 2026-10-07
### Fixed
- Web search no longer fails silently: each reply shows "Web search: N results used", or a warning when the search returned nothing (rate-limited, timed out or offline).
- Only the result count is sent to the browser; results stay ephemeral, and the note is not saved or exported.

## [0.3] — 2026-10-07
### Changed
- **UI redesign (watermelon theme):** rind green, flesh red, seed black on cream; light and dark themes (follows the OS, toggle in the header, remembered per browser).
- Professional layout: top bar with brand and connection status pill, full-height workspace (conversation list | chat | settings cards) with the message box always visible, proper chat bubbles, empty state, segmented thinking-mode control, SVG icons instead of emoji.
- Responsive: settings drop below the chat under 1180px; single column under 820px with 44px touch targets and no horizontal scroll.
- Typography moved from a monospace terminal look to the system UI font stack.

### Added
- Accessibility: skip link, labelled controls and live regions, visible focus rings, `prefers-reduced-motion`, 4.5:1+ text contrast in both themes.
- Security headers on every response: Content-Security-Policy (self only), `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`.
- Favicon (watermelon slice), print stylesheet (chat only), "Danger zone" fold for the history wipe.

## [0.2] — 2026-10-07
### Changed
- **Gradio removed.** The app is now a FastAPI backend (`nonita/server.py`) serving a plain HTML/CSS/JS page (`nonita/static/`).
- Chat replies stream as NDJSON; Stop aborts the request in the browser.
- Token/timing footer is stored separately from the reply text and no longer re-sent to the model.
- Server settings: `NONITA_HOST` / `NONITA_PORT` (legacy `GRADIO_SERVER_*` names still accepted); default port 7860.
- Dependencies: dropped `gradio`; added `fastapi`, `uvicorn`, `python-multipart`.
- Version now has a single source, `nonita.__version__`.

### Added
- Footer at the bottom of the screen showing the app version and Telegram contact `@augustmd`.
- `GET /api/meta`, `POST /api/validate-connection` and the rest of the JSON API (see `doc/decisions.md`).
- Vendored `marked` and `DOMPurify` for offline, sanitised markdown rendering.
- `doc/decisions.md` and this changelog.

### Removed
- `nonita/app.py` (Gradio UI), Gradio-specific helpers and tests.

## [0.1] — 2026-10-07
### Added
- Shared host/port validation (`connection.py`): trims input, accepts full URLs, rejects ports outside 1–65535.
- Browser-local connection settings (Save / Reset / Forget) stored in `localStorage` (`nonita.settings.v1`).
- Mobile layout (stacked rows, 44px touch targets, no horizontal scroll at 320px).
- Upload size cap (`NONITA_MAX_UPLOAD_MB`, default 20), clearer PDF read errors, `OLLAMA_TIMEOUT`.
- Automated tests and a README; project placed under git.

### Fixed
- Default Ollama host mismatch between code and docs; the default is now defined in one place.
