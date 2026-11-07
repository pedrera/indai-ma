"""Opt-in external development secrets/TLS generation; never starts Docker.

Run only after configuration review. No output file may live in the repository.
Dependencies are the project's already installed cryptography and standard library.
"""
import argparse
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ISSUER = "https://localhost:8443/realms/indai-ma-dev"
AUDIENCE = "indai-ma-api"


def private_directory(output: Path):
    if not output.is_absolute():
        raise ValueError("Choose an absolute external directory")
    output = output.resolve()
    if output.is_relative_to(REPO) or output.exists():
        raise ValueError("Output must be a new directory outside the repository")
    output.mkdir(mode=0o700)
    if sys.platform == "win32":
        account = subprocess.run(["whoami"], check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run(["icacls", str(output), "/inheritance:r", "/grant:r",
                        account + ":(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
                       check=True, capture_output=True)
    return output


def tls_files(root: Path):
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "indAI MA local development CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(ca.subject).public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=14))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                           x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    for folder in (root / "ca", root / "tls"):
        folder.mkdir(mode=0o700)
    for path, key in ((root / "ca/ca-key.pem", ca_key), (root / "tls/localhost-key.pem", leaf_key)):
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
        path.chmod(0o600)
    ca_pem = ca.public_bytes(serialization.Encoding.PEM)
    (root / "ca/ca-cert.pem").write_bytes(ca_pem)
    (root / "ca/ca-cert.cer").write_bytes(ca.public_bytes(serialization.Encoding.DER))
    (root / "tls/localhost-cert.pem").write_bytes(leaf.public_bytes(serialization.Encoding.PEM) + ca_pem)


def prepare(output: Path):
    root = private_directory(output)
    tls_files(root)
    client_secret = secrets.token_urlsafe(32)
    values = {
        "INDAI_KEYCLOAK_ENV_FILE": (root / "compose.env").as_posix(),
        "INDAI_KEYCLOAK_TLS_DIR": (root / "tls").as_posix(),
        "KC_BOOTSTRAP_ADMIN_USERNAME": "indai-local-bootstrap",
        "KC_BOOTSTRAP_ADMIN_PASSWORD": secrets.token_urlsafe(32),
        "INDAI_STREAMLIT_CLIENT_SECRET": client_secret,
        **{name: secrets.token_urlsafe(32) for name in (
            "KC_ANALYST_A_PASSWORD", "KC_ANALYST_B_PASSWORD", "KC_VIEWER_A_PASSWORD", "KC_OUTSIDER_PASSWORD")},
    }
    (root / "compose.env").write_text("".join(f'{key}="{value}"\n' for key, value in values.items()), encoding="utf-8")
    (root / "streamlit.toml").write_text(
        '[auth]\nredirect_uri = "http://localhost:8501/oauth2callback"\n'
        f'cookie_secret = "{secrets.token_urlsafe(64)}"\n'
        'client_id = "indai-ma-streamlit"\n'
        f'client_secret = "{client_secret}"\nserver_metadata_url = "{ISSUER}/.well-known/openid-configuration"\n'
        'expose_tokens = ["access"]\n\n[auth.client_kwargs]\nscope = "openid profile"\n'
        'code_challenge_method = "S256"\n', encoding="utf-8")
    shutil.copyfile(HERE / "backend-memberships.json", root / "backend-memberships.json")
    (root / "backend.env").write_text(
        f'INDAI_OIDC_ISSUER={ISSUER}\nINDAI_OIDC_AUDIENCE={AUDIENCE}\n'
        f'INDAI_OIDC_JWKS_URL={ISSUER}/protocol/openid-connect/certs\n'
        f'INDAI_MEMBERSHIPS_FILE={(root / "backend-memberships.json").as_posix()}\n', encoding="utf-8")
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    prepare(args.output)
    print("External development configuration created. No trust store changed; no container started.")


if __name__ == "__main__":
    main()
