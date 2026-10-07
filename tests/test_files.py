from toty_webui.files import ingest_upload_paths


def test_oversized_file_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("TOTY_MAX_UPLOAD_MB", "0.001")
    big = tmp_path / "big.txt"
    big.write_text("x" * 5000)
    out = ingest_upload_paths([str(big)])
    assert "Skipped" in out and "upload limit" in out


def test_small_file_read(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    assert "hello" in ingest_upload_paths([str(f)])
