"""Which certificates gBuilder trusts: the operating system's, not Python's own list.

Python does not ask the operating system whether a certificate is good. It hands the question to
its own OpenSSL with its own list of authorities, and that list is wherever the interpreter's
build put it. Two field reports in one week came from the gap between the two:

* **macOS, a Python with no list at all.** The python.org installer ships an interpreter whose
  certificate file does not exist until someone runs "Install Certificates.command", and almost
  nobody does. Safari works, because Safari asks the keychain; gBuilder trusted nothing. The
  course server sends its whole chain up to the self-signed USERTrust root, so the refusal read
  "self-signed certificate in certificate chain" — and gBuilder told the student the server's
  certificate was self-signed, which it is not. That student's outbox held three hand-ins and
  548 failed attempts going back seven weeks.
* **Windows, a list with the wrong copy in it.** Python loads the Intermediate store as trusted
  alongside the Root store, and Windows had cached a cross-signed USERTrust (issued by the old
  Comodo "AAA Certificate Services") in it. OpenSSL took the first certificate with the right
  name, found its issuer missing, and gave up without trying the real root two entries away:
  "unable to get issuer certificate". Windows' own chain builder backtracks and would have
  passed.

Both machines were perfectly able to verify the server; only Python could not. So gBuilder asks
the operating system instead, through `truststore` — the keychain on macOS, the Windows chain
builder (with its root updates) on Windows, the system bundle on Linux. It is what pip itself
uses by default. A student has nothing to install or run.

**`SSL_CERT_FILE` / `SSL_CERT_DIR` still win.** They are how an instructor points gBuilder at a
course server with a self-signed certificate (teaching-center/README.md documents exactly that),
and the system verifier would ignore them. Someone who names a certificate file has made the
choice for us.

**It must never stop a launch.** If truststore is missing (an install that predates the
dependency) or cannot load, gBuilder carries on with Python's own list — which is what every
release before this one did, and works on most machines — and `describe()` says which list was
used, so a certificate refusal can name it.

**It must run before the first HTTPS request.** `urllib.request.urlopen` builds its opener once
and that opener fixes its SSL context when it is constructed (see `_fresh_opener` in
tests/test_tc_tls.py for the day that cost), so swapping the context after the first request
changes nothing. `gini.__main__.main` calls this before it does anything else.
"""
from __future__ import annotations

import os
import ssl
from collections.abc import Callable, Mapping

SYSTEM = "system"
PYTHON = "python"

#: Environment variables that name a certificate list explicitly. OpenSSL reads them; the
#: operating system's verifier does not.
OVERRIDES = ("SSL_CERT_FILE", "SSL_CERT_DIR")

_mode: str | None = None
_why = ""


def use_system_trust(env: Mapping[str, str] | None = None,
                     inject: Callable[[], None] | None = None) -> str:
    """Verify HTTPS against the operating system's certificates from now on. Returns the mode.

    Idempotent: the first call decides, later calls report that decision. Never raises.
    `env` and `inject` exist for the tests; the app passes neither.
    """
    global _mode, _why
    if _mode is not None:
        return _mode
    env = os.environ if env is None else env
    named = [k for k in OVERRIDES if env.get(k)]
    if named:
        _mode, _why = PYTHON, f"{named[0]} is set"
        return _mode
    try:
        if inject is None:
            import truststore
            inject = truststore.inject_into_ssl
        inject()
    except Exception as e:                                   # noqa: BLE001 — never block a launch
        _mode, _why = PYTHON, f"the system certificates could not be used: {e}"
        return _mode
    _mode, _why = SYSTEM, ""
    return _mode


def mode() -> str:
    """`SYSTEM` once `use_system_trust` has succeeded, otherwise `PYTHON`."""
    return _mode or PYTHON


def describe() -> str:
    """Which certificate list a refusal was checked against, in words for an error message.

    An instructor reading a student's screenshot needs this first: a refusal by the operating
    system and a refusal by an interpreter with an empty file are different problems with
    different fixes, and they can carry the same words.
    """
    if mode() == SYSTEM:
        return "this computer's system certificates"
    paths = ssl.get_default_verify_paths()
    where = paths.cafile or paths.openssl_cafile or "no certificate file"
    if paths.cafile is None and not os.path.exists(paths.openssl_cafile or ""):
        where += ", which does not exist"
    reason = f"; {_why}" if _why else ""
    return f"Python's own certificate list ({where}{reason})"


def _reset() -> None:
    """For tests: forget the decision, so the next call decides again."""
    global _mode, _why
    _mode, _why = None, ""
