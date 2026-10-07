"""TLS helpers: elliptic-curve-only certificates and a strong cipher list."""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

# ECDHE key exchange (forward secrecy) with AEAD ciphers only. TLS 1.3 suites are not affected by this string.
STRONG_CIPHERS = "ECDHE+AESGCM:ECDHE+CHACHA20"


def require_ec_key(key_path: Path) -> None:
    """Exit with a helpful message unless *key_path* holds an unencrypted elliptic-curve private key."""
    try:
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except (ValueError, TypeError) as exc:
        raise SystemExit(f"Cannot load {key_path}: {exc}. Regenerate with 'uv run nonita gen-cert --force'.")
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise SystemExit(
            f"{key_path} is a {type(key).__name__.replace('_', ' ')}, but Nonita only accepts elliptic-curve keys (no RSA). "
            "Replace it with 'uv run nonita gen-cert --force'."
        )
    if key.curve.key_size < 256:
        raise SystemExit(f"{key_path} uses a weak curve ({key.curve.name}); use P-256 or stronger.")


def generate_self_signed(cert_path: Path, key_path: Path, extra_names: list[str] | None = None, days: int = 365) -> None:
    """Write a self-signed ECDSA P-384 / SHA-384 certificate and key (key file mode 0600)."""
    key = ec.generate_private_key(ec.SECP384R1())
    names = ["localhost", socket.gethostname(), *(extra_names or [])]
    sans: list[x509.GeneralName] = [x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.IPAddress(ipaddress.ip_address("::1"))]
    for name in dict.fromkeys(n for n in names if n):
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            sans.append(x509.DNSName(name))
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "nonita")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                     data_encipherment=False, key_agreement=True, key_cert_sign=False,
                                     crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA384())
    )
    key_bytes = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key_bytes)
    os.chmod(key_path, 0o600)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
