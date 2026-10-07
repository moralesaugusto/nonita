"""Event handlers and chat logic for the Gradio UI."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from toty_webui.config import env_timeout
from toty_webui.connection import parse_connection
from toty_webui.client import OllamaClient, gradio_messages_to_ollama
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
    """Gradio sometimes passes None for empty inputs; normalize to str."""
    if val is None:
        return ""
    return str(val)


def _stop_flag(raw: dict | None) -> dict:
    """Shared session dict for cooperative streaming cancel."""
    if isinstance(raw, dict):
        raw.setdefault("abort", False)
        return raw
    return {"abort": False}


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


def _fmt_conv_metrics(state: dict) -> str:
    turns = state.get("turns", 0)
    if turns == 0:
        return ""
    prompt = state.get("prompt", 0)
    completion = state.get("completion", 0)
    total = prompt + completion
    label = "turn" if turns == 1 else "turns"
    parts = [f"**{turns}** {label}"]
    if total:
        parts.append(f"↑{prompt:,} ↓{completion:,} tok")
        parts.append(f"**{total:,} total**")
    return "Session: " + " · ".join(parts)


def stream_reply(
    message: str | None,
    history: list | None,
    host: str | None,
    port: str | int | float | None,
    model: str | None,
    system_prompt: str | None,
    temperature: float | None,
    num_ctx: int | float | None,
    stream_delay_ms: float | None,
    uploaded_files: list[str] | str | None,
    stop_flag: dict | None,
    conv_state: dict | None,
    debug_mode: bool = False,
    search_enabled: bool = False,
    search_max_results: int | float | None = 4,
    think_mode: str | None = None,
):
    history = history or []
    msg_text = _text(message).strip()
    metrics_empty, debug_empty = empty_metrics_display()
    cs = conv_state if isinstance(conv_state, dict) else {"prompt": 0, "completion": 0, "turns": 0}
    conv_display = _fmt_conv_metrics(cs)

    if not msg_text:
        yield history, "", metrics_empty, debug_empty, cs, conv_display
        return

    if not model:
        working = list(history)
        working.append({"role": "user", "content": msg_text})
        working.append(
            {
                "role": "assistant",
                "content": "Pick a model from the dropdown or click **Refresh models**.",
            }
        )
        yield working, "", metrics_empty, debug_empty, cs, conv_display
        return

    client_or_err = _client_from_inputs(host, port)
    if isinstance(client_or_err, str):
        working = list(history)
        working.append({"role": "user", "content": msg_text})
        port_err = (
            "Invalid port — enter a number between 1 and 65535."
            if client_or_err == "Port must be a number."
            else client_or_err
        )
        working.append({"role": "assistant", "content": port_err})
        yield working, "", metrics_empty, debug_empty, cs, conv_display
        return

    client = client_or_err
    paths = normalize_upload_paths(uploaded_files)
    file_ctx = ingest_upload_paths(paths)
    attachment_count = len(paths) if paths else 0

    working = list(history)
    working.append({"role": "user", "content": msg_text})

    search_ctx = ""
    search_result_count = 0
    if search_enabled:
        # Search runs synchronously and can take several seconds (network-bound,
        # bounded by search.HARD_TIMEOUT_SECONDS) — show a status so the UI
        # doesn't look frozen while it's in flight.
        yield working, "", "🔍 **Searching the web…**", "", cs, conv_display
        try:
            max_results = int(search_max_results) if search_max_results else 4
        except (TypeError, ValueError):
            max_results = 4
        results = web_search(msg_text, max_results=max(1, max_results))
        search_result_count = len(results)
        search_ctx = format_search_context(msg_text, results)

    sys_text = _text(system_prompt).strip()
    messages: list[dict[str, str]] = []
    if sys_text:
        messages.append({"role": "system", "content": sys_text})
    if file_ctx:
        messages.append({"role": "system", "content": file_ctx})
    if search_ctx:
        # Ephemeral: only ever sent to Ollama for this one turn, never appended
        # to `working`/history — so it's never shown, saved, or exported.
        messages.append({"role": "system", "content": search_ctx})
    messages.extend(gradio_messages_to_ollama(history))
    messages.append({"role": "user", "content": msg_text})

    request_chars = sum(len(m["content"]) for m in messages)
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
        request_char_count=request_chars,
        system_prompt_chars=len(sys_text),
        file_context_chars=len(file_ctx),
        attachment_count=attachment_count,
        search_context_chars=len(search_ctx),
        search_result_count=search_result_count,
        think_mode=think or "off",
        host=_text(host).strip(),
        port=port_int,
    )

    flag = _stop_flag(stop_flag)
    flag["abort"] = False

    yield working, "", format_live_metrics(0.0), "", cs, conv_display

    assistant = ""
    thinking_acc = ""
    content_idx: int | None = None
    thinking_idx: int | None = None
    thinking_started: float | None = None
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
    aborted = False

    try:

        def should_abort() -> bool:
            return bool(flag.get("abort"))

        for event in client.stream_chat(
            model=model, messages=messages, options=opts, think=think, should_abort=should_abort
        ):
            if event.thinking:
                if thinking_idx is None:
                    working.append(
                        {
                            "role": "assistant",
                            "content": "",
                            "metadata": {"title": "🤔 Thinking…", "status": "pending"},
                        }
                    )
                    thinking_idx = len(working) - 1
                    thinking_started = time.perf_counter()
                thinking_acc += event.thinking
                working[thinking_idx]["content"] = thinking_acc
                elapsed = time.perf_counter() - started
                if delay_s > 0:
                    time.sleep(delay_s)
                yield working, "", format_live_metrics(elapsed), "", cs, conv_display

            if event.text:
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                if thinking_idx is not None and working[thinking_idx]["metadata"]["status"] != "done":
                    think_seconds = time.perf_counter() - (thinking_started or time.perf_counter())
                    working[thinking_idx]["metadata"]["status"] = "done"
                    working[thinking_idx]["metadata"]["title"] = f"🤔 Thought for {think_seconds:.1f}s"
                if content_idx is None:
                    working.append({"role": "assistant", "content": ""})
                    content_idx = len(working) - 1
                assistant += event.text
                working[content_idx]["content"] = assistant
                elapsed = time.perf_counter() - started
                if delay_s > 0:
                    time.sleep(delay_s)
                yield working, "", format_live_metrics(elapsed), "", cs, conv_display

            if event.done and event.stats:
                raw_stats = event.stats

        if thinking_idx is not None and working[thinking_idx]["metadata"]["status"] != "done":
            think_seconds = time.perf_counter() - (thinking_started or time.perf_counter())
            working[thinking_idx]["metadata"]["status"] = "done"
            working[thinking_idx]["metadata"]["title"] = f"🤔 Thought for {think_seconds:.1f}s"
        if content_idx is None:
            working.append({"role": "assistant", "content": ""})
            content_idx = len(working) - 1

        aborted = bool(flag.get("abort"))
        if aborted:
            if assistant:
                assistant = assistant.rstrip() + "\n\n*[generation stopped]*"
            else:
                assistant = "*[generation stopped]*"
            working[content_idx]["content"] = assistant

        wall_seconds = time.perf_counter() - started
        ttft = (first_token_at - started) if first_token_at is not None else None
        turn_metrics = ChatTurnMetrics.from_ollama(
            model=str(model),
            wall_seconds=wall_seconds,
            ttft_seconds=ttft,
            raw_stats=raw_stats,
            aborted=aborted,
            request=request_info,
        )
        # Append compact token + timing footer as a markdown blockquote
        if turn_metrics.completion_tokens is not None and not turn_metrics.error:
            fp: list[str] = []
            if turn_metrics.prompt_tokens is not None:
                fp.append(f"↑{turn_metrics.prompt_tokens:,} ↓{turn_metrics.completion_tokens:,} tok")
            else:
                fp.append(f"↓{turn_metrics.completion_tokens:,} tok")
            if turn_metrics.tokens_per_second is not None:
                fp.append(f"{turn_metrics.tokens_per_second:.1f} tok/s")
            if turn_metrics.ttft_seconds is not None:
                fp.append(f"TTFT {turn_metrics.ttft_seconds:.2f}s")
            fp.append(f"⏱ {turn_metrics.wall_seconds:.2f}s")
            working[content_idx]["content"] = (
                working[content_idx]["content"].rstrip() + "\n\n> *" + "  ·  ".join(fp) + "*"
            )

        new_cs = {
            "prompt": cs.get("prompt", 0) + (turn_metrics.prompt_tokens or 0),
            "completion": cs.get("completion", 0) + (turn_metrics.completion_tokens or 0),
            "turns": cs.get("turns", 0) + 1,
        }
        summary = format_metrics_summary(turn_metrics)
        debug_text = format_debug_panel(turn_metrics) if debug_mode else ""
        yield working, "", summary, debug_text, new_cs, _fmt_conv_metrics(new_cs)

    except Exception as exc:  # noqa: BLE001
        logger.exception("stream_chat failed")
        if content_idx is None:
            # Exception may have fired before any content (or even thinking) arrived
            # — e.g. Ollama rejects `think` immediately for a non-thinking model —
            # so `working` may still end with the user's own message. Never clobber it.
            working.append({"role": "assistant", "content": ""})
            content_idx = len(working) - 1
        working[content_idx]["content"] = f"**Request failed:** `{exc}`"
        wall_seconds = time.perf_counter() - started
        turn_metrics = ChatTurnMetrics.from_ollama(
            model=str(model),
            wall_seconds=wall_seconds,
            ttft_seconds=(first_token_at - started) if first_token_at else None,
            raw_stats=raw_stats,
            aborted=aborted,
            error=str(exc),
            request=request_info,
        )
        summary = format_metrics_summary(turn_metrics)
        debug_text = format_debug_panel(turn_metrics) if debug_mode else ""
        yield working, "", summary, debug_text, cs, conv_display


def clear_chat(stop_flag: dict | None) -> tuple[list[Any], str, dict, str, str, dict, str]:
    flag = _stop_flag(stop_flag)
    flag["abort"] = False
    metrics_empty, debug_empty = empty_metrics_display()
    empty_conv: dict = {"prompt": 0, "completion": 0, "turns": 0}
    return [], "", flag, metrics_empty, debug_empty, empty_conv, ""


def request_stop(stop_flag: dict | None) -> dict:
    flag = _stop_flag(stop_flag)
    flag["abort"] = True
    return flag


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


def insert_example_prompt() -> str:
    return "Summarize the attached files in three bullet points, then suggest logical next steps."
