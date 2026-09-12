#!/usr/bin/env python3
"""Why won't gBuilder connect to the course server?

Run this ON THE MACHINE THAT FAILS, with THE SAME PYTHON that runs gBuilder — that second part is
the whole point. A browser proves nothing here: browsers use the operating system's certificate
store and will fetch a missing intermediate on their own, while Python uses its own store and never
does. "It works in Safari" and "it fails in gBuilder" are perfectly consistent.

    python3 tc_check.py https://gini.cs.mcgill.ca

To find the interpreter gBuilder uses:

    head -1 "$(command -v gbuilder)"      # macOS / Linux — the shebang names it
    python -c "import sys; print(sys.executable)"

This prints a verdict and the evidence behind it. Send the whole output to your instructor.
"""
from __future__ import annotations

import hashlib
import os
import platform
import socket
import ssl
import sys
import urllib.parse

# SHA-256 of the certificate each endpoint really serves, read from the server itself. If your
# machine is handed something else, the connection is being intercepted — that is the one failure
# no amount of configuring gBuilder can fix, and the one worth knowing about immediately.
KNOWN = {
    ("gini.cs.mcgill.ca", 443):
        "b490f70d61b045cd9284c83409eba84616c904000b2fdf9d8ae745cbb4430646",
    ("gini-tc.samoza.space", 8443):
        "45998543135b46606666561143a65495e38fa0f03e3437d8da03013f4b0b68e9",
}


def _say(label: str, value: str) -> None:
    print(f"  {label:<26} {value}")


def main(argv: list[str]) -> int:
    url = argv[1] if len(argv) > 1 else "https://gini.cs.mcgill.ca"
    p = urllib.parse.urlparse(url if "//" in url else "https://" + url)
    host = p.hostname or ""
    port = p.port or (443 if p.scheme == "https" else 80)

    print(f"\nChecking {p.scheme}://{host}:{port}\n")
    print("Python")
    _say("version", sys.version.split()[0])
    _say("executable", sys.executable)
    _say("platform", f"{platform.system()} {platform.release()}")

    # ---- the certificate store this Python will actually use ---------------------------------- #
    print("\nCertificate store")
    ctx = ssl.create_default_context()
    paths = ssl.get_default_verify_paths()
    _say("cafile", f"{paths.cafile or '(none)'}"
                   f"{'' if not paths.cafile else '  [exists]' if os.path.exists(paths.cafile) else '  [MISSING]'}")
    _say("capath", str(paths.capath or "(none)"))
    for var in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        if os.environ.get(var):
            _say(var, os.environ[var])
    try:
        import certifi
        _say("certifi", certifi.where())
    except ImportError:
        _say("certifi", "(not installed)")

    # ---- can we even reach it? ---------------------------------------------------------------- #
    print("\nConnection")
    try:
        sock = socket.create_connection((host, port), timeout=10)
    except Exception as e:
        _say("TCP connect", f"FAILED — {type(e).__name__}: {e}")
        print("\nVERDICT: the server cannot be reached at all. This is a network problem, not a\n"
              "certificate one — check that you are on the campus VPN.\n")
        return 2
    _say("TCP connect", f"ok ({sock.getpeername()[0]})")

    # What was actually served, regardless of whether we trust it.
    seen_fp, seen_ok = "", False
    try:
        bare = ssl._create_unverified_context()
        with bare.wrap_socket(sock, server_hostname=host) as s:
            der = s.getpeercert(binary_form=True) or b""
            seen_fp = hashlib.sha256(der).hexdigest()
            _say("TLS version", s.version() or "?")
    except Exception as e:
        _say("TLS handshake", f"FAILED — {type(e).__name__}: {e}")
        return 2
    finally:
        try:
            sock.close()
        except Exception:
            pass

    expected = KNOWN.get((host, port))
    _say("certificate seen", seen_fp[:32] + "…")
    if expected:
        seen_ok = seen_fp == expected
        _say("matches the real one", "YES" if seen_ok else "NO  <-- something is rewriting it")

    # ---- now with verification on, which is what gBuilder does --------------------------------- #
    err = None
    try:
        with socket.create_connection((host, port), timeout=10) as s2:
            with ctx.wrap_socket(s2, server_hostname=host):
                pass
        _say("verified connection", "ok")
    except ssl.SSLCertVerificationError as e:
        err = e
        _say("verified connection", f"REFUSED — {e.verify_message or e.reason}")
    except Exception as e:                                   # noqa: BLE001
        err = e
        _say("verified connection", f"REFUSED — {type(e).__name__}: {e}")

    # ---- does this Python trust ANYTHING? ------------------------------------------------------ #
    #
    # The tempting check is cert_store_stats(), and it is wrong: certificates loaded through
    # set_default_verify_paths() are read lazily, so a healthy machine reports zero CAs and every
    # failure looks like an empty trust store. Ask a question that cannot be answered lazily —
    # reach a well-known public site with the SAME context. If that fails too, nothing about the
    # course server is special and the trust store is the story.
    internet = None
    if err is not None:
        try:
            with socket.create_connection(("pypi.org", 443), timeout=10) as s3:
                with ssl.create_default_context().wrap_socket(s3, server_hostname="pypi.org"):
                    pass
            internet = True
        except Exception:                                    # noqa: BLE001
            internet = False
        _say("trusts pypi.org too", "yes" if internet else "NO — this Python trusts almost nothing")

    # ---- what it means ------------------------------------------------------------------------- #
    print("\nVERDICT")
    reason = (getattr(err, "verify_message", "") or str(err or "")).lower()
    if err is None:
        print("  This machine connects and verifies the certificate correctly. If gBuilder still\n"
              "  refuses, it is running a DIFFERENT Python from this one — re-run this with the\n"
              "  interpreter named by:  head -1 \"$(command -v gbuilder)\"")
    elif expected and not seen_ok:
        print("  The certificate this machine received is NOT the one the server is sending, so\n"
              "  something between you and the server is intercepting HTTPS — antivirus with\n"
              "  'SSL scanning', a corporate proxy, or a captive portal. Its root is trusted by\n"
              "  your browser but not by Python, which is why the browser works and gBuilder does\n"
              "  not. Turn off HTTPS/SSL scanning, or use a different network.")
    elif internet is False:
        # Print the command for THIS machine, already filled in. "Substitute your version" is how
        # an instruction earns a second round of emails: the folder is /Applications/Python 3.13
        # only on 3.13, and a student who has two Pythons installed cannot tell which to name.
        mm = f"{sys.version_info.major}.{sys.version_info.minor}"
        official = f"/Applications/Python {mm}/Install Certificates.command"
        print("  This Python distrusts pypi.org as well, so it is not the course server that is\n"
              "  wrong — this machine cannot verify ordinary HTTPS at all. It has no usable\n"
              "  certificate store, which is what the python.org installer leaves behind when its\n"
              "  certificate step is never run. Your browser has its own store and is unaffected,\n"
              "  which is why the site looks fine in Safari and not in gBuilder.\n")
        if sys.platform == "darwin" and os.path.exists(official):
            print(f"  Run this, then restart gBuilder:\n\n      open \"{official}\"\n")
        else:
            print("  Run this, then restart gBuilder:\n\n"
                  f'      "{sys.executable}" -m pip install --upgrade certifi\n'
                  f'      "{sys.executable}" -c "import ssl,pathlib,certifi; '
                  "d=pathlib.Path(ssl.get_default_verify_paths().openssl_cafile); "
                  "d.parent.mkdir(parents=True,exist_ok=True); "
                  "d.unlink(missing_ok=True); d.symlink_to(certifi.where()); print('linked',d)\"\n")
            print("  (That is exactly what Install Certificates.command does: install certifi and\n"
                  "  point this interpreter's certificate path at it. Add sudo if it says the path\n"
                  "  is not writable.)\n")
        print("  Then re-run this check — both lines should say ok.")
    elif "hostname mismatch" in reason:
        print("  The URL does not match the name on the certificate. Check the address in\n"
              "  gBuilder's Settings against the one your instructor gave you — in particular the\n"
              "  PORT, since a different port here serves a different certificate.")
    elif "expired" in reason:
        print("  The server's certificate has expired. Only the instructor can fix this; nothing\n"
              "  you do on your machine will help. Check your computer's clock first, though — a\n"
              "  wrong date makes a perfectly good certificate look expired.")
    elif "self-signed" in reason or "self signed" in reason:
        print("  The chain ends in a self-signed certificate that this machine does not trust.\n"
              "  Either the server is using a self-signed certificate, or something on your\n"
              "  network is intercepting HTTPS. Send this output to your instructor.")
    elif "unable to get local issuer" in reason:
        print("  This machine cannot find the authority that issued the server's certificate,\n"
              "  even though it has a certificate store. Usually an out-of-date store or an\n"
              "  intercepting proxy. Send this output to your instructor.")
    else:
        print(f"  Unrecognised failure: {err}\n  Send this output to your instructor.")
    print()
    return 0 if err is None else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
