import json

import pytest

from gini_doctor.stage1 import SCHEMA
from gini_doctor.stage1.report import ABSENT, ERROR, NA, OK, Report


def test_round_trip_keeps_every_fact_and_the_policy_it_ran_under(tmp_path):
    r = Report("linux", "cached", "hc-7", host="tr-open-12")
    r.groups.append("system")
    r.set("system.os.id", OK, "ubuntu")
    r.set("engine.podman.version", ABSENT)
    r.set("engine.docker.info", ERROR, detail="Cannot connect to the Docker daemon")
    r.set("rootless.subuid", NA)
    path = tmp_path / r.default_filename()
    r.save(str(path))

    back = Report.load(str(path))
    assert back.to_dict() == r.to_dict()
    assert back.policy == {"source": "cached", "version": "hc-7"}
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == SCHEMA


def test_only_ok_facts_carry_values_and_errors_keep_the_machines_own_words():
    r = Report("linux")
    r.set("a", ABSENT, value="ignored")
    r.set("b", ERROR, detail="x" * 900)
    assert "value" not in r.facts["a"]
    assert len(r.facts["b"]["detail"]) == 500


def test_odd_values_are_stringified_not_fatal():
    r = Report("linux")
    r.set("x", OK, object())
    r.set("y", OK, (1, "two", {"three": 3}))
    assert isinstance(r.facts["x"]["value"], str)
    assert r.facts["y"]["value"][:2] == [1, "two"]


@pytest.mark.parametrize("bad", [{"schema": "other/1"}, {"schema": SCHEMA, "facts": []},
                                 {"schema": SCHEMA, "facts": {"k": {"status": "maybe"}}}])
def test_foreign_or_malformed_reports_are_refused(bad):
    with pytest.raises(ValueError):
        Report.from_dict(bad)


def test_unknown_status_is_a_programming_error():
    with pytest.raises(ValueError):
        Report("linux").set("k", "fine")


def test_default_filename_is_safe_on_every_platform():
    r = Report("windows", host='lab:pc/7*"x', collected_at="2026-09-17T14:03:11Z")
    name = r.default_filename()
    assert name == "gini-doctor-lab_pc_7__x-20260917T140311Z.json"
