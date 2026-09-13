"""Is there a newer gBuilder — and what would installing it actually take?

Asked by a student, answered honestly. The check is READ-ONLY and does not install anything, which
is a deliberate stopping point rather than an unfinished one: a RELEASED gBuilder pins its
container images to its own exact version (`setup/images.image_tag` — only a dev build falls back
to `latest`), so upgrading the Python package alone leaves a machine whose app wants
`gini-xv6:6.11.4` and whose Docker has 6.11.3. The
next launch does recover — `services/bootstrap` reports UPDATE and offers to refresh the images —
but a one-click button that silently leaves Run broken until a restart is worse than a sentence
telling the student the command and what happens next. So this prints the command; it does not run
it. When the image half is wired to the same button, that is the moment to add the button.

**Nothing is sent.** One GET to PyPI's public JSON for `gini-toolkit`, no identifiers, no version
of ours in the request, no report of who asked. And it only ever happens when a student picks the
menu item — never on launch. A lab machine that cannot reach the internet gets a sentence saying
so, and the app is otherwise unaffected.

**Why the install method matters.** The same upgrade is three different commands, and giving the
wrong one is worse than giving none: `pip install --upgrade` inside a pipx venv half-breaks it, and
either command run against a source checkout would REPLACE the tree the student is working in with
a release build. So a checkout is detected and refused by name rather than guessed at.

Only the toolkit is named in the upgrade command. `gini-core` follows because the floor in
`pyproject.toml` always equals the release version — which is not a convention anyone has to
remember, it is checked by `scripts/release.sh`. That was learned the hard way: a 6.8.0 toolkit
sat beside a 6.7.0 core for a whole release because the floor said `>=6.3.2` and pip does not
upgrade a dependency an existing floor already satisfies.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

PYPI = "https://pypi.org/pypi/gini-toolkit/json"
TIMEOUT = 8.0        # a student is watching a dialog; a wedged mirror must not hold it open

# How this copy of gBuilder was installed. Each one is a different upgrade command.
PIPX = "pipx"
VENV = "venv"
USER = "pip"
SOURCE = "source"        # an editable install / a checkout: upgrading would overwrite their work
UNKNOWN = "unknown"


def _release_key(v: str) -> tuple:
    """Order two version strings. `(numbers…, 1)` for a release, `(numbers…, 0)` for a pre-release.

    `packaging` is not a declared dependency of this package (setuptools-scm pulls it at BUILD
    time, which is not the same thing), so it is used when present and re-derived when not. The
    fallback only has to handle the two shapes GINI actually produces — `6.11.3` from a tag and
    `6.11.4.dev3+g1a2b3c` from a checkout between tags — and the trailing flag is what keeps the
    second one BELOW the release it is leading up to.
    """
    try:
        from packaging.version import Version
        return (Version(str(v)),)
    except Exception:                            # noqa: BLE001 — not installed, or not a version
        pass
    head = str(v).strip().split("+", 1)[0]
    nums = tuple(int(n) for n in re.findall(r"\d+", head.split(".dev")[0].split("rc")[0]))
    nums = nums + (0,) * (4 - len(nums))
    pre = 0 if re.search(r"(\.dev|rc|a|b)\d", head) else 1
    return (nums[:4], pre)


def is_newer(candidate: str, installed: str) -> bool:
    """True if `candidate` is a version worth upgrading to. False on anything unparseable —
    an unknown version is not a reason to tell a student to reinstall."""
    if not candidate or not installed:
        return False
    try:
        return _release_key(candidate) > _release_key(installed)
    except Exception:                            # noqa: BLE001
        return False


def install_kind() -> str:
    """How this gBuilder got here, judged from where its own module actually sits.

    The module path rather than the distribution metadata, because the two can disagree and the
    path is the one that matters. A checkout on `PYTHONPATH` shadows an installed wheel — the
    arrangement `dev.sh check` exists to untangle — and there `direct_url.json` would faithfully
    report the pipx install while the code actually running, and the code an upgrade would fail to
    replace, is the tree. That is the expensive direction to be wrong in: it tells a developer to
    pip-install over their own working copy.

    Both layouts in this repo are `<dist>/src/gini/…`, so the checkout test is exact rather than a
    heuristic, and it holds for an editable install too — PEP 660 leaves `__file__` in the source
    tree.
    """
    try:
        here = str(Path(__file__).resolve()).replace("\\", "/").lower()
    except Exception:                            # noqa: BLE001 — a frozen or exotic loader
        return UNKNOWN
    if "/src/gini/" in here:
        return SOURCE
    prefix = sys.prefix.replace("\\", "/").lower()
    if "/pipx/venvs/" in here or "/pipx/venvs/" in prefix:
        return PIPX
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return VENV
    if "site-packages" in here or "dist-packages" in here:
        return USER
    return UNKNOWN


def upgrade_command(kind: str) -> str:
    """The one command that upgrades this kind of install. Empty for the kinds we refuse to guess."""
    if kind == PIPX:
        return "pipx upgrade gini-toolkit"
    if kind in (VENV, USER):
        # The interpreter's full path, not "python3". The student pastes this into a terminal that
        # may not have the venv activated, where "python3" would upgrade a DIFFERENT interpreter's
        # copy — succeeding loudly and changing nothing they can see.
        exe = sys.executable or "python3"
        quoted = f'"{exe}"' if " " in exe else exe
        return f"{quoted} -m pip install --upgrade gini-toolkit"
    return ""                                    # SOURCE and UNKNOWN are told in words instead


def latest_version(timeout: float = TIMEOUT) -> str:
    """The newest `gini-toolkit` on PyPI. Raises `OSError` if it cannot be reached."""
    with urllib.request.urlopen(PYPI, timeout=timeout) as r:
        return str((json.loads(r.read() or b"{}").get("info") or {}).get("version") or "")


def check(timeout: float = TIMEOUT) -> dict:
    """One call, one dict, never an exception. Keys:

    `ok`         the question was answered (False means the network, not the version)
    `installed`  what is running here
    `latest`     what PyPI has, "" if unknown
    `newer`      True if `latest` is worth moving to
    `kind`       one of PIPX / VENV / USER / SOURCE / UNKNOWN
    `command`    what to type, "" when we decline to say
    `error`      a sentence for the student when `ok` is False

    Failure is always a sentence and never an exception, for the same reason `tc_submit` works that
    way: this runs off the GUI thread, where a raised exception reaches nobody.
    """
    from ..version import gini_version

    installed = gini_version()
    kind = install_kind()
    try:
        latest = latest_version(timeout)
    except Exception as e:                       # noqa: BLE001 — offline, proxy, DNS, TLS, all one
        return {"ok": False, "installed": installed, "latest": "", "newer": False,
                "kind": kind, "command": "",
                "error": f"Could not reach PyPI to ask ({e}). This does not affect anything you "
                         f"are working on — gBuilder never needs the internet to run a lab."}
    newer = is_newer(latest, installed)
    return {"ok": True, "installed": installed, "latest": latest, "newer": newer,
            "kind": kind, "command": upgrade_command(kind) if newer else "", "error": ""}


#: Said after every "you can upgrade" answer. The image half is not a footnote: gBuilder asks for
#: images tagged with its OWN version, so a student who upgrades the package and goes straight back
#: to a lab finds Run broken, with nothing on screen connecting the two events.
_AFTER = ("\n\nAfter upgrading, the next launch will offer to refresh the container images — "
          "gBuilder uses images built for its own version, so the two move together. That "
          "download takes a few minutes and happens once.")


def advice(result: dict) -> dict:
    """Turn a `check()` result into what to SAY: `{headline, detail, command}`.

    Here rather than in the dialog because deciding what to tell someone is a decision, and a
    decision belongs where it can be tested — the UI half is then only a QMessageBox with no
    judgement in it. `command` is empty whenever there is nothing safe to offer, which is what the
    dialog keys its Copy button off.
    """
    installed = result.get("installed") or "unknown"
    latest = result.get("latest") or "?"
    if not result.get("ok"):
        return {"headline": f"gBuilder {installed} — could not check for updates.",
                "detail": result.get("error", ""), "command": ""}
    if not result.get("newer"):
        if is_newer(installed, latest):
            return {"headline": f"gBuilder {installed} is ahead of the newest release ({latest}).",
                    "detail": "This is a development build, so there is nothing to upgrade to — "
                              "upgrading would move you backwards.", "command": ""}
        return {"headline": f"gBuilder {installed} is up to date.",
                "detail": f"{latest} is the newest release on PyPI.", "command": ""}

    kind, command = result.get("kind"), result.get("command") or ""
    head = f"gBuilder {latest} is available — you have {installed}."
    if kind == SOURCE:
        return {"headline": head, "command": "",
                "detail": "This copy runs from a source checkout, so do not install it with pip — "
                          "that would replace the tree you are working in. Update it the way you "
                          "got it:\n\n    git pull && ./scripts/dev.sh install" + _AFTER}
    if not command:
        return {"headline": head, "command": "",
                "detail": "GINI could not tell how this copy was installed, so it will not guess a "
                          "command that might damage it. Upgrade it the way you installed it."
                          + _AFTER}
    return {"headline": head, "command": command,
            "detail": f"Run this in a terminal:\n\n    {command}" + _AFTER}
