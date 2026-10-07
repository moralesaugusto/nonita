# Privacy and data-flow findings

**Author:** gpt-5.6-luna  
**Review date:** 2026-10-07  
**Scope:** source, bundled front-end assets, configuration, and dependency declarations in this repository.

## Conclusion

No covert analytics, telemetry, tracking pixel, external JavaScript include, shell command, or background upload path was found in the reviewed implementation. The browser API calls are same-origin, and the default Ollama endpoint is `http://127.0.0.1:11434`.

However, the implementation cannot be described as unconditionally “purely local.” It intentionally supports outbound network traffic, and privacy depends on configuration and user choices. In particular, web search sends the complete chat message to DuckDuckGo when enabled, and chat content/attachments are sent to whichever Ollama host the user configures.

## Findings

### High — opt-in web search discloses the full message

The web-search checkbox is opt-in, but when enabled `stream_events()` passes the complete trimmed user message to `web_search()` (`nonita/handlers.py`, search branch; `nonita/search.py:34-35`). The `ddgs` dependency performs network search, so the message is disclosed to DuckDuckGo/search backends. A timeout warning also logs the complete query locally (`nonita/search.py:53-55`).

This is documented in the UI (`nonita/static/index.html:205-211`) and is not covert exfiltration, but it is incompatible with a strict offline-only privacy claim. Keep web search disabled for sensitive prompts, or remove the feature/dependency for an enforced offline mode.

### High — user-configurable Ollama endpoint receives chat data and attachments

The connection parser accepts arbitrary hostnames/IPs and explicit `http://` or `https://` URLs (`nonita/connection.py:25-50`). The client sends messages, conversation history, system prompts, extracted attachment text, and optional search context to that endpoint (`nonita/client.py:123-149`; `nonita/handlers.py` chat-message construction). A remote or untrusted Ollama endpoint can therefore receive all of that content by design.

For local-only operation, restrict the endpoint to loopback and do not expose a remote Ollama URL. If a remote endpoint is intentional, use HTTPS and treat it as a data processor.

### Medium — default server and Ollama traffic are HTTP unless TLS is configured

The default UI binds to `127.0.0.1:7860`, which limits network exposure on the default machine. The default Ollama connection is plain HTTP, and the server explicitly warns that HTTP mode does not encrypt traffic (`nonita/server.py:429-439` and `.env.example`). This is acceptable for loopback but exposes passwords, prompts, attachments, and responses to network observers if the UI or Ollama host is reachable over a network.

Use the provided certificate configuration for HTTPS when serving beyond loopback, and use an `https://` Ollama URL for remote inference.

### Medium — uploaded files remain in a temporary directory after successful use

Uploads are written to `tempfile.gettempdir()/nonita_uploads/<id>/` (`nonita/server.py:55-57`, `290-312`). Oversized uploads are removed, but successful upload directories are not removed when the user clears the attachment list or after a chat turn. Their contents therefore remain on local disk until external temporary-file cleanup or manual removal. The files are not sent anywhere unless included in a chat request, at which point their extracted text is sent to the configured Ollama endpoint.

Consider deleting successful upload directories after the turn, adding explicit user-controlled deletion, and applying restrictive file permissions.

### Low — conversation history is local but opt-in and contains sensitive content

Saved conversations are written to `nonita_history.db` only through explicit save actions (`nonita/history.py:47-65`). Exports are written to a local temporary file and removed after response (`nonita/history.py:137-153`; `nonita/server.py:388-400`). This is local persistence, not exfiltration, but saved prompts and responses remain sensitive data. Unlike the auth database, the history database is not explicitly chmod'ed to owner-only permissions.

Protect the project directory and consider setting `nonita_history.db` to mode `0600` when created.

### Informational — external Telegram link is manual navigation only

The footer contains a user-clickable link to `https://t.me/augustmd` (`nonita/static/index.html:217-226`; `nonita/server.py:51-53`). No automatic navigation or data transmission to Telegram was found. The link is still an external destination users should recognize.

## Positive controls observed

- No analytics or telemetry identifiers/calls were found.
- Front-end scripts and libraries are served locally; the CSP uses `connect-src 'self'` and no remote script source.
- Browser local storage stores only the selected host/port and theme, not prompts or passwords (`nonita/static/app.js:210-225`, `410-415`).
- Session cookies are `HttpOnly` and `SameSite=Strict`; password hashes use `scrypt`; auth database permissions are tightened to `0600` (`nonita/server.py:151-158`, `nonita/auth.py:42-48`, `71-83`).
- Search results are not added to saved/exported chat history, but that does not prevent the search provider or configured Ollama host from seeing data during the request.

## Verification limits

This is a source-level review, not a guarantee about the host operating system, compromised dependencies, reverse proxies, browser extensions, DNS, or a malicious configured Ollama server. The repository’s tests could not be executed in this environment because `pytest` is not installed.
