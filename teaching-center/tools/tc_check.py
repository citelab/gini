#!/usr/bin/env python3
"""Why won't gBuilder connect to the course server?

Run this ON THE MACHINE THAT FAILS, with THE SAME PYTHON that runs gBuilder — that second part is
the whole point. A browser proves nothing here: browsers use the operating system's certificate
store and will fetch a missing intermediate on their own, while Python uses its own store and never
does. "It works in Safari" and "it fails in gBuilder" are perfectly consistent.

Releases after 6.22.3 close most of that gap: gBuilder verifies through `truststore`, against the
operating system's certificates, so this checks both ways — Python's own list (what an older
gBuilder used) and the system's (what a current one uses) — and says which one gBuilder needs.

    python3 tc_check.py https://gini.cs.mcgill.ca

To find the interpreter gBuilder uses:

    head -1 "$(command -v gbuilder)"      # macOS / Linux — the shebang names it
    python -c "import sys; print(sys.executable)"

This prints a verdict and the evidence behind it. Send the whole output to your instructor.

Once the connection verifies, it also sends a hand-in-sized body (PROBE_BYTES of it, under a
code no course ever issued) down the real submit route and reports how far it got. That is the
check for the OTHER hand-in failure, "Broken pipe": a long lab's proof is megabytes, and a proxy
in front of the Teaching Center with a body limit refuses it mid-upload. The Teaching Center
itself answers the probe with a refusal (HTTP 409, unknown code) and stores nothing — anything
else is something between you and it.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

#: The size of the submit probe. A long OS lab's proof, uncompressed, is about this — and an
#: older gBuilder sends it uncompressed. nginx's default `client_max_body_size` is 1 MB.
PROBE_BYTES = 2_000_000


def probe_submit(url: str, ctx: ssl.SSLContext, size: int = PROBE_BYTES,
                 timeout: float = 90.0) -> tuple[str, str]:
    """POST a body of `size` bytes to the submit route. Returns (verdict, detail).

    Verdicts: `ok` (the Teaching Center read all of it and refused the fake code, as it should),
    `too_large` (an HTTP 413 — a proxy's body limit, since the Teaching Center has none),
    `dropped` (the connection was closed while the body was being sent — what gBuilder shows as
    "Broken pipe"), `timeout`, or `odd` with whatever came back.
    """
    body = json.dumps({"code": "TC-CHECK-PROBE", "proof": {"pad": "x" * size}}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/api/activity/submit", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return "odd", f"HTTP {r.status} — a refusal was expected"
    except urllib.error.HTTPError as e:
        took = time.time() - t0
        if e.code == 409:
            return "ok", f"refused as expected (HTTP 409) — {len(body):,} bytes in {took:.1f}s"
        if e.code == 413:
            return "too_large", (f"HTTP 413 — something in front of the Teaching Center caps "
                                 f"request bodies below {len(body):,} bytes")
        return "odd", f"HTTP {e.code} after {took:.1f}s"
    except Exception as e:                                   # noqa: BLE001
        took = time.time() - t0
        cause = getattr(e, "reason", e)
        if isinstance(cause, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return "dropped", (f"the connection was closed while the body was being sent, "
                               f"after {took:.1f}s ({cause})")
        if isinstance(cause, (TimeoutError, socket.timeout)) or "timed out" in str(e).lower():
            return "timeout", (f"no answer within {timeout:.0f}s — {len(body):,} bytes is too "
                               f"slow for this link")
        return "odd", f"{type(e).__name__}: {e}"

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

    # ---- now with verification on, against Python's own list (gBuilder 6.22.3 and older) ------ #
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

    # ---- and with the operating system's certificates ------------------------------------------ #
    #
    # gBuilder releases after 6.22.3 verify through `truststore` — the keychain on macOS, the
    # Windows chain builder — and never consult the list above. So the line above says what an
    # older gBuilder sees, and this one what a current one sees. If truststore is not importable
    # here, this interpreter is not running a current gBuilder.
    system = None
    try:
        import truststore
    except ImportError:
        _say("system certificates", "(truststore not installed — an older gBuilder)")
    else:
        try:
            sctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            with socket.create_connection((host, port), timeout=10) as s4:
                with sctx.wrap_socket(s4, server_hostname=host):
                    pass
            system = True
            _say("system certificates", "ok — what a current gBuilder uses")
        except Exception as e:                               # noqa: BLE001
            system = False
            _say("system certificates",
                 f"REFUSED — {getattr(e, 'verify_message', '') or e}")

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

    # ---- can a hand-in-sized upload get through? ---------------------------------------------- #
    probe = None
    if err is None:
        print("\nSubmit route")
        probe, detail = probe_submit(f"{p.scheme}://{host}:{port}", ctx)
        _say(f"{PROBE_BYTES // 1_000_000} MB upload", detail)

    # ---- what it means ------------------------------------------------------------------------- #
    print("\nVERDICT")
    reason = (getattr(err, "verify_message", "") or str(err or "")).lower()
    if err is None and probe == "too_large":
        print("  The connection is fine, but a hand-in-sized upload is refused with HTTP 413. The\n"
              "  Teaching Center has no body limit, so this is the proxy in front of it — on nginx,\n"
              "  `client_max_body_size` defaults to 1 MB and the README's configuration sets 32m.\n"
              "  A student with a long lab sees this as \"Broken pipe\" at hand-in. The instructor\n"
              "  fixes it on the server; the student's work is safe in gBuilder's outbox meanwhile.")
    elif err is None and probe == "dropped":
        print("  The connection verifies, but an upload of hand-in size was cut off before it\n"
              "  finished — the same \"Broken pipe\" gBuilder reports. On a VPN this is usually the\n"
              "  link; if it happens every time from this machine, it is something in front of the\n"
              "  server (a body limit, with the refusal lost mid-upload). Send this output to your\n"
              "  instructor, who can check the proxy's error log for \"too large body\".")
    elif err is None and probe == "timeout":
        print("  The connection verifies, but a hand-in-sized upload did not finish in time. This\n"
              "  link is slow; gBuilder keeps the work and retries, and Course → Resubmit to Course\n"
              "  Server sends it when you are somewhere faster.")
    elif err is None:
        print("  This machine connects and verifies the certificate correctly. If gBuilder still\n"
              "  refuses, it is running a DIFFERENT Python from this one — re-run this with the\n"
              "  interpreter named by:  head -1 \"$(command -v gbuilder)\"")
    elif system:
        print("  This Python's own certificate list refuses the server, but this computer's\n"
              "  system certificates accept it — and those are what gBuilder uses since the\n"
              "  release after 6.22.3. If gBuilder still refuses, it is older than that:\n\n"
              "      pipx upgrade gini-toolkit        (or: pip install -U gini-toolkit)\n\n"
              "  then restart gBuilder and press Retry now — the queued hand-ins go out.")
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
