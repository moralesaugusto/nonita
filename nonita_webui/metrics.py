"""Format chat timing and token metrics from Ollama responses."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def ns_to_seconds(value: Any) -> float | None:
    """Convert Ollama nanosecond durations to seconds."""
    if value is None:
        return None
    try:
        return float(value) / 1_000_000_000
    except (TypeError, ValueError):
        return None


@dataclass
class RequestDebugInfo:
    """Client-side context about the outbound chat request."""

    model: str
    temperature: float
    message_count: int
    request_char_count: int
    system_prompt_chars: int
    file_context_chars: int
    attachment_count: int
    search_context_chars: int
    search_result_count: int
    think_mode: str
    host: str
    port: int


@dataclass
class ChatTurnMetrics:
    """Metrics for a single chat turn (normal + debug views)."""

    model: str
    wall_seconds: float = 0.0
    ttft_seconds: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    load_seconds: float | None = None
    prompt_eval_seconds: float | None = None
    eval_seconds: float | None = None
    total_duration_seconds: float | None = None
    tokens_per_second: float | None = None
    aborted: bool = False
    done_reason: str | None = None
    error: str | None = None
    raw_stats: dict[str, Any] | None = field(default=None, repr=False)
    request: RequestDebugInfo | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None and self.completion_tokens is None:
            return None
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)

    @classmethod
    def from_ollama(
        cls,
        *,
        model: str,
        wall_seconds: float,
        ttft_seconds: float | None,
        raw_stats: dict[str, Any] | None,
        aborted: bool = False,
        error: str | None = None,
        request: RequestDebugInfo | None = None,
    ) -> ChatTurnMetrics:
        prompt_tokens = None
        completion_tokens = None
        load_seconds = None
        prompt_eval_seconds = None
        eval_seconds = None
        total_duration_seconds = None
        tokens_per_second = None
        done_reason = None

        if raw_stats:
            prompt_tokens = _int_or_none(raw_stats.get("prompt_eval_count"))
            completion_tokens = _int_or_none(raw_stats.get("eval_count"))
            load_seconds = ns_to_seconds(raw_stats.get("load_duration"))
            prompt_eval_seconds = ns_to_seconds(raw_stats.get("prompt_eval_duration"))
            eval_seconds = ns_to_seconds(raw_stats.get("eval_duration"))
            total_duration_seconds = ns_to_seconds(raw_stats.get("total_duration"))
            done_reason = raw_stats.get("done_reason")
            if isinstance(done_reason, str):
                done_reason = done_reason or None
            else:
                done_reason = None

            if eval_seconds and eval_seconds > 0 and completion_tokens:
                tokens_per_second = completion_tokens / eval_seconds

        return cls(
            model=model,
            wall_seconds=wall_seconds,
            ttft_seconds=ttft_seconds,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            load_seconds=load_seconds,
            prompt_eval_seconds=prompt_eval_seconds,
            eval_seconds=eval_seconds,
            total_duration_seconds=total_duration_seconds,
            tokens_per_second=tokens_per_second,
            aborted=aborted,
            done_reason=done_reason,
            error=error,
            raw_stats=raw_stats,
            request=request,
        )


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fmt_seconds(seconds: float | None, *, digits: int = 2) -> str:
    if seconds is None:
        return "—"
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    return f"{seconds:.{digits}f}s"


def format_live_metrics(elapsed_seconds: float, *, streaming: bool = True) -> str:
    label = "streaming…" if streaming else "finishing…"
    return f"⏱ **{_fmt_seconds(elapsed_seconds)}** · {label}"


def format_metrics_summary(metrics: ChatTurnMetrics) -> str:
    """Compact one-line summary shown after every turn."""
    if metrics.error:
        return f"⏱ **{_fmt_seconds(metrics.wall_seconds)}** · **Error** — {metrics.error}"

    parts: list[str] = [f"⏱ **{_fmt_seconds(metrics.wall_seconds)}**"]

    if metrics.ttft_seconds is not None:
        parts.append(f"first token **{_fmt_seconds(metrics.ttft_seconds)}**")

    if metrics.prompt_tokens is not None or metrics.completion_tokens is not None:
        prompt = metrics.prompt_tokens if metrics.prompt_tokens is not None else "?"
        completion = metrics.completion_tokens if metrics.completion_tokens is not None else "?"
        total = metrics.total_tokens
        token_part = f"**{prompt}** prompt → **{completion}** completion"
        if total is not None:
            token_part += f" (**{total}** total)"
        parts.append(token_part)

    if metrics.tokens_per_second is not None:
        parts.append(f"**{metrics.tokens_per_second:.1f} tok/s**")

    if metrics.aborted:
        parts.append("*stopped early*")
    elif metrics.done_reason:
        parts.append(f"`{metrics.done_reason}`")

    return " · ".join(parts)


def format_debug_panel(metrics: ChatTurnMetrics) -> str:
    """Detailed debug view for operators."""
    lines: list[str] = ["#### Last request — debug"]

    if metrics.request:
        req = metrics.request
        lines.extend(
            [
                "**Request**",
                f"- Model: `{req.model}` @ `{req.host}:{req.port}`",
                f"- Temperature: `{req.temperature}`",
                f"- Messages sent: `{req.message_count}`",
                f"- Request size: `{req.request_char_count:,}` chars",
                f"- System prompt: `{req.system_prompt_chars:,}` chars",
                f"- File context: `{req.file_context_chars:,}` chars ({req.attachment_count} file(s))",
                f"- Web search context: `{req.search_context_chars:,}` chars ({req.search_result_count} result(s))",
                f"- Thinking mode: `{req.think_mode}`",
                "",
            ]
        )

    lines.extend(
        [
            "**Timing (wall clock)**",
            f"- Total (UI): `{_fmt_seconds(metrics.wall_seconds)}`",
            f"- Time to first token: `{_fmt_seconds(metrics.ttft_seconds)}`",
            "",
            "**Timing (Ollama)**",
            f"- Total duration: `{_fmt_seconds(metrics.total_duration_seconds)}`",
            f"- Model load: `{_fmt_seconds(metrics.load_seconds)}`",
            f"- Prompt eval: `{_fmt_seconds(metrics.prompt_eval_seconds)}`",
            f"- Generation: `{_fmt_seconds(metrics.eval_seconds)}`",
            "",
            "**Tokens**",
            f"- Prompt tokens: `{metrics.prompt_tokens if metrics.prompt_tokens is not None else '—'}`",
            f"- Completion tokens: `{metrics.completion_tokens if metrics.completion_tokens is not None else '—'}`",
            f"- Throughput: `{f'{metrics.tokens_per_second:.2f} tok/s' if metrics.tokens_per_second else '—'}`",
            "",
            "**Status**",
            f"- Aborted: `{metrics.aborted}`",
            f"- Done reason: `{metrics.done_reason or '—'}`",
        ]
    )

    if metrics.raw_stats:
        pretty = json.dumps(metrics.raw_stats, indent=2, sort_keys=True)
        lines.extend(["", "**Raw Ollama final event**", f"```json\n{pretty}\n```"])

    return "\n".join(lines)


def empty_metrics_display() -> tuple[str, str]:
    return "_No metrics yet — send a message to see timing and token usage._", ""
