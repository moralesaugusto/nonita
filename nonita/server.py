"""FastAPI server: JSON/NDJSON API plus the static HTML front end."""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nonita import __version__
from nonita import auth
from nonita import history as history_store
from nonita.config import (
    DEFAULT_HOST,
    cert_paths,
    env_debug_mode,
    env_host,
    env_max_upload_bytes,
    env_port,
    server_host,
    server_port,
    ssl_launch_kwargs,
)
from nonita.connection import parse_connection
from nonita.handlers import (
    fetch_models,
    list_loaded_models,
    ollama_server_info,
    ping_host,
    pick_model_value,
    show_model_info,
    stream_events,
    unload_all_loaded_models,
    unload_model,
)
from nonita.metrics import empty_metrics_display

logger = logging.getLogger(__name__)

APP_TITLE = "Nonita Web UI"
TELEGRAM_HANDLE = "@augustmd"
TELEGRAM_URL = "https://t.me/augustmd"

STATIC_DIR = Path(__file__).resolve().parent / "static"
UPLOAD_DIR = Path(tempfile.gettempdir()) / "nonita_uploads"
_UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")

app = FastAPI(title=APP_TITLE, version=__version__)

_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


_PUBLIC_API = {"/api/meta", "/api/auth/login", "/api/auth/me", "/api/auth/logout"}
_PASSWORD_CHANGE_API = {"/api/auth/password"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _json_error(status: int, detail: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, **extra})


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.middleware("http")
async def require_login(request, call_next):
    """Everything under /api/ needs a session, except login/meta; a pending password change blocks the rest."""
    path = request.url.path
    if path.startswith("/api/"):
        origin = request.headers.get("origin")
        if request.method not in _SAFE_METHODS and origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            return _json_error(403, "Cross-origin request blocked.")
        if path not in _PUBLIC_API:
            user = auth.session_user(request.cookies.get(auth.COOKIE_NAME))
            if user is None:
                return _json_error(401, "Sign in required.", code="auth_required")
            if user["must_change"] and path not in _PASSWORD_CHANGE_API:
                return _json_error(403, "You must change your password first.", code="password_change_required")
            request.state.user = user
    return await call_next(request)


# ── Request models ────────────────────────────────────────────────────────
class Conn(BaseModel):
    host: str = ""
    port: int | float | str | None = None


class ModelReq(Conn):
    model: str | None = None


class ModelsReq(Conn):
    current_model: str | None = None


class ChatReq(Conn):
    message: str = ""
    history: list[dict[str, Any]] = Field(default_factory=list)
    model: str | None = None
    system_prompt: str = ""
    temperature: float = 0.7
    num_ctx: int | float | None = 0
    stream_delay_ms: float = 0
    upload_ids: list[str] = Field(default_factory=list)
    debug_mode: bool = False
    search_enabled: bool = False
    search_max_results: int = 4
    think_mode: str = "Off"


class LoginReq(BaseModel):
    username: str = ""
    password: str = ""


class PasswordReq(BaseModel):
    current_password: str = ""
    new_password: str = ""


class SaveReq(BaseModel):
    conv_id: int | None = None
    model: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)


class ExportReq(BaseModel):
    model: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)


# ── Authentication ────────────────────────────────────────────────────────
def _set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        auth.COOKIE_NAME, token, max_age=auth.SESSION_SECONDS, httponly=True, samesite="strict",
        secure=request.url.scheme == "https", path="/",
    )


@app.post("/api/auth/login")
def login(req: LoginReq, request: Request) -> Response:
    key = f"{request.client.host if request.client else ''}|{req.username.lower()}"
    wait = auth.retry_after(key)
    if wait:
        return _json_error(429, f"Too many failed attempts. Try again in {wait} s.")
    token, must_change = auth.login(req.username, req.password, request.client.host if request.client else "")
    if token is None:
        return _json_error(401, "Incorrect username or password.")
    response = JSONResponse({"username": req.username, "must_change": must_change})
    _set_cookie(response, request, token)
    return response


@app.post("/api/auth/logout")
def logout(request: Request) -> Response:
    auth.logout(request.cookies.get(auth.COOKIE_NAME))
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


@app.get("/api/auth/me")
def me(request: Request) -> dict[str, Any]:
    user = auth.session_user(request.cookies.get(auth.COOKIE_NAME))
    if user is None:
        return {"authenticated": False}
    return {"authenticated": True, **user}


@app.post("/api/auth/password")
def change_password(req: PasswordReq, request: Request) -> Response:
    token, error = auth.change_password(request.state.user["username"], req.current_password, req.new_password)
    if token is None:
        return _json_error(422, error or "Could not change password.")
    response = JSONResponse({"ok": True})
    _set_cookie(response, request, token)
    return response


# ── Meta / config ─────────────────────────────────────────────────────────
@app.get("/api/meta")
def meta() -> dict[str, str]:
    return {
        "title": APP_TITLE,
        "version": __version__,
        "telegram": TELEGRAM_HANDLE,
        "telegram_url": TELEGRAM_URL,
    }


@app.get("/api/config")
def config() -> dict[str, Any]:
    metrics_empty, _ = empty_metrics_display()
    return {
        "host": env_host(),
        "port": env_port(),
        "default_host": DEFAULT_HOST,
        "debug": env_debug_mode(),
        "metrics_empty": metrics_empty,
        "max_upload_bytes": env_max_upload_bytes(),
    }


# ── Ollama connection / models ────────────────────────────────────────────
@app.post("/api/models")
def models(req: ModelsReq) -> dict[str, Any]:
    choices, note, badge = fetch_models(req.host, req.port, req.current_model)
    return {
        "choices": choices,
        "selected": pick_model_value(choices, req.current_model),
        "note": note,
        "badge": badge,
    }


@app.post("/api/validate-connection")
def validate_connection(req: Conn) -> dict[str, Any]:
    _, err = parse_connection(req.host, req.port)
    return {"ok": err is None, "error": err}


@app.post("/api/ping")
def ping(req: Conn) -> dict[str, str]:
    note, badge = ping_host(req.host, req.port)
    return {"note": note, "badge": badge}


@app.post("/api/server-info")
def server_info(req: Conn) -> dict[str, str]:
    return {"markdown": ollama_server_info(req.host, req.port)}


@app.post("/api/model-info")
def model_info(req: ModelReq) -> dict[str, str]:
    return {"markdown": show_model_info(req.host, req.port, req.model)}


@app.post("/api/loaded")
def loaded(req: Conn) -> dict[str, str]:
    return {"markdown": list_loaded_models(req.host, req.port)}


@app.post("/api/unload")
def unload(req: ModelReq) -> dict[str, str]:
    return {"markdown": unload_model(req.host, req.port, req.model)}


@app.post("/api/unload-all")
def unload_all(req: Conn) -> dict[str, str]:
    return {"markdown": unload_all_loaded_models(req.host, req.port)}


# ── Uploads ───────────────────────────────────────────────────────────────
@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    limit = env_max_upload_bytes()
    saved: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for item in files:
        name = Path(item.filename or "upload").name or "upload"
        upload_id = uuid.uuid4().hex
        folder = UPLOAD_DIR / upload_id
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / name
        size = 0
        too_big = False
        with dest.open("wb") as fh:
            while chunk := await item.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    too_big = True
                    break
                fh.write(chunk)
        if too_big:
            shutil.rmtree(folder, ignore_errors=True)
            rejected.append(
                {"name": name, "reason": f"over the {limit / 1_048_576:.0f} MB upload limit (NONITA_MAX_UPLOAD_MB)"}
            )
            continue
        saved.append({"id": upload_id, "name": name, "size": size})
    return {"files": saved, "rejected": rejected}


def _resolve_uploads(ids: list[str]) -> list[str]:
    paths: list[str] = []
    for upload_id in ids:
        if not _UPLOAD_ID.match(upload_id):
            continue  # ignore anything that is not an id we issued (no path traversal)
        folder = UPLOAD_DIR / upload_id
        if folder.is_dir():
            paths.extend(str(p) for p in sorted(folder.iterdir()) if p.is_file())
    return paths


# ── Chat (NDJSON stream) ──────────────────────────────────────────────────
@app.post("/api/chat")
def chat(req: ChatReq) -> StreamingResponse:
    def gen():
        for event in stream_events(
            message=req.message,
            history=req.history,
            host=req.host,
            port=req.port,
            model=req.model,
            system_prompt=req.system_prompt,
            temperature=req.temperature,
            num_ctx=req.num_ctx,
            stream_delay_ms=req.stream_delay_ms,
            file_paths=_resolve_uploads(req.upload_ids),
            debug_mode=req.debug_mode,
            search_enabled=req.search_enabled,
            search_max_results=req.search_max_results,
            think_mode=req.think_mode,
        ):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Saved conversations ───────────────────────────────────────────────────
@app.get("/api/sessions")
def list_sessions() -> list[dict[str, Any]]:
    return [{"id": cid, "label": label} for label, cid in history_store.list_conversation_choices()]


@app.post("/api/sessions")
def save_session(req: SaveReq) -> dict[str, Any]:
    if not req.messages:
        raise HTTPException(status_code=422, detail="Nothing to save — chat is empty.")
    return {"id": history_store.save_conversation(req.conv_id, req.model, req.messages)}


@app.get("/api/sessions/{conv_id}")
def get_session(conv_id: int) -> dict[str, Any]:
    convo = history_store.get_conversation(conv_id)
    if convo is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"id": conv_id, "model": convo["model"], "messages": convo["messages"]}


@app.delete("/api/sessions/{conv_id}")
def delete_session(conv_id: int) -> dict[str, bool]:
    return {"deleted": history_store.delete_conversation(conv_id)}


@app.delete("/api/sessions")
def wipe_sessions(confirm: bool = False) -> dict[str, int]:
    if not confirm:
        raise HTTPException(status_code=400, detail="Pass ?confirm=true to delete all conversations.")
    return {"deleted": history_store.wipe_all()}


def _txt_response(path: str | None) -> Response:
    if not path:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    p = Path(path)
    try:
        body = p.read_bytes()
    finally:
        p.unlink(missing_ok=True)
    return Response(
        body,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="nonita_conversation.txt"'},
    )


@app.get("/api/sessions/{conv_id}/export")
def export_session(conv_id: int) -> Response:
    return _txt_response(history_store.export_conversation_to_txt(conv_id))


@app.post("/api/export")
def export_current(req: ExportReq) -> Response:
    if not req.messages:
        raise HTTPException(status_code=422, detail="Nothing to export — chat is empty.")
    return _txt_response(history_store.export_to_txt(req.messages, req.model))


# ── Static front end ──────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.exception_handler(ValueError)
async def value_error_handler(_, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def serve() -> None:
    host = server_host()
    port = server_port()
    ssl_kwargs = ssl_launch_kwargs()
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting %s v%s on %s:%s (Ollama default host %s)", APP_TITLE, __version__, host, port, DEFAULT_HOST)
    if ssl_kwargs:
        logger.info("HTTPS enabled (elliptic-curve certificate %s)", ssl_kwargs["ssl_certfile"])
    else:
        logger.warning("HTTP mode, traffic and passwords are NOT encrypted. Run 'uv run nonita gen-cert' to enable HTTPS.")
    uvicorn.run(app, host=host, port=port, log_level="info", **ssl_kwargs)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="nonita", description=APP_TITLE)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="start the web UI (default)")
    gen = sub.add_parser("gen-cert", help="generate a self-signed ECDSA P-384 certificate and key")
    gen.add_argument("--san", action="append", default=[], metavar="NAME", help="extra DNS name or IP address (repeatable)")
    gen.add_argument("--days", type=int, default=365)
    gen.add_argument("--force", action="store_true", help="overwrite an existing certificate/key")
    sub.add_parser("reset-password", help="reset the admin account to admin/admin (password change forced at next login)")
    args = parser.parse_args(argv)

    if args.command == "gen-cert":
        from nonita.tls import generate_self_signed

        cert, key = cert_paths()
        if (cert.exists() or key.exists()) and not args.force:
            raise SystemExit(f"{cert} or {key} already exists; use --force to overwrite.")
        generate_self_signed(cert, key, args.san, args.days)
        print(f"Wrote {cert} and {key} (ECDSA P-384, SHA-384, valid {args.days} days).")
    elif args.command == "reset-password":
        if input(f"Reset '{auth.DEFAULT_USER}' to the default password? [y/N] ").strip().lower() == "y":
            auth.reset_to_default()
            print(f"'{auth.DEFAULT_USER}' reset to '{auth.DEFAULT_PASSWORD}'; a new password is required at next login.")
    else:
        serve()


if __name__ == "__main__":
    main()
