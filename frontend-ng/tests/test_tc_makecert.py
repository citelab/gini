"""`gini-tc --make-cert`: requiring a certificate without requiring work.

The Teaching Center serves HTTPS and nothing else. That is only a reasonable rule if getting a
certificate for a local server is trivial — otherwise it is a rule people route around, which is
worse than not having it. These tests pin the parts that make it safe to lean on.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center" / "src"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))

from gini_teaching_center import certs                          # noqa: E402

needs_openssl = pytest.mark.skipif(
    subprocess.run(["which", "openssl"], capture_output=True).returncode != 0,
    reason="openssl not available")


def test_the_certificate_lives_beside_the_course_data(tmp_path):
    """So `--data` moves the certificate with everything else, rather than leaving it somewhere
    the next person has to go looking for."""
    cert, key = certs.default_paths(tmp_path)
    assert cert.parent == tmp_path / "tls" and key.parent == tmp_path / "tls"


def test_the_bind_address_is_covered_when_it_is_a_real_one():
    assert certs.hosts_for("gini.cs.mcgill.ca") == ["localhost", "127.0.0.1", "gini.cs.mcgill.ca"]
    # 0.0.0.0 is "every interface", not a name anything can be verified against
    assert certs.hosts_for("0.0.0.0") == ["localhost", "127.0.0.1"]
    assert certs.hosts_for("127.0.0.1") == ["localhost", "127.0.0.1"]      # no duplicate


def test_ips_and_names_are_told_apart_in_the_san():
    san = certs._san(["localhost", "127.0.0.1", "gini.cs.mcgill.ca", "::1"])
    assert "DNS:localhost" in san and "IP:127.0.0.1" in san
    assert "DNS:gini.cs.mcgill.ca" in san and "IP:::1" in san


@needs_openssl
def test_it_makes_a_usable_certificate_with_a_subject_alt_name(tmp_path):
    """A certificate carrying only a CN is rejected by OpenSSL 3 and by macOS however it is signed,
    and it fails looking exactly like a trust problem — so the SAN is the whole point."""
    cert, key = certs.default_paths(tmp_path)
    path, made = certs.ensure(cert, key, certs.hosts_for("127.0.0.1"))
    assert made and path.is_file() and key.is_file()
    text = subprocess.run(["openssl", "x509", "-in", str(path), "-noout", "-text"],
                          capture_output=True, text=True, check=True).stdout
    assert "Subject Alternative Name" in text
    assert "DNS:localhost" in text and "127.0.0.1" in text


@needs_openssl
def test_an_existing_certificate_is_never_regenerated(tmp_path):
    """THE property. Making a fresh one would silently throw away whatever trust the old one had
    been given — a mkcert CA, an entry in a student's trust store — turning a convenience into a
    foot-gun on every restart."""
    cert, key = certs.default_paths(tmp_path)
    certs.ensure(cert, key)
    before = cert.read_bytes()
    path, made = certs.ensure(cert, key)
    assert made is False and path.read_bytes() == before


@needs_openssl
def test_the_private_key_is_not_readable_by_the_rest_of_the_machine(tmp_path):
    """It is a private key on a VM other people can log into."""
    cert, key = certs.default_paths(tmp_path)
    certs.ensure(cert, key)
    assert key.stat().st_mode & 0o077 == 0


@needs_openssl
def test_the_pair_it_makes_is_one_the_server_will_actually_load(tmp_path):
    """Generating something `_tls_context` then rejects would be a worse failure than not
    generating at all — it would look like the feature worked."""
    from gini_teaching_center import server
    cert, key = certs.default_paths(tmp_path)
    certs.ensure(cert, key)
    ctx = server._tls_context(str(cert), str(key))       # raises SystemExit if it will not load
    import ssl
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_a_machine_without_openssl_says_so_instead_of_failing_obscurely(tmp_path):
    def no_openssl(*a, **k):
        raise FileNotFoundError("openssl")

    cert, key = certs.default_paths(tmp_path)
    with pytest.raises(SystemExit) as e:
        certs.ensure(cert, key, run=no_openssl)
    assert "openssl" in str(e.value) and "--tls-cert" in str(e.value)


def test_a_failed_openssl_reports_its_own_error(tmp_path):
    class R:
        returncode = 1
        stderr = b"unknown option -wat"

    cert, key = certs.default_paths(tmp_path)
    with pytest.raises(SystemExit) as e:
        certs.ensure(cert, key, run=lambda *a, **k: R())
    assert "unknown option -wat" in str(e.value)
