"""Making a certificate, so that requiring one is not the same as requiring work.

The Teaching Center serves HTTPS and nothing else. That is only a reasonable rule if getting a
certificate for a local or loopback server is trivial — otherwise it is a rule people route around,
which is worse than the rule not existing. `--make-cert` is the trivial path.

Nothing here is for a real course server. A self-signed certificate encrypts but is not trusted, so
for a class you want a certificate for a real name from a real CA (see the README). This is for
your own machine, a demo, and the loopback backend behind an nginx that holds the real one.

Shells out to `openssl` rather than taking a dependency on `cryptography`: this package is
deliberately tiny so a headless VM can install it in seconds, and openssl is already the thing the
server's own error message tells people to use.
"""
from __future__ import annotations

import ipaddress
import subprocess
from pathlib import Path


def default_paths(data_dir: str | Path) -> tuple[Path, Path]:
    """Where a made certificate lives: beside the course data, so `--data` moves both together."""
    d = Path(data_dir).expanduser() / "tls"
    return d / "cert.pem", d / "key.pem"


def _san(hosts) -> str:
    """`subjectAltName` entries, IPs and names told apart.

    Not decoration, and not optional: a certificate carrying only a CN is rejected outright by
    OpenSSL 3 and by macOS however it is signed — and it fails looking exactly like a trust
    problem, which sends you to the wrong end of the system.
    """
    parts = []
    for h in dict.fromkeys(hosts):                     # de-duplicated, order kept
        if not h:
            continue
        try:
            ipaddress.ip_address(h)
            parts.append(f"IP:{h}")
        except ValueError:
            parts.append(f"DNS:{h}")
    return ", ".join(parts)


def hosts_for(bind_host: str = "") -> list[str]:
    """localhost and 127.0.0.1 always; the bind address too when it is a real one.

    `0.0.0.0` means "every interface", which is not a name anything can be verified against, so it
    contributes nothing to a certificate.
    """
    hosts = ["localhost", "127.0.0.1"]
    h = (bind_host or "").strip()
    if h and h not in ("0.0.0.0", "::", "localhost", "127.0.0.1"):
        hosts.append(h)
    return hosts


def ensure(cert: str | Path, key: str | Path, hosts=None, *,
           run=subprocess.run) -> tuple[Path, bool]:
    """Make a self-signed pair at these paths if it is not already there.

    Returns `(cert_path, made)`. **Existing files are left alone** — regenerating would silently
    invalidate whatever trust the certificate had already been given (a `mkcert` CA, an entry in a
    student's trust store), turning a convenience into a foot-gun.
    """
    cert, key = Path(cert).expanduser(), Path(key).expanduser()
    if cert.is_file() and key.is_file() and cert.stat().st_size and key.stat().st_size:
        return cert, False

    cert.parent.mkdir(parents=True, exist_ok=True)
    cnf = cert.parent / "openssl.cnf"
    cnf.write_text(
        "[req]\ndistinguished_name = dn\nx509_extensions = v3\nprompt = no\n"
        "[dn]\nCN = localhost\n"
        f"[v3]\nsubjectAltName = {_san(hosts or hosts_for())}\n"
        "basicConstraints = critical, CA:TRUE\n", encoding="utf-8")
    try:
        r = run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                 "-days", "825", "-keyout", str(key), "-out", str(cert),
                 "-config", str(cnf)], capture_output=True)
    except FileNotFoundError as e:
        raise SystemExit(
            "--make-cert needs `openssl`, which is not on this machine. Install it, or pass an "
            "existing certificate with --tls-cert/--tls-key.") from e
    if getattr(r, "returncode", 1) != 0:
        detail = (getattr(r, "stderr", b"") or b"").decode(errors="replace").strip()[:400]
        raise SystemExit(f"openssl could not make a certificate:\n{detail}")
    key.chmod(0o600)          # it is a private key on a machine other people may log into
    return cert, True
