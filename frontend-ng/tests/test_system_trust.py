"""gBuilder verifies HTTPS against the operating system's certificates, and says which it used.

Two field reports in one week: a Mac whose python.org Python had no certificate list at all, and a
Windows machine whose list held a cross-signed copy of the right root that OpenSSL could not get
past. Both operating systems verified the course server without complaint; only Python refused.
The cure is gini/app/trust.py. These tests pin what it promises: it uses the system when it can,
gets out of the way of an explicit SSL_CERT_FILE, never blocks a launch — and that a refusal from
the system's verifier (whose words differ, and on Windows are not even in English) still reaches
the student as the right sentence.
"""
from __future__ import annotations

import inspect
import ssl
import urllib.error

import pytest

from gini.app import trust
from gini.services import tc_submit


@pytest.fixture
def undecided():
    """Undo conftest's `_python_trust_in_tests` for the test that wants to watch the decision."""
    trust._reset()
    yield
    trust._reset()


# ---- the decision ------------------------------------------------------------------------------ #
def test_the_system_certificates_are_used_when_nothing_says_otherwise(undecided):
    calls = []
    assert trust.use_system_trust(env={}, inject=lambda: calls.append(1)) == trust.SYSTEM
    assert calls == [1]
    assert trust.mode() == trust.SYSTEM
    assert trust.describe() == "this computer's system certificates"


def test_the_decision_is_made_once(undecided):
    """Swapping the context after the first request changes nothing (urllib caches its opener),
    so a second call must not pretend to have done something."""
    calls = []
    trust.use_system_trust(env={}, inject=lambda: calls.append(1))
    assert trust.use_system_trust(env={}, inject=lambda: calls.append(1)) == trust.SYSTEM
    assert calls == [1]


@pytest.mark.parametrize("var", trust.OVERRIDES)
def test_a_named_certificate_file_wins_over_the_system(undecided, var):
    """`SSL_CERT_FILE=<cert.pem> gbuilder` is the documented way to trust a course server with a
    self-signed certificate. The system verifier ignores it, so naming one keeps Python's list."""
    calls = []
    assert trust.use_system_trust(env={var: "/x/cert.pem"},
                                  inject=lambda: calls.append(1)) == trust.PYTHON
    assert calls == []
    assert var in trust.describe()


def test_a_missing_or_broken_truststore_never_stops_a_launch(undecided):
    def boom():
        raise ImportError("No module named 'truststore'")
    assert trust.use_system_trust(env={}, inject=boom) == trust.PYTHON
    assert "No module named 'truststore'" in trust.describe()
    assert "Python's own certificate list" in trust.describe()


def test_gbuilder_decides_before_it_does_anything_else():
    """It must come before the first HTTPS request, and the safest place for that is first."""
    from gini import __main__ as entry
    src = inspect.getsource(entry.main)
    assert "use_system_trust()" in src
    assert src.index("use_system_trust()") < src.index("PySide6")


def test_truststore_is_a_declared_dependency():
    """An install without it falls back quietly, which is exactly how its absence would go
    unnoticed."""
    from pathlib import Path
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert '"truststore>=' in pyproject.read_text(encoding="utf-8")


# ---- what the student is told -------------------------------------------------------------------- #
def _told(message: str, code: int | None = None) -> str:
    err = ssl.SSLCertVerificationError(message)
    err.verify_message = message
    if code is not None:
        err.verify_code = code
    out = tc_submit._wrap(urllib.error.URLError(err), "https://gini.example:443")
    assert isinstance(out, tc_submit.Untrusted)
    return str(out)


def test_a_root_at_the_top_of_the_chain_is_not_called_a_self_signed_server():
    """The report that started this: an empty certificate list met the server's full chain, OpenSSL
    said 'self-signed certificate in certificate chain', and the student was told the SERVER'S
    certificate was self-signed. It is not, and the instructor went looking in the wrong place."""
    msg = _told("self-signed certificate in certificate chain", 19)
    assert "is self-signed" not in msg
    assert "certificate list does not include the authority" in msg
    assert "python.org" in msg


@pytest.mark.parametrize("text", ["unable to get issuer certificate",
                                  "unable to get local issuer certificate"])
def test_both_missing_issuer_refusals_say_the_same_thing(text):
    """'unable to get issuer certificate' was the Windows report; it means what the Mac's did."""
    assert "certificate list does not include the authority" in _told(text)


def test_a_server_certificate_that_really_is_self_signed_still_says_so():
    assert "is self-signed" in _told("self-signed certificate", 18)


def test_the_macos_verifier_distrusting_the_issuer_is_named():
    msg = _told("“gini.example” certificate is not trusted", -67843)
    assert "does not trust the authority" in msg


def test_a_windows_refusal_is_read_by_its_code_not_its_words():
    """FormatMessage answers in the user's language, so a French Windows says nothing a string
    match would find."""
    msg = _told("Une chaîne de certificats a été traitée, mais s'est terminée par un certificat "
                "racine qui n'est pas approuvé.", 0x800B0109)
    assert "does not trust the authority" in msg


def test_a_windows_code_arriving_signed_is_read_the_same():
    assert "does not trust the authority" in _told("x", 0x800B0109 - 2**32)


@pytest.mark.parametrize("code, words", [(-67602, "does not match the name"),
                                         (0x800B010F, "does not match the name"),
                                         (-67818, "has expired"),
                                         (0x800B0101, "has expired")])
def test_hostname_and_expiry_are_recognised_from_either_verifier(code, words):
    assert words in _told("opaque", code)


def test_the_message_names_the_certificate_list_it_was_checked_against():
    """An OS that distrusts a certificate and an interpreter with an empty file can produce the
    same words; the instructor reading the screenshot needs to know which."""
    msg = _told("self-signed certificate in certificate chain", 19)
    assert "Checked against Python's own certificate list" in msg
    assert "server is running" in msg and "instructor" in msg
