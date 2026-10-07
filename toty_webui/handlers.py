"""Event handlers and chat logic for the web UI."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from collections.abc import Iterator
from typing import Any

from toty_webui.config import env_timeout
from toty_webui.connection import parse_connection
from toty_webui.client import OllamaClient, messages_to_ollama
from toty_webui.files import ingest_upload_paths, normalize_upload_paths
from toty_webui.metrics import (
    ChatTurnMetrics,
    RequestDebugInfo,
    empty_metrics_display,
    format_debug_panel,
    format_live_metrics,
    format_metrics_summary,
)
from toty_webui.search import format_search_context, web_search

logger = logging.getLogger(__name__)


def _text(val: str | float | None) -> str:
    """Request fields may be None for empty inputs; normalize to str."""
    if val is None:
        return ""
    return str(val)


def _client_from_inputs(host: str | None, port: str | int | float | None) -> OllamaClient | str:
    base, err = parse_connection(host, port)
    if err or base is None:
        return err or "Invalid connection settings."
    return OllamaClient(base_url=base, timeout=env_timeout())


_THINK_LEVELS = {"low", "medium", "high"}


def _normalize_think(value: str | None) -> bool | str:
    """UI label ("Off"/"Low"/"Medium"/"High") -> Ollama's `think` value.

    "Off" resolves to `False` (sent explicitly), not omitted — some models
    (e.g. gpt-oss) think by default even when `think` isn't in the request at all.
    """
    level = _text(value).strip().lower()
    return level if level in _THINK_LEVELS else False


def connection_badge(client: OllamaClient, *, ok: bool | None = None) -> str:
    reachable = client.health_ok() if ok is None else ok
    if reachable:
        return f"🟢 **Connected** — `{client.base_url}`"
    return f"🔴 **Unreachable** — cannot reach `{client.base_url}`"


def fetch_models(
    host: str,
    port: str | int | float,
    current_model: str | None = None,
) -> tuple[list[str], str, str]:
    """Return (choices, status_note, connection_markdown)."""
    client_or_err = _client_from_inputs(host, port)
    if isinstance(client_or_err, str):
        return [], client_or_err, "⚪ **Not connected** — fix host/port and refresh."

    client = client_or_err
    try:
        models = client.list_models()
        badge = connection_badge(client, ok=True)
        if not models:
            note = (
                f"No models at `{client.base_url}`. "
                "Pull one with `ollama pull …` on the host, then refresh."
            )
            return [], note, badge
        note = f"**{len(models)}** model(s) available at `{client.base_url}`."
        return models, note, badge
    except Exception as exc:  # noqa: BLE001 — show error in UI
        logger.exception("list_models failed")
        badge = connection_badge(client, ok=False)
        return [], f"Could not reach Ollama at `{client.base_url}`: `{exc}`", badge


def pick_model_value(choices: list[str], current: str | None) -> str | None:
    if current and current in choices:
        return current
    return choices[0] if choices else None


def ping_host(host: str, port: str | int | float) -> tuple[str, str]:
    client_or_err = _client_from_inputs(host, port)
    if isinstance(client_or_err, str):
        return client_or_err, "⚪ **Not connected** — enter a valid port."

    client = client_or_err
    ok = client.health_ok()
    note = "Reachable — `/api/tags` responded OK." if ok else "Cannot reach `/api/tags` on that host/port."
    return note, connection_badge(client, ok=ok)


def list_loaded_models(host: str, port: str | int | float) -> str:
    client_or_err = _client_from_inputs(host, port)
    if isinstance(client_or_err, str):
        return client_or_err

    client = client_or_err
    try:
        names = client.list_loaded_in_memory()
        if not names:
            return "No models are currently loaded in memory."
        return "**Loaded in memory:**\n\n" + "\n".join(f"- `{name}`" for name in names)
    except Exception as exc:  # noqa: BLE001 — show error in UI
        logger.exception("list_loaded_models failed")
        return f"Could not list loaded models: `{exc}`"


def unload_model(host: str, port: str | int | float, model: str | None) -> str:
    if not model:
        return "Pick a model to unload."

    client_or_err = _client_from_inputs(host, port)
    if isinstance(client_or_err, str):
        return client_or_err

    client = client_or_err
    try:
        client.unload_model(model)
        return f"Model `{model}` unloaded from memory."
    except Exception as exc:  # noqa: BLE001 — show error in UI
        logger.exception("unload_model failed")
        return f"Could not unload `{model}`: `{exc}`"


def unload_all_loaded_models(host: str, port: str | int | float) -> str:
    client_or_err = _client_from_inputs(host, port)
    if isinstance(client_or_err, str):
        return client_or_err

    client = client_or_err
    try:
        unloaded, failed = client.unload_all_loaded()
        if not unloaded and not failed:
            return "No models are currently loaded in memory."

        note = ""
        if unloaded:
            note += "**Unloaded:**\n\n" + "\n".join(f"- `{name}`" for name in unloaded)
        if failed:
            note += "\n\n**Failed to unload:**\n\n" + "\n".join(f"- `{name}`" for name in failed)
        return note or "No models were unloaded."
    except Exception as exc:  # noqa: BLE001 — show error in UI
        logger.exception("unload_all_loaded_models failed")
        return f"Could not unload loaded models: `{exc}`"


def _turn_footer(m: ChatTurnMetrics) -> str:
    """Compact token + timing footer appended under each reply (markdown)."""
    if m.completion_tokens is None or m.error:
        return ""
    fp: list[str] = []
    if m.prompt_tokens is not None:
        fp.append(f"↑{m.prompt_tokens:,} ↓{m.completion_tokens:,} tok")
    else:
        fp.append(f"↓{m.completion_tokens:,} tok")
    if m.tokens_per_second is not None:
        fp.append(f"{m.tokens_per_second:.1f} tok/s")
    if m.ttft_seconds is not None:
        fp.append(f"TTFT {m.ttft_seconds:.2f}s")
    fp.append(f"⏱ {m.wall_seconds:.2f}s")
    return "  ·  ".join(fp)


def stream_events(
    *,
    message: str | None,
    history: list | None,
    host: str | None,
    port: str | int | float | None,
    model: str | None,
    system_prompt: str | None = None,
    temperature: float | None = None,
    num_ctx: int | float | None = None,
    stream_delay_ms: float | None = None,
    file_paths: list[str] | None = None,
    debug_mode: bool = False,
    search_enabled: bool = False,
    search_max_results: int | float | None = 4,
    think_mode: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Run one chat turn and yield JSON-serialisable events.

    Event types: ``status`` (text), ``search`` (count), ``thinking`` (text chunk), ``text`` (text chunk),
    ``done`` (metrics, debug, footer, usage) and ``error`` (message, plus metrics/debug
    when the failure happened mid-request). Closing the generator (client
    disconnect = Stop) closes the upstream HTTP stream.
    """
    history = history or []
    msg_text = _text(message).strip()

    if not msg_text:
        yield {"type": "error", "message": "Type a message first."}
        return
    if not model:
        yield {"type": "error", "message": "Pick a model from the dropdown or click **Refresh models**."}
        return

    client_or_err = _client_from_inputs(host, port)
    if isinstance(client_or_err, str):
        yield {"type": "error", "message": client_or_err}
        return
    client = client_or_err

    file_ctx = ingest_upload_paths(file_paths)
    attachment_count = len(file_paths) if file_paths else 0

    search_ctx = ""
    search_result_count = 0
    if search_enabled:
        # Search is synchronous and network-bound (bounded by search.HARD_TIMEOUT_SECONDS).
        yield {"type": "status", "text": "🔍 **Searching the web…**"}
        try:
            max_results = int(search_max_results) if search_max_results else 4
        except (TypeError, ValueError):
            max_results = 4
        results = web_search(msg_text, max_results=max(1, max_results))
        search_result_count = len(results)
        search_ctx = format_search_context(msg_text, results)
        # Only the count is reported to the UI — never the results themselves.
        yield {"type": "search", "count": search_result_count}

    sys_text = _text(system_prompt).strip()
    messages: list[dict[str, str]] = []
    if sys_text:
        messages.append({"role": "system", "content": sys_text})
    if file_ctx:
        messages.append({"role": "system", "content": file_ctx})
    if search_ctx:
        # Ephemeral: sent to Ollama for this turn only, never echoed to the client,
        # so it is never shown, saved, or exported.
        messages.append({"role": "system", "content": search_ctx})
    messages.extend(messages_to_ollama(history))
    messages.append({"role": "user", "content": msg_text})

    temp_value = float(temperature if temperature is not None else 0.7)
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        port_int = 0
    think = _normalize_think(think_mode)

    request_info = RequestDebugInfo(
        model=str(model),
        temperature=temp_value,
        message_count=len(messages),
        request_char_count=sum(len(m["content"]) for m in messages),
        system_prompt_chars=len(sys_text),
        file_context_chars=len(file_ctx),
        attachment_count=attachment_count,
        search_context_chars=len(search_ctx),
        search_result_count=search_result_count,
        think_mode=think or "off",
        host=_text(host).strip(),
        port=port_int,
    )

    opts: dict[str, Any] = {"temperature": temp_value}
    try:
        ctx_int = int(num_ctx) if num_ctx is not None else None
    except (TypeError, ValueError):
        ctx_int = None
    if ctx_int and ctx_int > 0:
        opts["num_ctx"] = ctx_int
    try:
        delay_s = float(stream_delay_ms) / 1000.0 if stream_delay_ms else 0.0
    except (TypeError, ValueError):
        delay_s = 0.0

    started = time.perf_counter()
    first_token_at: float | None = None
    raw_stats: dict[str, Any] | None = None

    try:
        for event in client.stream_chat(model=model, messages=messages, options=opts, think=think):
            if event.thinking:
                if delay_s > 0:
                    time.sleep(delay_s)
                yield {"type": "thinking", "text": event.thinking}
            if event.text:
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                if delay_s > 0:
                    time.sleep(delay_s)
                yield {"type": "text", "text": event.text}
            if event.done and event.stats:
                raw_stats = event.stats

        metrics = ChatTurnMetrics.from_ollama(
            model=str(model),
            wall_seconds=time.perf_counter() - started,
            ttft_seconds=(first_token_at - started) if first_token_at is not None else None,
            raw_stats=raw_stats,
            aborted=False,
            request=request_info,
        )
        yield {
            "type": "done",
            "metrics": format_metrics_summary(metrics),
            "debug": format_debug_panel(metrics) if debug_mode else "",
            "footer": _turn_footer(metrics),
            "usage": {
                "prompt": metrics.prompt_tokens or 0,
                "completion": metrics.completion_tokens or 0,
            },
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("stream_chat failed")
        metrics = ChatTurnMetrics.from_ollama(
            model=str(model),
            wall_seconds=time.perf_counter() - started,
            ttft_seconds=(first_token_at - started) if first_token_at else None,
            raw_stats=raw_stats,
            aborted=False,
            error=str(exc),
            request=request_info,
        )
        yield {
            "type": "error",
            "message": f"**Request failed:** `{exc}`",
            "metrics": format_metrics_summary(metrics),
            "debug": format_debug_panel(metrics) if debug_mode else "",
        }


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.0f} MB"
    return f"{n / 1024:.0f} KB"


def _fmt_expiry(expires_at: str | None) -> str:
    if not expires_at:
        return ""
    try:
        # Python < 3.11 doesn't parse sub-second offsets in all formats; strip sub-seconds
        ts = expires_at.split(".")[0]
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts).astimezone(timezone.utc)
        delta = dt - datetime.now(timezone.utc)
        secs = int(delta.total_seconds())
        if secs <= 0:
            return "expired"
        if secs < 60:
            return f"expires in {secs}s"
        return f"expires in {secs // 60}m {secs % 60:02d}s"
    except Exception:  # noqa: BLE001
        return ""


def _model_detail_line(m: dict) -> str:
    details = m.get("details") or {}
    parts: list[str] = []
    param = details.get("parameter_size")
    if param:
        parts.append(param)
    quant = details.get("quantization_level")
    if quant:
        parts.append(quant)
    size = _fmt_bytes(m.get("size"))
    parts.append(size)
    family = details.get("family")
    if family:
        parts.append(family)
    mod = m.get("modified_at", "")
    if mod:
        parts.append(mod[:10])  # date portion only
    return " · ".join(parts)


def ollama_server_info(host: str, port: str | int | float) -> str:
    """Return a rich markdown summary of the Ollama server environment."""
    client_or_err = _client_from_inputs(host, port)
    if isinstance(client_or_err, str):
        return client_or_err

    client = client_or_err
    lines: list[str] = [f"### Ollama server — `{client.base_url}`\n"]

    # Version
    try:
        version = client.get_version()
        lines.append(f"**Version:** `{version}`")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"**Version:** could not fetch — `{exc}`")

    lines.append("")

    # Loaded models (VRAM / RAM state) — most actionable, show first
    loaded_names: set[str] = set()
    try:
        loaded = client.list_loaded_detailed()
        loaded_names = {m.get("name", "") for m in loaded}
        if loaded:
            lines.append(f"**Loaded in memory ({len(loaded)}):**")
            for m in loaded:
                name = m.get("name", "?")
                details = m.get("details") or {}
                param = details.get("parameter_size", "")
                quant = details.get("quantization_level", "")
                vram = _fmt_bytes(m.get("size"))
                until_expiry = _fmt_bytes(m.get("size_until_expiry"))
                expiry_str = _fmt_expiry(m.get("expires_at"))

                meta: list[str] = []
                if param:
                    meta.append(param)
                if quant:
                    meta.append(quant)
                meta.append(f"{vram} VRAM")
                if until_expiry != vram:
                    meta.append(f"{until_expiry} kept")
                if expiry_str:
                    meta.append(expiry_str)
                lines.append(f"- **`{name}`** — {' · '.join(meta)}")
        else:
            lines.append("**Loaded in memory:** none")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"**Loaded models:** could not fetch — `{exc}`")

    lines.append("")

    # All available models
    try:
        models = client.list_models_detailed()
        lines.append(f"**Available models ({len(models)}):**")
        for m in models:
            name = m.get("name", "?")
            tag = " 🟢" if name in loaded_names else ""
            lines.append(f"- **`{name}`**{tag} — {_model_detail_line(m)}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"**Models:** could not fetch — `{exc}`")

    return "\n".join(lines)


def show_model_info(host: str, port: str | int | float, model: str | None) -> str:
    """Return a formatted markdown string with detailed info for *model*."""
    if not model:
        return "_Pick a model from the dropdown first._"

    client_or_err = _client_from_inputs(host, port)
    if isinstance(client_or_err, str):
        return client_or_err

    client = client_or_err
    try:
        info = client.show_model_info(model)
    except Exception as exc:  # noqa: BLE001
        logger.exception("show_model_info failed")
        return f"Could not fetch info for `{model}`: `{exc}`"

    lines: list[str] = [f"### `{model}`\n"]

    # Loaded-in-memory status
    try:
        loaded = client.list_loaded_in_memory()
        tag = "🟢 Loaded in memory" if model in loaded else "⚪ Not currently loaded"
        lines.append(f"**Status:** {tag}\n")
    except Exception:  # noqa: BLE001
        pass

    # High-level model details
    details = info.get("details") or {}
    if details:
        lines.append("**Details**")
        if details.get("parameter_size"):
            lines.append(f"- **Parameters:** {details['parameter_size']}")
        if details.get("quantization_level"):
            lines.append(f"- **Quantization:** {details['quantization_level']}")
        if details.get("family"):
            lines.append(f"- **Family:** {details['family']}")
        families = details.get("families")
        if families and isinstance(families, list) and len(families) > 1:
            lines.append(f"- **Families:** {', '.join(families)}")
        if details.get("format"):
            lines.append(f"- **Format:** {details['format']}")
        lines.append("")

    # Parse modelfile parameters to extract num_ctx
    raw_params = (info.get("parameters") or "").strip()
    modelfile_num_ctx: int | None = None
    for line in raw_params.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[0].lower() == "num_ctx":
            try:
                modelfile_num_ctx = int(parts[1])
            except ValueError:
                pass

    # Context window summary — the most actionable section
    model_info = info.get("model_info") or {}
    arch = model_info.get("general.architecture", "")
    arch_ctx = model_info.get(f"{arch}.context_length") or model_info.get("context_length")

    lines.append("**Context window**")
    if arch_ctx is not None:
        lines.append(f"- **Model max (architecture):** {int(arch_ctx):,} tokens")
    if modelfile_num_ctx is not None:
        lines.append(f"- **Effective (modelfile `num_ctx`):** {modelfile_num_ctx:,} tokens")
        if arch_ctx is not None and modelfile_num_ctx < int(arch_ctx):
            lines.append(
                f"  > ⚠️ modelfile caps context at {modelfile_num_ctx:,} — "
                f"model supports up to {int(arch_ctx):,}"
            )
    else:
        lines.append("- **Effective `num_ctx`:** not set in modelfile — **Ollama defaults to 2,048 tokens**")
        if arch_ctx is not None and int(arch_ctx) > 2048:
            lines.append(
                f"  > ⚠️ This model supports up to {int(arch_ctx):,} tokens but Ollama will only use 2,048 "
                "unless you set `num_ctx` in Generation Options or in the modelfile."
            )
    lines.append("")

    # Architecture fields from model_info
    if model_info:
        embed = model_info.get(f"{arch}.embedding_length") or model_info.get("embedding_length")
        layers = model_info.get(f"{arch}.block_count")
        heads = model_info.get(f"{arch}.attention.head_count")
        kv_heads = model_info.get(f"{arch}.attention.head_count_kv")
        param_count = model_info.get("general.parameter_count")

        lines.append("**Architecture**")
        if arch:
            lines.append(f"- **Architecture:** {arch}")
        if param_count is not None:
            n = int(param_count)
            label = f"{n / 1e9:.2f}B" if n >= 1_000_000_000 else f"{n / 1e6:.1f}M"
            lines.append(f"- **Parameter count:** {label}")
        if embed is not None:
            lines.append(f"- **Embedding size:** {int(embed):,}")
        if layers is not None:
            lines.append(f"- **Layers:** {int(layers)}")
        if heads is not None:
            lines.append(f"- **Attention heads:** {int(heads)}")
        if kv_heads is not None:
            lines.append(f"- **KV heads:** {int(kv_heads)}")
        lines.append("")

    if raw_params:
        lines.append("**Modelfile parameters**")
        lines.append("```")
        lines.append(raw_params)
        lines.append("```")

    return "\n".join(lines)
