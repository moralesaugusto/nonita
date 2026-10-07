"""FastAPI server: JSON/NDJSON API plus the static HTML front end."""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nonita import __version__
from nonita import history as history_store
from nonita.config import (
    DEFAULT_HOST,
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


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


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


class SaveReq(BaseModel):
    conv_id: int | None = None
    model: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)


class ExportReq(BaseModel):
    model: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)


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


def main() -> None:
    host = server_host()
    port = server_port()
    ssl_kwargs = ssl_launch_kwargs()
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting %s v%s on %s:%s (Ollama default host %s)", APP_TITLE, __version__, host, port, DEFAULT_HOST)
    if ssl_kwargs:
        logger.info("HTTPS enabled (cert.pem + key.pem found in project root)")
    else:
        logger.info("HTTP mode — place cert.pem and key.pem in the project root for HTTPS")
    uvicorn.run(app, host=host, port=port, log_level="info", **ssl_kwargs)


if __name__ == "__main__":
    main()
