import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives import hashes, serialization

from nonita import config, tls


def test_generated_cert_is_ecdsa_p384(tmp_path):
    cert_p, key_p = tmp_path / "c.pem", tmp_path / "k.pem"
    tls.generate_self_signed(cert_p, key_p, ["nonita.lan", "10.1.2.3"])
    cert = x509.load_pem_x509_certificate(cert_p.read_bytes())
    pub = cert.public_key()
    assert isinstance(pub, ec.EllipticCurvePublicKey) and pub.curve.name == "secp384r1"
    assert isinstance(cert.signature_hash_algorithm, hashes.SHA384)
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "nonita.lan" in san.get_values_for_type(x509.DNSName)
    assert key_p.stat().st_mode & 0o777 == 0o600
    tls.require_ec_key(key_p)


def test_rsa_key_is_refused(tmp_path):
    k = tmp_path / "rsa.pem"
    k.write_bytes(rsa.generate_private_key(65537, 2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with pytest.raises(SystemExit, match="no RSA"):
        tls.require_ec_key(k)


def test_https_is_mandatory_and_generates_ec_cert_when_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NONITA_CERT", str(tmp_path / "c.pem"))
    monkeypatch.setenv("NONITA_KEY", str(tmp_path / "k.pem"))
    kw = config.ssl_launch_kwargs()           # no certificate yet: one is generated, never plain HTTP
    assert set(kw) == {"ssl_certfile", "ssl_keyfile"} and "generated" in capsys.readouterr().out
    tls.require_ec_key(tmp_path / "k.pem")
    assert config.ssl_launch_kwargs() == kw   # reused on the next start


def test_half_a_certificate_pair_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("NONITA_CERT", str(tmp_path / "c.pem"))
    monkeypatch.setenv("NONITA_KEY", str(tmp_path / "k.pem"))
    (tmp_path / "c.pem").write_text("x")
    with pytest.raises(SystemExit, match="only one"):
        config.ssl_launch_kwargs()


def test_minimum_tls_version_is_1_3():
    import ssl
    assert tls.MIN_TLS_VERSION == ssl.TLSVersion.TLSv1_3


def test_session_cookie_is_always_secure(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from nonita import auth, server
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "a.db")
    monkeypatch.setattr(auth, "_SCRYPT_N", 2**10)
    r = TestClient(server.app, base_url="http://testserver").post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert "secure" in r.headers["set-cookie"].lower() and "strict-transport-security" in r.headers
