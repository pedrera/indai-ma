"""Explicitly add/remove the local CA in this development venv's certifi bundle.

Never executed by Compose or the application. No TLS verification bypass. This
changes trust for this venv, so review/approve it before running; restart clients.
"""
import argparse
from pathlib import Path
import sys

import certifi
from cryptography import x509


def configure(ca_path: Path, *, remove=False):
    prefix = Path(sys.prefix).resolve()
    target = Path(certifi.where()).resolve()
    if sys.prefix == sys.base_prefix or not target.is_relative_to(prefix):
        raise ValueError("Only an isolated development venv may be configured")
    ca_pem = ca_path.read_bytes()
    ca = x509.load_pem_x509_certificate(ca_pem)
    if not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise ValueError("The supplied certificate is not a CA")
    backup = target.with_suffix(".pem.indai-keycloak-original")
    current = target.read_bytes()
    if backup.exists():
        original = backup.read_bytes()
    else:
        if remove:
            raise ValueError("No original bundle backup exists")
        backup.write_bytes(current)
        original = current
    combined = original + b"\n" + ca_pem
    if current not in (original, combined):
        raise ValueError("Bundle changed since setup; inspect it manually, do not overwrite")
    target.write_bytes(original if remove else combined)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ca-file", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--install", action="store_true")
    mode.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    configure(args.ca_file, remove=args.remove)
    print("Development venv trust configured. Restart Streamlit/FastAPI; no containers started.")
