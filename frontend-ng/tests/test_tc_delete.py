"""Teaching Center fragment deletion + OTA deletion-propagation.

A fragment lives on the TC (the source of truth clients pull). Deleting it there must stop it
re-seeding to students, and a client's next sync must DROP the removed fragment — but never a
locally-authored or locally-edited one. The author menu needs to tell built-in from authored.
"""
import hashlib
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

# the teaching-center package sits beside frontend-ng
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "teaching-center"))

from gini.agent.teaching_center import TeachingCenterClient
from gini.domain import content as C
from gini.domain import fragments as F

import teacher as TC  # noqa: E402


def _write(fid, *, certified=False):
    d = C.ensure_user_content_dir()
    cert = "certified: true\n" if certified else ""
    (d / f"{fid}.yaml").write_text(
        f"id: {fid}\nlayer: core\nteaches: t\nsummary: s\n{cert}engine_version: '6.0'\n"
        "objectives:\n- {id: h, say: h, check: exists(host), level: 1}\n")
    F.reload()


def _served(fid):
    return {"id": fid, "engine_version": "6.0",
            "yaml": (f"id: {fid}\nlayer: core\nteaches: t\nsummary: s\nengine_version: '6.0'\n"
                     "objectives:\n- {id: h, say: h, check: exists(host), level: 1}\n")}


def test_delete_fragment_matches_spaced_or_slug_id():
    d = C.ensure_user_content_dir()
    (d / "simple LAN.yaml").write_text(
        "id: simple LAN\nlayer: core\nteaches: t\nsummary: s\nengine_version: '6.0'\n"
        "objectives:\n- {id: h, say: h, check: exists(host), level: 1}\n")
    F.reload()
    assert TC.delete_fragment("simple LAN")["ok"]           # deletable by its spaced internal id
    assert not (d / "simple LAN.yaml").exists()
    assert not TC.delete_fragment("nope")["ok"]             # nothing to delete → honest failure


def test_fragment_library_flags_authored_and_certified():
    _write("cap-lan", certified=True)
    lib = {f["id"]: f for f in TC.fragment_library()}
    assert lib["cap-lan"]["authored"] and lib["cap-lan"]["certified"]
    builtin = next(f for f in lib.values() if not f["authored"])
    assert builtin["authored"] is False                     # built-ins are not deletable


def test_pull_drops_a_removed_fragment_but_keeps_a_locally_edited_one():
    served = [_served("cap-lan"), _served("router"), _served("mine")]
    c = TeachingCenterClient("http://x", course="c1", student_id="s1",
                             cache_dir=tempfile.mkdtemp(),
                             transport=lambda m, p, b: (200, served))
    c.pull_content()
    d = C.user_content_dir()
    assert (d / "router.yaml").exists() and (d / "mine.yaml").exists()

    # user adopts 'mine' by editing it locally; TC drops both 'router' and 'mine'
    (d / "mine.yaml").write_text((d / "mine.yaml").read_text() + "# local edit\n")
    served[:] = [_served("cap-lan")]
    r = c.pull_content()
    assert r["removed"] == ["router"]                       # untouched OTA copy is dropped
    assert not (d / "router.yaml").exists()
    assert (d / "mine.yaml").exists()                       # edited copy is preserved
