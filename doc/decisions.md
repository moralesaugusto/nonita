# Design decisions

## v0.2 — replace Gradio with FastAPI + plain HTML

**Why:** Gradio's generated front end made layout, mobile behaviour, the footer and browser-side storage hard to control (most of v0.1's CSS fought Gradio's internals). A small FastAPI API with a hand-written page gives full control and fewer dependencies.

| Area | Decision | Reason |
|---|---|---|
| Backend | FastAPI served by uvicorn (`python -m toty_webui` / `toty-webui`) | Simple, async-capable, typed request models |
| Front end | `static/index.html` + `app.js` + `style.css`, vanilla JS, no build step | Nothing to compile; easy to edit |
| Markdown | `marked` + `DOMPurify` vendored in `static/vendor/` | Works offline on a LAN; all model/server text is sanitised before insertion |
| Streaming | `POST /api/chat` returns NDJSON events: `status`, `thinking`, `text`, `done`, `error` | Works with plain `fetch` + `ReadableStream`; no WebSocket needed |
| Stop | Browser aborts the fetch; server generator is closed, which closes the upstream Ollama stream | Removes the shared `stop_state` flag; the UI appends `*[generation stopped]*` |
| State | Chat history and generation options live in the browser; the server is stateless except SQLite history | Several browsers can use one instance with different hosts/models |
| Saved settings | Host/port in `localStorage` under `toty-webui.settings.v1` (same key as v0.1) | Per-browser; v0.1 saved values carry over; no secrets stored |
| Reply footer | Token/timing footer is a separate `footer` field, not appended to message text | It is no longer sent back to the model as conversation context |
| Uploads | `POST /api/upload` saves to a temp dir under a random id; chat requests refer to ids; size cap `TOTY_MAX_UPLOAD_MB` | Ids are validated as 32 hex chars, so no path traversal |
| Version | Single source `toty_webui.__version__`, exposed at `GET /api/meta` and shown in the page footer | One place to bump; `pyproject.toml` reads it |
| Footer | Fixed bar: `Toty Web UI vX.Y · Telegram @augustmd` (links to https://t.me/augustmd) | Requested by the owner |
| Config names | `TOTY_HOST` / `TOTY_PORT` (default `0.0.0.0` / `7860`); legacy `GRADIO_SERVER_NAME` / `GRADIO_SERVER_PORT` still read | Existing launch scripts keep working |
| Wipe history | `DELETE /api/sessions?confirm=true` | Destructive call needs an explicit flag |

## Carried over from v0.1
- Bind to `0.0.0.0`; **no authentication** (owner's decision). Use on a trusted LAN only.
- Optional HTTPS when `cert.pem` and `key.pem` sit in the project root (both git-ignored).
- Central host/port validation (`connection.py`), `OLLAMA_TIMEOUT`, upload size cap.
- Default Ollama host `127.0.0.1`, port `11434`.
- Versions are tracked with git tags (`v0.1`, `v0.2`, …).

## API summary
`GET /` · `GET /api/meta` · `GET /api/config` · `POST /api/validate-connection` · `POST /api/models` · `/api/ping` · `/api/server-info` · `/api/model-info` · `/api/loaded` · `/api/unload` · `/api/unload-all` · `POST /api/upload` · `POST /api/chat` · `GET|POST /api/sessions` · `GET|DELETE /api/sessions/{id}` · `GET /api/sessions/{id}/export` · `POST /api/export` · `DELETE /api/sessions?confirm=true`

## v0.3 — watermelon theme, enterprise polish

| Area | Decision | Reason |
|---|---|---|
| Palette | Flesh red `#d62839` (primary actions), rind green `#1f7a4d` (accents, header rule, assistant label), seed-black text, cream background; dark theme uses lighter tints (`#ff6b7d`, `#52c68d`) on deep green-black | Watermelon identity with AA contrast in both themes |
| Theme | CSS custom properties; `data-theme` on `<html>`; default from `prefers-color-scheme`; choice in `localStorage` (`toty-webui.theme`) | No flash-heavy JS, per-user preference |
| Type & icons | System UI font stack, inline SVG icon sprite | No external font/CDN requests; consistent rendering |
| Layout | App frame fills the viewport; chat scrolls internally so the composer never leaves the screen; side panels scroll independently | Fixes the v0.2 page overflowing the screen |
| Security headers | CSP `default-src 'self'` (no inline scripts; inline styles allowed only for markdown output), nosniff, frame deny, no-referrer | Reasonable enterprise baseline for a LAN tool; authentication remains intentionally out of scope |
| Accessibility | Semantic landmarks, labels, live regions, focus-visible, reduced motion | Enterprise/WCAG expectations |
