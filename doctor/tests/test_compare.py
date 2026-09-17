import pytest

from gini_doctor.stage1.compare import compare, format_text
from gini_doctor.stage1.report import ABSENT, NA, OK, Report


def _r(host, platform="linux", **facts):
    r = Report(platform, host=host)
    for k, v in facts.items():
        key = k.replace("__", ".")
        if v is ABSENT or v is NA:
            r.set(key, v)
        else:
            r.set(key, OK, v)
    return r


def test_only_disagreements_are_shown_with_the_reference_first():
    good = _r("tr-open-12", engine__podman="4.9.3", gini__images=9, system__cpus=8)
    bad = _r("tr-open-18", engine__podman="4.9.3", gini__images=0, system__cpus=8)
    c = compare([good, bad])
    assert c.names == ["tr-open-12", "tr-open-18"]
    assert [row.key for row in c.differ] == ["gini.images"]
    assert c.differ[0].cells == ["9", "0"]


def test_not_applicable_is_a_platform_difference_not_a_fault():
    lin = _r("lab", "linux", rootless__subuid="100000:65536", engine__docker=ABSENT)
    mac = _r("mac", "macos", rootless__subuid=NA, engine__docker="27.1")
    c = compare([lin, mac])
    assert [row.key for row in c.platform] == ["rootless.subuid"]
    assert [row.key for row in c.differ] == ["engine.docker"]
    text = format_text(c)
    assert "platform differences" in text and "[absent]" in text


def test_noise_is_hidden_unless_asked_for():
    a = _r("a", system__disk__home__free_gb=10.5)
    b = _r("b", system__disk__home__free_gb=88.0)
    assert compare([a, b]).differ == []
    assert len(compare([a, b], include_noisy=True).differ) == 1


def test_same_hostname_twice_stays_distinguishable_and_policy_mismatch_is_flagged():
    a = _r("pc")
    b = _r("pc")
    b.policy = {"source": "live", "version": "hc-4"}
    c = compare([a, b])
    assert c.names == ["pc", "pc#2"] and c.policy_mismatch


def test_one_report_is_not_a_comparison():
    with pytest.raises(ValueError):
        compare([_r("only")])
