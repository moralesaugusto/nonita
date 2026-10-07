from nonita import config


def test_env_port_invalid_falls_back(monkeypatch):
    for bad in ("abc", "0", "70000", "-5"):
        monkeypatch.setenv("OLLAMA_PORT", bad)
        assert config.env_port() == config.DEFAULT_PORT
    monkeypatch.setenv("OLLAMA_PORT", "8080")
    assert config.env_port() == 8080


def test_env_host_default_and_override(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert config.env_host() == config.DEFAULT_HOST
    monkeypatch.setenv("OLLAMA_HOST", " box ")
    assert config.env_host() == "box"


def test_env_timeout(monkeypatch):
    monkeypatch.delenv("OLLAMA_TIMEOUT", raising=False)
    assert config.env_timeout() == 120.0
    monkeypatch.setenv("OLLAMA_TIMEOUT", "30")
    assert config.env_timeout() == 30.0
    for bad in ("x", "0", "-1"):
        monkeypatch.setenv("OLLAMA_TIMEOUT", bad)
        assert config.env_timeout() == 120.0


def test_env_max_upload(monkeypatch):
    monkeypatch.setenv("NONITA_MAX_UPLOAD_MB", "1")
    assert config.env_max_upload_bytes() == 1024 * 1024
    monkeypatch.setenv("NONITA_MAX_UPLOAD_MB", "junk")
    assert config.env_max_upload_bytes() == 20 * 1024 * 1024


def test_env_file_loaded_without_overriding_real_env(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("# comment\nZZ_A=1\nZZ_B = 'two words'\nZZ_C=from-file\nbroken line\n")
    monkeypatch.delenv("ZZ_A", raising=False)
    monkeypatch.delenv("ZZ_B", raising=False)
    monkeypatch.setenv("ZZ_C", "from-env")
    config.load_env_file(f)
    import os
    assert os.environ["ZZ_A"] == "1" and os.environ["ZZ_B"] == "two words" and os.environ["ZZ_C"] == "from-env"
    for k in ("ZZ_A", "ZZ_B"):
        monkeypatch.delenv(k)


def test_safe_defaults(monkeypatch):
    monkeypatch.delenv("NONITA_HOST", raising=False)
    monkeypatch.delenv("GRADIO_SERVER_NAME", raising=False)
    assert config.server_host() == "127.0.0.1" and config.DEFAULT_HOST == "127.0.0.1"
