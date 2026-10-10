"""Reference modules in ~/.gini/scripts are updated when the user never touched them.

Seeding used to copy a reference module only when no file of that name existed, so an install kept
the first version it was given forever: rip_reference.lua's `cost + 1` would have outlived the
switch to link costs on every machine that already had it. Now a copy that is byte-for-byte a
version GINI shipped is replaced with the current one; anything else is somebody's work and stays.
"""
from pathlib import Path

from gini.services import orchestrator as O

EXAMPLES = Path(O.__file__).resolve().parents[1] / "data" / "examples"
OLD_RIP = Path(__file__).resolve().parent / "data" / "rip_reference_2026-08-23.lua"


def _seed(tmp_path):
    scripts, shared = tmp_path / "scripts", tmp_path / "shared"
    scripts.mkdir(); shared.mkdir()
    O._seed_examples(scripts, shared)
    return scripts


def test_a_fresh_install_gets_the_current_modules(tmp_path):
    scripts = _seed(tmp_path)
    assert (scripts / "rip_reference.lua").read_bytes() == \
        (EXAMPLES / "rip_reference.lua").read_bytes()


def test_an_untouched_old_copy_is_brought_up_to_date(tmp_path):
    scripts, shared = tmp_path / "scripts", tmp_path / "shared"
    scripts.mkdir(); shared.mkdir()
    (scripts / "rip_reference.lua").write_bytes(OLD_RIP.read_bytes())
    assert O._sha256(scripts / "rip_reference.lua") in O._SHIPPED_HASHES["rip_reference.lua"]
    O._seed_examples(scripts, shared)
    text = (scripts / "rip_reference.lua").read_text()
    assert "cost_of(iface)" in text and "cost + 1" not in text


def test_an_edited_copy_is_never_overwritten(tmp_path):
    scripts, shared = tmp_path / "scripts", tmp_path / "shared"
    scripts.mkdir(); shared.mkdir()
    mine = OLD_RIP.read_text() + "\n-- my change\n"
    (scripts / "rip_reference.lua").write_text(mine)
    O._seed_examples(scripts, shared)
    assert (scripts / "rip_reference.lua").read_text() == mine


def test_every_shipped_version_is_on_record():
    """Editing a reference module fails here until its new hash is added to _SHIPPED_HASHES --
    keeping the old one, so the copies of it already out there can still be recognised and
    updated next time."""
    import hashlib
    for name, hashes in O._SHIPPED_HASHES.items():
        current = hashlib.sha256((EXAMPLES / name).read_bytes()).hexdigest()
        assert current in hashes, f"{name} changed: add {current} to _SHIPPED_HASHES"
    assert hashlib.sha256(OLD_RIP.read_bytes()).hexdigest() in O._SHIPPED_HASHES["rip_reference.lua"]
