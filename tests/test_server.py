import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nonita import __version__, auth, history, server
from nonita.client import ChatStreamEvent, OllamaClient

STATIC = Path(server.STATIC_DIR)


@pytest.fixture()
def client(tmp_path, monkeypatch, request):
    monkeypatch.setattr(history, "DB_PATH", tmp_path / "h.db")
    monkeypatch.setattr(server, "UPLOAD_DIR", tmp_path / "up")
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "_SCRYPT_N", 2**10)  # fast hashing in tests
    monkeypatch.setattr(auth, "_fails", {})
    c = TestClient(server.app)
    if getattr(request, "param", "login") != "anon":
        r = c.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert r.json()["must_change"] is True
        r = c.post("/api/auth/password", json={"current_password": "admin", "new_password": "correct-horse-9"})
        assert r.status_code == 200
    return c


def test_meta_has_version_and_telegram(client):
    r = client.get("/api/meta").json()
    assert r["version"] == __version__ == "0.4.0"
    assert r["telegram"] == "@augustmd" and r["telegram_url"] == "https://t.me/augustmd"


def test_index_and_static(client):
    html = client.get("/").text
    assert 'id="app-version"' in html and "t.me/augustmd" in html and "gradio" not in html.lower()
    assert client.get("/static/app.js").status_code == 200
    css = (STATIC / "style.css").read_text()
    assert "@media (max-width: 820px)" in css and "min-height: 44px" in css
    assert '[data-theme="dark"]' in css and "prefers-reduced-motion" in css


def test_security_headers(client):
    r = client.get("/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/meta").headers["x-frame-options"] == "DENY"


def test_validate_connection(client):
    assert client.post("/api/validate-connection", json={"host": "h", "port": 11434}).json()["ok"]
    bad = client.post("/api/validate-connection", json={"host": "h", "port": 70000}).json()
    assert not bad["ok"] and "1 to 65535" in bad["error"]


def test_models_endpoint(client, monkeypatch):
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["a", "b"])
    r = client.post("/api/models", json={"host": "h", "port": 1, "current_model": "b"}).json()
    assert r["choices"] == ["a", "b"] and r["selected"] == "b" and "Connected" in r["badge"]


def _chat(client, **over):
    body = {"host": "h", "port": 1, "message": "hi", "model": "m", **over}
    with client.stream("POST", "/api/chat", json=body) as resp:
        return [json.loads(line) for line in resp.iter_lines() if line]


def test_chat_stream_events(client, monkeypatch):
    def fake(self, **kw):
        yield ChatStreamEvent(thinking="hmm")
        yield ChatStreamEvent(text="Hel")
        yield ChatStreamEvent(text="lo")
        yield ChatStreamEvent(done=True, stats={"prompt_eval_count": 3, "eval_count": 2})

    monkeypatch.setattr(OllamaClient, "stream_chat", fake)
    ev = _chat(client)
    assert [e["type"] for e in ev] == ["thinking", "text", "text", "done"]
    assert ev[-1]["usage"] == {"prompt": 3, "completion": 2}


def test_chat_search_reports_count_not_results(client, monkeypatch):
    from nonita import handlers
    from nonita.search import SearchResult

    monkeypatch.setattr(handlers, "web_search", lambda q, max_results=4: [SearchResult("T", "http://u", "secret snippet")])
    monkeypatch.setattr(OllamaClient, "stream_chat", lambda self, **kw: iter([ChatStreamEvent(text="x"), ChatStreamEvent(done=True, stats={})]))
    ev = _chat(client, search_enabled=True)
    found = [e for e in ev if e["type"] == "search"]
    assert found == [{"type": "search", "count": 1}]
    assert "secret snippet" not in json.dumps(ev)
    monkeypatch.setattr(handlers, "web_search", lambda q, max_results=4: [])
    assert [e for e in _chat(client, search_enabled=True) if e["type"] == "search"][0]["count"] == 0


def test_chat_errors(client, monkeypatch):
    assert _chat(client, model=None)[0]["type"] == "error"
    assert "out of range" in _chat(client, port=70000)[0]["message"]

    def boom(self, **kw):
        raise RuntimeError("down")
        yield

    monkeypatch.setattr(OllamaClient, "stream_chat", boom)
    ev = _chat(client)
    assert ev[-1]["type"] == "error" and "down" in ev[-1]["message"]


def test_upload_cap_and_context(client, monkeypatch):
    monkeypatch.setenv("NONITA_MAX_UPLOAD_MB", "0.001")
    r = client.post("/api/upload", files=[("files", ("big.txt", b"x" * 5000)), ("files", ("s.txt", b"ok"))]).json()
    assert [f["name"] for f in r["files"]] == ["s.txt"] and r["rejected"][0]["name"] == "big.txt"
    assert server._resolve_uploads([r["files"][0]["id"], "../etc"])[0].endswith("s.txt")


def test_sessions_crud_and_wipe(client):
    msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "yo"}]
    cid = client.post("/api/sessions", json={"model": "m", "messages": msgs}).json()["id"]
    assert client.get("/api/sessions").json()[0]["id"] == cid
    assert client.get(f"/api/sessions/{cid}").json()["messages"] == msgs
    assert "hello" in client.get(f"/api/sessions/{cid}/export").text
    assert "yo" in client.post("/api/export", json={"messages": msgs}).text
    assert client.delete("/api/sessions").status_code == 400
    assert client.delete("/api/sessions?confirm=true").json()["deleted"] == 1
    assert client.get(f"/api/sessions/{cid}").status_code == 404
    assert client.post("/api/sessions", json={"messages": []}).status_code == 422


# ── authentication ────────────────────────────────────────────────────────
@pytest.fixture()
def anon(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DB_PATH", tmp_path / "h.db")
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "_SCRYPT_N", 2**10)
    monkeypatch.setattr(auth, "_fails", {})
    return TestClient(server.app)


def test_api_requires_login_but_static_and_meta_do_not(anon):
    assert anon.get("/api/config").status_code == 401
    assert anon.get("/api/sessions").json()["code"] == "auth_required"
    assert anon.get("/api/meta").status_code == 200
    assert anon.get("/").status_code == 200 and anon.get("/static/app.js").status_code == 200
    assert anon.get("/api/auth/me").json() == {"authenticated": False}


def test_default_admin_must_change_password_first(anon):
    assert anon.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    r = anon.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200 and r.json()["must_change"] is True
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie
    blocked = anon.get("/api/config")
    assert blocked.status_code == 403 and blocked.json()["code"] == "password_change_required"
    assert anon.get("/api/auth/me").json()["must_change"] is True

    for bad in ("",):
        assert anon.post("/api/auth/password", json={"current_password": "admin", "new_password": bad}).status_code == 422
    assert anon.post("/api/auth/password", json={"current_password": "wrong", "new_password": "a-good-passphrase"}).status_code == 422
    assert anon.post("/api/auth/password", json={"current_password": "admin", "new_password": "x"}).status_code == 200
    assert anon.get("/api/config").status_code == 200
    # old password is dead, new one works
    anon.post("/api/auth/logout")
    assert anon.get("/api/config").status_code == 401
    assert anon.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 401
    r = anon.post("/api/auth/login", json={"username": "admin", "password": "x"})
    assert r.json()["must_change"] is False


def test_password_change_revokes_other_sessions(anon, tmp_path):
    anon.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    anon.post("/api/auth/password", json={"current_password": "admin", "new_password": "first-passphrase"})
    other = TestClient(server.app)
    other.post("/api/auth/login", json={"username": "admin", "password": "first-passphrase"})
    assert other.get("/api/config").status_code == 200
    anon.post("/api/auth/password", json={"current_password": "first-passphrase", "new_password": "second-passphrase"})
    assert other.get("/api/config").status_code == 401
    assert anon.get("/api/config").status_code == 200


def test_login_throttle(anon):
    for _ in range(5):
        assert anon.post("/api/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401
    assert anon.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 429


def test_cross_origin_post_blocked(client):
    r = client.post("/api/ping", json={"host": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_password_is_hashed_at_rest(anon, tmp_path):
    anon.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    raw = (tmp_path / "auth.db").read_bytes()
    assert b"scrypt$" in raw
