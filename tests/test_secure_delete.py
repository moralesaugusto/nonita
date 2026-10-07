import os
import sqlite3

import pytest

from nonita import history, secure_delete

SECRET = "TOP-SECRET-MARKER-9f3a"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DB_PATH", tmp_path / "h.db")
    return tmp_path


def _all_bytes(folder):
    return b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file())


def _msgs(text):
    return [{"role": "user", "content": text}, {"role": "assistant", "content": "ok", "searchQuery": text}]


def test_shred_file_overwrites_then_removes(tmp_path):
    f = tmp_path / "x.txt"
    f.write_bytes(SECRET.encode() * 1000)
    seen = {}
    real = os.fsync
    def spy(fd):
        seen.setdefault("data", open(f"/proc/self/fd/{fd}", "rb").read())
        return real(fd)
    os.fsync = spy
    try:
        secure_delete.shred_file(f)
    finally:
        os.fsync = real
    assert set(seen["data"]) == {0}                      # the pass wrote zeros over the whole file
    assert list(tmp_path.iterdir()) == []                # file (and its temporary name) are gone


def test_shred_passes_env(monkeypatch):
    monkeypatch.delenv("NONITA_SHRED_PASSES", raising=False)
    assert secure_delete.shred_passes() == 1
    monkeypatch.setenv("NONITA_SHRED_PASSES", "3")
    assert secure_delete.shred_passes() == 3
    monkeypatch.setenv("NONITA_SHRED_PASSES", "junk")
    assert secure_delete.shred_passes() == 1


def test_shred_does_not_follow_symlinks(tmp_path):
    target = tmp_path / "keep.txt"
    target.write_text("keep me")
    link = tmp_path / "link"
    link.symlink_to(target)
    secure_delete.shred_file(link)
    assert target.read_text() == "keep me" and not link.exists()


def test_conversations_are_encrypted_at_rest(db):
    cid = history.save_conversation(None, "m", _msgs(SECRET))
    assert SECRET.encode() not in _all_bytes(db)
    assert history.get_conversation(cid)["messages"][1]["searchQuery"] == SECRET
    assert SECRET in history.list_conversation_choices()[0][0]
    assert history.key_path().stat().st_mode & 0o777 == 0o600


def test_delete_removes_data_rotates_key_and_leaves_no_trace(db):
    keep = history.save_conversation(None, "m", _msgs("keep this one"))
    gone = history.save_conversation(None, "m", _msgs(SECRET))
    old_key = history.key_path().read_bytes()
    assert history.delete_conversation(gone) is True
    assert history.get_conversation(gone) is None
    assert history.get_conversation(keep)["messages"][0]["content"] == "keep this one"
    assert history.key_path().read_bytes() != old_key                 # old key destroyed
    assert sorted(p.name for p in db.iterdir()) == ["h.db", "h.key"]  # no stray .old/.new/journal files
    assert SECRET.encode() not in _all_bytes(db)
    assert history.delete_conversation(gone) is False


def test_wipe_destroys_database_and_key(db):
    for i in range(3):
        history.save_conversation(None, "m", _msgs(SECRET + str(i)))
    assert history.wipe_all() == 3
    assert list(db.iterdir()) == []
    assert history.list_conversation_choices() == []
    cid = history.save_conversation(None, "m", _msgs("fresh"))   # works again with a new key
    assert history.get_conversation(cid)["messages"][0]["content"] == "fresh"


def test_legacy_plaintext_database_is_migrated_and_plaintext_shredded(db):
    conn = sqlite3.connect(history.DB_PATH)
    conn.executescript(history._SCHEMA)
    conn.execute("INSERT INTO conversations (title,model,messages_json,created_at,updated_at) VALUES (?,?,?,?,?)",
                 (SECRET, "m", '[{"role":"user","content":"%s"}]' % SECRET, "t", "t"))
    conn.commit(); conn.close()
    assert SECRET.encode() in _all_bytes(db)
    choices = history.list_conversation_choices()
    assert SECRET in choices[0][0]
    assert SECRET.encode() not in _all_bytes(db)
    assert history.get_conversation(choices[0][1])["messages"][0]["content"] == SECRET


def test_missing_key_means_unreadable(db):
    cid = history.save_conversation(None, "m", _msgs(SECRET))
    history.key_path().unlink()
    with pytest.raises(RuntimeError, match="key"):
        history.get_conversation(cid)


def test_export_temp_file_is_shredded(db, monkeypatch):
    from fastapi.testclient import TestClient
    from nonita import auth, server
    monkeypatch.setattr(auth, "DB_PATH", db / "auth.db")
    monkeypatch.setattr(auth, "_SCRYPT_N", 2**10)
    shredded = []
    real = server.shred_file
    monkeypatch.setattr(server, "shred_file", lambda p, *a: (shredded.append(str(p)), real(p, *a)))
    c = TestClient(server.app, base_url="https://testserver")
    c.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    c.post("/api/auth/password", json={"current_password": "admin", "new_password": "x"})
    r = c.post("/api/export", json={"messages": _msgs("hello")})
    assert r.status_code == 200 and shredded and not os.path.exists(shredded[0])
