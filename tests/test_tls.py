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


def test_ssl_kwargs_use_strong_ciphers(tmp_path, monkeypatch):
    monkeypatch.setenv("NONITA_CERT", str(tmp_path / "c.pem"))
    monkeypatch.setenv("NONITA_KEY", str(tmp_path / "k.pem"))
    assert config.ssl_launch_kwargs() == {}
    tls.generate_self_signed(tmp_path / "c.pem", tmp_path / "k.pem")
    kw = config.ssl_launch_kwargs()
    assert kw["ssl_ciphers"] == tls.STRONG_CIPHERS and "RSA" not in kw["ssl_ciphers"]
