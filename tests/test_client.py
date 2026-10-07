import json

import httpx
import pytest

from nonita.client import OllamaClient


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_from_host_validates():
    assert OllamaClient.from_host("h", 1234).base_url == "http://h:1234"
    with pytest.raises(ValueError):
        OllamaClient.from_host("h", 0)


def test_list_models_sorted_and_filtered(monkeypatch):
    payload = {"models": [{"name": "b"}, {"name": "a"}, {"name": ""}, None, {"x": 1}]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(payload))
    assert OllamaClient("http://h").list_models() == ["a", "b"]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse({}))
    assert OllamaClient("http://h").list_models() == []


def test_stop_partial_stream(monkeypatch):
    lines = [json.dumps({"message": {"content": c}, "done": False}) for c in ("a", "b", "c")]
    lines.append(json.dumps({"done": True}))

    def handler(request):
        return httpx.Response(200, content="\n".join(lines).encode())

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    seen = []

    def abort():
        return len(seen) >= 1

    events = []
    for evt in OllamaClient("http://h").stream_chat(model="m", messages=[], should_abort=abort):
        events.append(evt)
        seen.append(evt)
    assert [e.text for e in events] == ["a"]
    assert not any(e.done for e in events)
