"""TLS: the Teaching Center serving HTTPS, and what gBuilder does about it.

Staff sign in with a password. Over plain HTTP on a shared VM that password is readable by anyone
else logged into the same machine, so TLS is not decoration here.

Two things are worth defending, and neither is "does openssl work":

* **Plain HTTP is not an option at all.** It used to be, with a printed warning — and a warning is
  not a control. Loopback can hold a certificate like any other name, so the one argument for
  keeping an HTTP mode ("you cannot always have a cert") does not survive contact with `openssl`.
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


@pytest.fixture(autouse=True)
def _fresh_opener(monkeypatch):
    """Every test here decides its own trust — none inherits the previous one's TLS context.

    `urllib.request.urlopen` builds an opener once and caches it in the module global `_opener`,
    and `HTTPSHandler.__init__` resolves its SSL context at CONSTRUCTION time. So the first
    `urlopen` anywhere in the process freezes the context every later call uses: patching
    `ssl._create_default_https_context` after that point changes nothing, and the trusted-
    certificate test below fails as `Untrusted`. That is why it passed when run alone and failed
    in the file and in the suite — the untrusted tests above it had already built the opener.

    Clearing the cache per test makes the patch take effect, and, just as importantly, stops a
    trusting opener leaking forward into the tests that must NOT trust this certificate.
    """
    monkeypatch.setattr(urllib.request, "_opener", None)


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
    """With a certificate the machine trusts, gBuilder needs no configuration at all: the request
    goes through and comes back as an ordinary refusal, not a transport failure.

    Trust is injected at `ssl._create_default_https_context`, the hook `http.client` calls when no
    context is passed — which is the path `tc_submit` takes. The earlier version set SSL_CERT_FILE
    and it failed on macOS while passing on Linux: certificates loaded through
    `set_default_verify_paths()` are read lazily, so the guard that was supposed to detect an
    unhonoured env var saw an empty CA list and could not tell "ignored" from "not loaded yet".
    Patching the hook depends on nothing about the host's trust store, so it means the same thing
    on every machine. (SSL_CERT_FILE remains the real-world escape hatch for a school CA; it is
    just not a sound thing to build a test on.)
    """
    url, cert = tls_server
    ctx = ssl.create_default_context(cafile=str(cert))      # check_hostname stays ON
    monkeypatch.setattr(ssl, "_create_default_https_context", lambda: ctx)
    # Connect by name, not by IP: the fixture certificate carries DNS:localhost, and hostname
    # verification is part of what "trusted" has to mean.
    r = tc_submit.check_code(url.replace("127.0.0.1", "localhost"), "AAAA-AAAA")
    assert r.get("ok") is False and "error" in r            # a refusal, not a transport failure


@needs_openssl
def test_the_same_server_is_untrusted_without_that_trust(tls_server):
    """The other half of the pair, so the test above cannot pass for an unrelated reason: the very
    same server, reached the same way, is refused when its certificate is not trusted."""
    url, _ = tls_server
    with pytest.raises(tc_submit.Untrusted):
        tc_submit.check_code(url.replace("127.0.0.1", "localhost"), "AAAA-AAAA")


def test_a_real_outage_is_still_reported_as_one():
    """The classification must not swallow genuine network failures into a certificate story."""
    with pytest.raises(tc_submit.Unreachable) as e:
        tc_submit.check_code("https://127.0.0.1:9", "AAAA-AAAA")
    assert not isinstance(e.value, tc_submit.Untrusted)


# -- HTTP is gone, on both sides ------------------------------------------------ #
def test_the_server_refuses_to_start_without_tls_at_all(tmp_path, monkeypatch):
    """The rule. Previously this served plain HTTP and printed a warning if the bind was
    reachable — so the dangerous configuration started fine and told you afterwards."""
    from gini_teaching_center import server
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "MATERIALS", tmp_path / "m")
    with pytest.raises(SystemExit) as e:
        server.serve(host="127.0.0.1", port=0)          # loopback is not an excuse either
    assert "only serves HTTPS" in str(e.value)


def test_the_refusal_says_how_to_make_a_certificate(tmp_path, monkeypatch):
    """A mandate you cannot satisfy is a mandate people work around. The error carries the openssl
    recipe INCLUDING the subjectAltName, because a bare CN fails looking like a trust problem."""
    from gini_teaching_center import server
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "MATERIALS", tmp_path / "m")
    with pytest.raises(SystemExit) as e:
        server.serve(host="0.0.0.0", port=0)
    msg = str(e.value)
    assert "openssl req -x509" in msg and "subjectAltName" in msg
    assert "mkcert" in msg                      # and the one-command route for a trusted local CA


@pytest.mark.parametrize("url", ["http://gini.example.edu", "http://127.0.0.1:8080",
                                 "http://localhost:8080", "HTTP://LOCALHOST:8080"])
def test_the_student_path_refuses_plain_http(url):
    """The code travels in a query string and the whole proof in a body. This path replaced the
    old client for everything a student does, and guarded none of it — while the client it
    replaced had guarded passwords from the beginning.

    localhost included: "it never leaves the machine" was the justification for the exemption, and
    loopback TLS removes it.
    """
    with pytest.raises(tc_submit.Insecure):
        tc_submit.check_code(url, "AAAA-AAAA")
    with pytest.raises(tc_submit.Insecure):
        tc_submit.submit(url, "AAAA-AAAA", {"ticket": "AAAA-AAAA"})


def test_insecure_is_not_an_outage():
    """It must NOT be catchable as Unreachable: retrying cannot fix an address, the server may be
    perfectly up, and "check your network" sends a student to the wrong place entirely."""
    assert not issubclass(tc_submit.Insecure, tc_submit.Unreachable)


def test_a_password_is_refused_over_http_even_on_localhost():
    from gini.agent.teaching_center import InsecureTransport, refuse_plaintext_password
    for url in ("http://localhost:8080", "http://127.0.0.1:8080", "http://elsewhere"):
        with pytest.raises(InsecureTransport):
            refuse_plaintext_password(url)
    refuse_plaintext_password("https://localhost:8443")         # https is fine anywhere


def test_there_is_no_longer_a_switch_that_turns_encryption_off():
    """The override existed for demos. An override that reaches a server which no longer speaks
    HTTP would only produce a more confusing failure."""
    from gini.app.context import Settings
    assert not hasattr(Settings(), "tc_allow_insecure")
    import inspect
    from gini.agent import teaching_center as TC
    assert "allow_insecure" not in inspect.getsource(TC)


@needs_openssl
def test_a_busy_port_is_a_sentence_not_a_traceback(certs, tmp_path, monkeypatch):
    """`serve` is the only thing here a person runs by hand, and the commonest way it fails is the
    last copy still holding the port. A stack trace reads as a bug in the Teaching Center and
    buries the one useful fact."""
    import socket
    from gini_teaching_center import server
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "MATERIALS", tmp_path / "m")
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    port = held.getsockname()[1]
    try:
        with pytest.raises(SystemExit) as e:
            server.serve(host="127.0.0.1", port=port,
                         tls_cert=str(certs[0]), tls_key=str(certs[1]))
    finally:
        held.close()
    msg = str(e.value)
    assert f"Port {port} is already in use" in msg
    assert "lsof" in msg and "--port" in msg        # how to find it, and how to work around it
