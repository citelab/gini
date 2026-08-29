"""TLS: the Teaching Center serving HTTPS, and what gBuilder does about it.

Staff sign in with a password. Over plain HTTP on a shared VM that password is readable by anyone
else logged into the same machine, so TLS is not decoration here.

Two things are worth defending, and neither is "does openssl work":

* **Half a certificate pair must refuse to start.** Falling back to plain HTTP would look like a
  successful launch while every password crossed the network in the clear — the worst kind of
  failure, because nothing appears wrong.
* **An untrusted certificate is not an outage.** gBuilder's message decides what a student does
  next; "is the server running?" sends them chasing a server that is up, and no amount of retrying
  fixes a certificate.
"""
from __future__ import annotations

import json
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center" / "src"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))

from gini.services import tc_submit                              # noqa: E402

needs_openssl = pytest.mark.skipif(
    subprocess.run(["which", "openssl"], capture_output=True).returncode != 0,
    reason="openssl not available")


@pytest.fixture
def certs(tmp_path):
    """A self-signed pair — which is also the realistic 'wrong' case for a client.

    The subjectAltName is not decoration. A certificate carrying only CN=localhost is rejected by
    OpenSSL 3 and by macOS regardless of whether the issuer is trusted, so a `-subj "/CN=localhost"`
    fixture makes the trusted-certificate test fail for a reason that has nothing to do with trust
    — and it fails only on the newer stack, which is how it passed here and failed on the Mac.

    Written as a config file rather than `-addext` because LibreSSL, which is what `openssl` is on
    macOS, has not always supported that flag.
    """
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    cfg = tmp_path / "openssl.cnf"
    cfg.write_text("[req]\n"
                   "distinguished_name = dn\n"
                   "x509_extensions = v3\n"
                   "prompt = no\n"
                   "[dn]\n"
                   "CN = localhost\n"
                   "[v3]\n"
                   "subjectAltName = DNS:localhost, IP:127.0.0.1\n"
                   "basicConstraints = critical, CA:TRUE\n", encoding="utf-8")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "1",
                    "-config", str(cfg)], check=True, capture_output=True)
    return cert, key


@needs_openssl
def test_the_fixture_certificate_has_a_subject_alt_name(certs):
    """Guard the fixture. Without a SAN the trusted-certificate test below fails on a certificate
    technicality while looking exactly like a trust failure."""
    out = subprocess.run(["openssl", "x509", "-in", str(certs[0]), "-noout", "-text"],
                         capture_output=True, text=True, check=True).stdout
    assert "Subject Alternative Name" in out and "DNS:localhost" in out


@pytest.fixture
def tls_server(tmp_path, certs, monkeypatch):
    """The real server, over real TLS, on a loopback port."""
    cert, key = certs
    monkeypatch.setenv("COURSE_ROOT", str(tmp_path))
    monkeypatch.setenv("ADMIN_ID", "boss")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse")
    for mod in [m for m in list(sys.modules) if m.startswith("gini_teaching_center")]:
        sys.modules.pop(mod, None)
    from gini_teaching_center.store import Store
    Store._instances.clear()
    from gini_teaching_center import accounts as A
    from gini_teaching_center import server
    server.ROOT = tmp_path
    server.MATERIALS = tmp_path / "materials"
    server.MATERIALS.mkdir(parents=True, exist_ok=True)
    server._ACCTS = A.Accounts(tmp_path)
    server._STORE = Store(tmp_path)
    server._ACCTS.ensure_admin()          # ADMIN_PASSWORD is set above, so this creates `boss`

    from http.server import ThreadingHTTPServer
    ctx = server._tls_context(str(cert), str(key))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"https://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield url, cert
    finally:
        httpd.shutdown()
        httpd.server_close()


# -- refusing to start half-configured ---------------------------------------- #
@needs_openssl
def test_a_certificate_without_a_key_refuses_to_start(certs, monkeypatch, tmp_path):
    """THE dangerous slip. Falling back to plain HTTP would look like a clean start while every
    staff password went out in the clear."""
    from gini_teaching_center import server
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "MATERIALS", tmp_path / "m")
    with pytest.raises(SystemExit) as e:
        server.serve(host="127.0.0.1", port=0, tls_cert=str(certs[0]))
    assert "BOTH" in str(e.value)


@needs_openssl
def test_a_key_without_a_certificate_refuses_too(certs, monkeypatch, tmp_path):
    from gini_teaching_center import server
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "MATERIALS", tmp_path / "m")
    with pytest.raises(SystemExit):
        server.serve(host="127.0.0.1", port=0, tls_key=str(certs[1]))


def test_a_missing_certificate_file_names_which_half_is_wrong(tmp_path):
    from gini_teaching_center import server
    with pytest.raises(SystemExit) as e:
        server._tls_context(str(tmp_path / "nope.pem"), str(tmp_path / "nope.key"))
    assert "certificate" in str(e.value)


@needs_openssl
def test_a_mismatched_pair_is_refused_rather_than_served(certs, tmp_path):
    """A cert with somebody else's key would otherwise fail per-connection, at the worst moment."""
    from gini_teaching_center import server
    other_key = tmp_path / "other.key"
    subprocess.run(["openssl", "genrsa", "-out", str(other_key), "2048"],
                   check=True, capture_output=True)
    with pytest.raises(SystemExit):
        server._tls_context(str(certs[0]), str(other_key))


@needs_openssl
def test_tls_1_2_is_the_floor(certs):
    from gini_teaching_center import server
    ctx = server._tls_context(str(certs[0]), str(certs[1]))
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


# -- actually serving ---------------------------------------------------------- #
@needs_openssl
def test_the_console_is_served_over_https(tls_server):
    url, cert = tls_server
    ctx = ssl.create_default_context(cafile=str(cert))
    ctx.check_hostname = False
    with urllib.request.urlopen(url + "/", context=ctx, timeout=10) as r:
        assert r.status == 200
        assert b"GINI Teaching Center" in r.read()


@needs_openssl
def test_signing_in_works_over_https(tls_server):
    """The whole reason for TLS: this request carries a password."""
    url, cert = tls_server
    ctx = ssl.create_default_context(cafile=str(cert))
    ctx.check_hostname = False
    req = urllib.request.Request(
        url + "/auth/login", data=json.dumps({"id": "boss", "password": "correct-horse"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
        assert json.loads(r.read())["ok"]


@needs_openssl
def test_plain_http_against_the_tls_port_does_not_get_a_page(tls_server):
    url, _ = tls_server
    with pytest.raises(Exception):
        urllib.request.urlopen(url.replace("https://", "http://") + "/", timeout=5)


# -- what gBuilder tells the student ------------------------------------------- #
@needs_openssl
def test_an_untrusted_certificate_is_not_reported_as_an_outage(tls_server):
    """THE message that decides what a student does next. The server is up; telling them to check
    whether it is running wastes their evening on the wrong thing."""
    url, _ = tls_server
    with pytest.raises(tc_submit.Untrusted) as e:
        tc_submit.check_code(url, "AAAA-AAAA")
    msg = str(e.value)
    assert "certificate" in msg
    assert "server is running" in msg          # says explicitly that it is NOT an outage
    assert "instructor" in msg                 # and who can actually fix it


@needs_openssl
def test_untrusted_is_still_caught_by_code_that_expects_unreachable(tls_server):
    """It is a subclass on purpose: existing `except Unreachable` handlers must not start leaking
    a new exception type into a Qt slot."""
    url, _ = tls_server
    assert issubclass(tc_submit.Untrusted, tc_submit.Unreachable)
    with pytest.raises(tc_submit.Unreachable):
        tc_submit.submit(url, "AAAA-AAAA", {})


@needs_openssl
def test_a_trusted_certificate_just_works(tls_server, monkeypatch):
    """With a certificate the machine trusts, gBuilder needs no configuration at all — the request
    goes through and comes back as an ordinary refusal rather than a transport failure.

    Trust is established via SSL_CERT_FILE, which is what Python's default context honours. Worth
    knowing: it is also the escape hatch for a school that runs its own CA and has not managed to
    get the root into every student's system trust store.
    """
    url, cert = tls_server
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    # Confirm the lever actually moved before trusting the result. If a Python build ignores
    # SSL_CERT_FILE, this test would fail with `Untrusted` and read exactly like a bug in the
    # classification it is meant to be checking — so say which of the two it is.
    loaded = ssl.create_default_context().get_ca_certs()
    if not any("localhost" in str(c.get("subject", ())) for c in loaded):
        pytest.skip(f"this Python ignores SSL_CERT_FILE ({len(loaded)} CAs loaded, none ours), so "
                    f"trust cannot be injected here — the untrusted-path tests still cover the "
                    f"classification")
    # The certificate is CN=localhost, so connect by that name — an IP would fail hostname
    # verification even with the CA trusted, which is its own (correct) refusal.
    r = tc_submit.check_code(url.replace("127.0.0.1", "localhost"), "AAAA-AAAA")
    assert r.get("ok") is False and "error" in r        # a refusal, not a transport failure


def test_a_real_outage_is_still_reported_as_one():
    """The classification must not swallow genuine network failures into a certificate story."""
    with pytest.raises(tc_submit.Unreachable) as e:
        tc_submit.check_code("https://127.0.0.1:9", "AAAA-AAAA")
    assert not isinstance(e.value, tc_submit.Untrusted)
