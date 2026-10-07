"""HTTP client for Ollama's REST API."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from toty_webui.config import DEFAULT_PORT, DEFAULT_TIMEOUT
from toty_webui.connection import parse_connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatStreamEvent:
    """One event from Ollama's streaming /api/chat response."""

    text: str | None = None
    thinking: str | None = None
    done: bool = False
    stats: dict[str, Any] | None = None


@dataclass(frozen=True)
class OllamaClient:
    """Thin wrapper around Ollama HTTP endpoints."""

    base_url: str
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_host(cls, host: str, port: int = DEFAULT_PORT, timeout: float = DEFAULT_TIMEOUT) -> OllamaClient:
        base, err = parse_connection(host, port)
        if err or base is None:
            raise ValueError(err)
        return cls(base_url=base, timeout=timeout)

    def health_ok(self) -> bool:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=min(self.timeout, 10.0))
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def get_version(self) -> str:
        """Return the Ollama server version string."""
        r = httpx.get(f"{self.base_url}/api/version", timeout=min(self.timeout, 10.0))
        r.raise_for_status()
        return r.json().get("version", "unknown")

    def list_models(self) -> list[str]:
        r = httpx.get(f"{self.base_url}/api/tags", timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        models = data.get("models") or []
        names: list[str] = []
        for m in models:
            name = (m or {}).get("name")
            if isinstance(name, str) and name:
                names.append(name)
        names.sort()
        return names

    def list_models_detailed(self) -> list[dict[str, Any]]:
        """Return full model objects from /api/tags, sorted by name."""
        r = httpx.get(f"{self.base_url}/api/tags", timeout=self.timeout)
        r.raise_for_status()
        models: list[dict[str, Any]] = r.json().get("models") or []
        return sorted(models, key=lambda m: (m or {}).get("name", ""))

    def list_loaded_in_memory(self) -> list[str]:
        r = httpx.get(f"{self.base_url}/api/ps", timeout=min(self.timeout, 30.0))
        r.raise_for_status()
        data = r.json()
        names: list[str] = []
        for m in data.get("models") or []:
            name = (m or {}).get("name")
            if isinstance(name, str) and name:
                names.append(name)
        return names

    def list_loaded_detailed(self) -> list[dict[str, Any]]:
        """Return full running-model objects from /api/ps."""
        r = httpx.get(f"{self.base_url}/api/ps", timeout=min(self.timeout, 30.0))
        r.raise_for_status()
        return r.json().get("models") or []

    def unload_model(self, model: str) -> None:
        r = httpx.post(
            f"{self.base_url}/api/generate",
            json={"model": model, "keep_alive": 0},
            timeout=min(self.timeout, 30.0),
        )
        r.raise_for_status()

    def show_model_info(self, model: str) -> dict[str, Any]:
        """Fetch detailed model info from /api/show (context length, arch, quant, etc.)."""
        r = httpx.post(
            f"{self.base_url}/api/show",
            json={"model": model, "verbose": True},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def unload_all_loaded(self) -> tuple[list[str], list[str]]:
        unloaded: list[str] = []
        failed: list[str] = []
        for name in self.list_loaded_in_memory():
            try:
                self.unload_model(name)
                unloaded.append(name)
            except httpx.HTTPError:
                logger.exception("unload failed for %s", name)
                failed.append(name)
        return unloaded, failed

    def stream_chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        options: dict[str, Any] | None = None,
        think: bool | str | None = None,
        should_abort: Callable[[], bool] | None = None,
    ) -> Iterator[ChatStreamEvent]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if options:
            payload["options"] = options
        if think is not None:
            # `think=False` is still sent explicitly (not omitted): some models
            # (e.g. gpt-oss) think by default even when `think` is left out of
            # the request entirely, so turning it off requires sending `false`.
            payload["think"] = think

        with httpx.Client(timeout=self.timeout) as client:
            with client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    # Surface Ollama's own error message (e.g. "<model> does not
                    # support thinking") instead of a generic HTTP status string.
                    response.read()
                    try:
                        detail = response.json().get("error") or response.text
                    except (json.JSONDecodeError, ValueError):
                        detail = response.text
                    raise httpx.HTTPStatusError(
                        f"Ollama error ({response.status_code}): {detail}",
                        request=response.request,
                        response=response,
                    )
                for line in response.iter_lines():
                    if should_abort and should_abort():
                        break
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        logger.debug("Skip non-JSON line: %s", line[:200])
                        continue

                    msg = evt.get("message") or {}
                    chunk = msg.get("content")
                    thinking_chunk = msg.get("thinking")
                    if (isinstance(chunk, str) and chunk) or (isinstance(thinking_chunk, str) and thinking_chunk):
                        yield ChatStreamEvent(
                            text=chunk if isinstance(chunk, str) else None,
                            thinking=thinking_chunk if isinstance(thinking_chunk, str) else None,
                        )

                    if evt.get("done"):
                        yield ChatStreamEvent(done=True, stats=evt)
                        break


def gradio_content_to_text(content: Any) -> str:
    """Normalize Gradio Chatbot `content` (string or multimodal parts) to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item.get("text", "")))
        return "\n".join(p for p in parts if p)
    return str(content)


def gradio_messages_to_ollama(history: list[Any] | None) -> list[dict[str, str]]:
    """Convert Gradio 5 Chatbot message list to Ollama /api/chat messages."""
    messages: list[dict[str, str]] = []
    for msg in history or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in ("user", "assistant", "system"):
            continue
        text = gradio_content_to_text(msg.get("content")).strip()
        if text:
            messages.append({"role": str(role), "content": text})
    return messages
