# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/); versions are git tags.

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
- **Gradio removed.** The app is now a FastAPI backend (`toty_webui/server.py`) serving a plain HTML/CSS/JS page (`toty_webui/static/`).
- Chat replies stream as NDJSON; Stop aborts the request in the browser.
- Token/timing footer is stored separately from the reply text and no longer re-sent to the model.
- Server settings: `TOTY_HOST` / `TOTY_PORT` (legacy `GRADIO_SERVER_*` names still accepted); default port 7860.
- Dependencies: dropped `gradio`; added `fastapi`, `uvicorn`, `python-multipart`.
- Version now has a single source, `toty_webui.__version__`.

### Added
- Footer at the bottom of the screen showing the app version and Telegram contact `@augustmd`.
- `GET /api/meta`, `POST /api/validate-connection` and the rest of the JSON API (see `doc/decisions.md`).
- Vendored `marked` and `DOMPurify` for offline, sanitised markdown rendering.
- `doc/decisions.md` and this changelog.

### Removed
- `toty_webui/app.py` (Gradio UI), Gradio-specific helpers and tests.

## [0.1] — 2026-10-07
### Added
- Shared host/port validation (`connection.py`): trims input, accepts full URLs, rejects ports outside 1–65535.
- Browser-local connection settings (Save / Reset / Forget) stored in `localStorage` (`toty-webui.settings.v1`).
- Mobile layout (stacked rows, 44px touch targets, no horizontal scroll at 320px).
- Upload size cap (`TOTY_MAX_UPLOAD_MB`, default 20), clearer PDF read errors, `OLLAMA_TIMEOUT`.
- Automated tests and a README; project placed under git.

### Fixed
- Default Ollama host mismatch between code and docs; `127.0.0.1` is now the single default.
