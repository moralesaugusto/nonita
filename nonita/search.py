"""Ephemeral DuckDuckGo web search.

Results are only ever folded into the outbound Ollama request as a one-turn
system-context block, the same way file attachments are — they are never
appended to the visible chat history, so they're never written to
nonita_history.db, exported, or persisted anywhere.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass

from ddgs import DDGS
from ddgs.exceptions import DDGSException

logger = logging.getLogger(__name__)

# ddgs fans out across several backends internally (~5s per round, possibly several
# rounds) and that can't be fully bounded via its own `timeout=` kwarg alone. This is
# a hard wall-clock cap so a slow/rate-limited network can never stall a chat turn
# longer than this, no matter what ddgs does internally.
HARD_TIMEOUT_SECONDS = 10


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def _run_search(query: str, max_results: int) -> list[dict]:
    return DDGS(timeout=5).text(query, max_results=max_results)


def web_search(query: str, max_results: int = 5) -> list[SearchResult]:
    """Run a DuckDuckGo text search. Returns [] on any failure or timeout — never hangs."""
    query = (query or "").strip()
    if not query:
        return []

    # Not a `with` block on purpose: exiting a ThreadPoolExecutor context waits for
    # all submitted work to finish (shutdown(wait=True)), which would silently
    # re-introduce the hang we're trying to bound below. shutdown(wait=False) lets
    # us return immediately on timeout and leaves the orphaned thread to finish
    # (or time out) on its own — its result is simply discarded.
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ddg-search")
    try:
        future = pool.submit(_run_search, query, max_results)
        raw = future.result(timeout=HARD_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        logger.warning("DuckDuckGo search timed out after %ss (query of %d characters not logged)", HARD_TIMEOUT_SECONDS, len(query))
        return []
    except DDGSException as exc:
        logger.warning("DuckDuckGo search failed: %s", exc)
        return []
    except Exception:  # noqa: BLE001 — search is best-effort, never break the chat turn
        logger.exception("Unexpected error during DuckDuckGo search")
        return []
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    results: list[SearchResult] = []
    for item in raw or []:
        title = str(item.get("title", "")).strip()
        url = str(item.get("href", "")).strip()
        snippet = str(item.get("body", "")).strip()
        if title or snippet:
            results.append(SearchResult(title=title, url=url, snippet=snippet))
    return results


def format_search_context(query: str, results: list[SearchResult]) -> str:
    """Render results as a system-message block. Caller must not persist this string."""
    if not results:
        return ""
    lines = [f"Web search results for: {query}", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.title}")
        if r.url:
            lines.append(f"   {r.url}")
        if r.snippet:
            lines.append(f"   {r.snippet}")
        lines.append("")
    lines.append(
        "Use the search results above to help answer the user's question if they're relevant. "
        "Cite sources by URL when you use them. Ignore them if they aren't relevant."
    )
    return "\n".join(lines)
