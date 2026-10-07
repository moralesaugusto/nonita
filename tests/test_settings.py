from toty_webui import app


def test_validate_settings():
    assert app.validate_settings("h", 11434) == ("Saved locally in this browser.", "1")
    msg, ok = app.validate_settings("h", 70000)
    assert ok == "0" and msg.startswith("Not saved")


def test_reset_restores_env_defaults(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "envhost")
    monkeypatch.setenv("OLLAMA_PORT", "9999")
    host, port, _ = app.reset_settings()
    assert (host, port) == ("envhost", 9999)


def test_js_uses_versioned_key_and_only_host_port():
    assert "toty-webui.settings.v1" in app.JS_LOAD_SETTINGS
    assert "toty-webui.settings.v1" in app.JS_STORE_SETTINGS
    assert "removeItem" in app.JS_FORGET_SETTINGS
