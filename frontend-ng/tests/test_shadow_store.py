"""Shadow-folder housekeeping — reset one, prune orphans (never automatic)."""
from gini.services import shadow_store as ss


def _seed(name):
    d = ss.shadows_root() / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "gini_sched.c").write_text("// student work\n")


def test_list_reset_and_prune(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    for n in ("M1", "M2", "OldBox"):
        _seed(n)
    assert set(ss.list_shadows()) == {"M1", "M2", "OldBox"}

    assert ss.reset_shadow("M1") is True                 # deletes one folder
    assert "M1" not in ss.list_shadows()
    assert ss.reset_shadow("nope") is False              # nothing to delete

    removed = ss.prune_shadows(keep={"M2"})              # M2 is a live element; OldBox is an orphan
    assert removed == ["OldBox"]
    assert ss.list_shadows() == ["M2"]                   # student's live work (M2) preserved


def test_missing_root_is_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "nope"))
    assert ss.list_shadows() == []
    assert ss.prune_shadows(keep=set()) == []
