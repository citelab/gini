"""A case, driven end to end without a server: the rules are the thing being tested."""
from __future__ import annotations

import dataclasses

import pytest
from gini_doctor.stage1 import casecode
from gini_doctor.stage1.report import NA, OK, Report

from gini_healthcenter import cases


def a_report(host, platform="linux", **facts) -> Report:
    r = Report(platform, host=host, collected_at="2026-09-17T12:00:00Z")
    for key, value in facts.items():
        key = key.replace("__", ".")
        if value is NA:
            r.set(key, NA)
        else:
            r.set(key, OK, value)
    return r


@pytest.fixture
def case():
    return cases.open_case("two lab boxes, one pulls and one does not", "mahesh", now=1000.0)


def test_opening_a_case_vends_a_code_the_doctor_can_read(case):
    """The code IS the consent, so there is no moment at which a case exists and cannot be
    joined — and the thing vended has to be the thing the doctor will accept."""
    assert casecode.valid(case.code)
    assert casecode.parse(case.pretty_code) == case.code
    assert case.state == cases.OPEN and case.opened_at == 1000.0


def test_a_case_with_nothing_to_say_for_itself_is_refused():
    assert "one line" in str(pytest.raises(cases.CaseError, cases.open_case, "  ", "mahesh").value)
    assert "record who" in str(pytest.raises(cases.CaseError, cases.open_case, "x", "").value)


def test_two_cases_never_share_a_code():
    codes = {cases.open_case("s", "me").code for _ in range(50)}
    assert len(codes) == 50


def test_a_closed_case_accepts_nothing_further_and_says_when_it_closed(case):
    p = cases.join(case, "tr-open-12", "linux")
    closed = cases.close(case, "stale credential in the shared home", now=1_700_000_000.0)
    for call in (lambda: cases.join(closed, "tr-open-18", "linux"),
                 lambda: cases.attach(closed, p, a_report("tr-open-12")),
                 lambda: cases.post_task(closed, p, ["engine"], "why"),
                 lambda: cases.diagnose(closed, "it was the credential", ["registry.engine"])):
        with pytest.raises(cases.CaseError) as e:
            call()
        assert "closed" in str(e.value) and "2023-11-14" in str(e.value)


def test_closing_twice_is_the_same_outcome_not_an_error(case):
    once = cases.close(case, "done", now=5.0)
    assert cases.close(once, "done again", now=99.0) == once


def test_a_stalled_case_keeps_taking_reports_and_can_be_resumed(case):
    """A model failure stalls a case; it never loses data. Stalled means the agent stopped, not
    the machines, so a doctor mid-poll can still hand in what it gathered."""
    p = cases.join(case, "tr-open-12", "linux")
    stalled = cases.stall(case, "the model returned nothing twice")
    assert stalled.state == cases.STALLED and "returned nothing" in stalled.stalled_why
    attached = cases.attach(stalled, p, a_report("tr-open-12"))
    assert attached.case == case.id
    back = cases.resume(stalled)
    assert back.state == cases.OPEN and back.stalled_why == ""
    assert "closed case cannot be resumed" in str(
        pytest.raises(cases.CaseError, cases.resume, cases.close(case, "x")).value)


def test_a_machine_from_another_case_is_not_addressable(case):
    other = cases.open_case("something else", "mahesh")
    stranger = cases.join(other, "laptop", "macos")
    for call in (lambda: cases.post_task(case, stranger, ["engine"], "why"),
                 lambda: cases.attach(case, stranger, a_report("laptop", "macos"))):
        assert "different case" in str(pytest.raises(cases.CaseError, call).value)


def test_a_joining_machine_is_identified_by_its_token_not_its_hostname(case):
    """A lab's hostnames are distinct today, but a container or a reimaged laptop can repeat one,
    and two machines sharing a row would merge two diagnoses into one."""
    a = cases.join(case, "tr-open-12", "linux")
    b = cases.join(case, "tr-open-12", "linux")
    assert a.id != b.id and a.token != b.token and len(a.token) == 32
    assert "hostname" in str(pytest.raises(cases.CaseError, cases.join, case, " ", "linux").value)


# -- tasks ------------------------------------------------------------------ #
def test_a_task_that_starts_a_container_must_carry_what_it_will_run(case):
    """Consent is declared, never assumed: that text is what the person answers Y to, and without
    it the doctor has nothing to show and skips the group."""
    p = cases.join(case, "tr-open-12", "linux")
    with pytest.raises(cases.CaseError) as e:
        cases.post_task(case, p, ["engine", "live"], "reproduce the false did-not-start")
    assert "live start a container" in str(e.value)
    ok = cases.post_task(case, p, ["engine", "live"], "reproduce it",
                         consent="Creates a throwaway compose project from an image you already "
                                 "have, and removes it.")
    assert ok.consent and ok.state == cases.POSTED


def test_a_read_only_task_needs_no_consent_text_and_the_doctor_decides_which_is_which(case):
    p = cases.join(case, "tr-open-12", "linux")
    assert cases.post_task(case, p, ["engine", "registry", "qt"], "narrow it down").consent == ""
    # Asked of the doctor's registry, so a third container-starting group added there cannot be
    # posted from here as though it only looked.
    assert cases.groups_needing_consent(cases.known_groups()) == ("live", "xv6")


def test_a_group_the_doctor_does_not_have_is_refused_with_the_ones_it_does(case):
    p = cases.join(case, "tr-open-12", "linux")
    with pytest.raises(cases.CaseError) as e:
        cases.post_task(case, p, ["engine", "telepathy"], "why")
    assert "no group called telepathy" in str(e.value) and "rootless" in str(e.value)


def test_a_task_with_no_reason_is_refused(case):
    p = cases.join(case, "tr-open-12", "linux")
    e = pytest.raises(cases.CaseError, cases.post_task, case, p, ["engine"], " ")
    assert "cannot question" in str(e.value)
    assert "at least one probe group" in str(
        pytest.raises(cases.CaseError, cases.post_task, case, p, [], "why").value)


def test_a_task_goes_posted_then_taken_then_answered_and_no_further(case):
    p = cases.join(case, "tr-open-12", "linux")
    t = cases.post_task(case, p, ["engine"], "the two differ only on image count",
                        posted_by="agent")
    taken = cases.take(t, now=10.0)
    assert (taken.state, taken.ended_at) == (cases.TAKEN, 0.0)
    done = cases.answer(taken, "report-7", now=20.0)
    assert (done.state, done.outcome, done.ended_at) == (cases.ANSWERED, "report-7", 20.0)
    assert "answered cannot become" in str(
        pytest.raises(cases.CaseError, cases.answer, done, "report-8").value)
    assert "posted cannot become answered" in str(
        pytest.raises(cases.CaseError, cases.answer, t, "report-9").value)
    assert "names the report" in str(pytest.raises(cases.CaseError, cases.answer, taken, "").value)


def test_a_refusal_is_an_answer_and_is_not_a_timeout(case):
    """A person saying no is an answer to the question the task asked. Reading it as silence
    would make the case look unfinished and invite the agent to ask again."""
    p = cases.join(case, "tr-open-12", "linux")
    t = cases.take(cases.post_task(case, p, ["xv6"], "measure the feed", consent="boots a kernel"))
    said_no = cases.decline(t, "not during the lab session", now=30.0)
    assert (said_no.state, said_no.outcome) == (cases.DECLINED, "not during the lab session")
    assert cases.decline(t).outcome == "declined at the machine"
    assert cases.expire(t).state == cases.EXPIRED
    assert "no doctor collected this" in cases.expire(t).outcome
    for terminal in (said_no, cases.expire(t)):
        assert "cannot become" in str(
            pytest.raises(cases.CaseError, cases.answer, terminal, "r").value)


# -- reports and the comparison --------------------------------------------- #
def test_a_report_answers_a_task_and_remembers_which(case):
    p = cases.join(case, "tr-open-12", "linux")
    t = cases.post_task(case, p, ["engine"], "why")
    attached = cases.attach(case, p, a_report("tr-open-12", engine__podman__images__count=9),
                            task=t, now=50.0)
    assert attached.answers == t.id and attached.host == "tr-open-12"
    assert "tr-open-12 (linux, 1 facts" in attached.summary
    assert cases.attach(case, p, a_report("tr-open-12")).answers == ""
    other = cases.open_case("s", "me")
    stray = cases.post_task(other, cases.join(other, "h", "linux"), ["engine"], "why")
    assert "different case" in str(
        pytest.raises(cases.CaseError, cases.attach, case, p, a_report("h"), task=stray).value)


def test_the_comparison_is_the_doctor_s_own_and_needs_two_machines(case):
    """Delegated, so the Health Center cannot develop a second opinion about what counts as a
    difference — including that a fact which cannot exist on a platform is not a fault."""
    p12 = cases.join(case, "tr-open-12", "linux")
    p18 = cases.join(case, "tr-open-18", "linux")
    mac = cases.join(case, "laptop", "macos")
    twelve = cases.attach(case, p12, a_report("tr-open-12", engine__podman__images__count=9,
                                              rootless__subuid="100000:65536"))
    eighteen = cases.attach(case, p18, a_report("tr-open-18", engine__podman__images__count=0,
                                                rootless__subuid="100000:65536"))
    laptop = cases.attach(case, mac, a_report("laptop", "macos", engine__podman__images__count=9,
                                              rootless__subuid=NA))

    assert "needs two machines" in str(
        pytest.raises(cases.CaseError, cases.differences, [twelve]).value)
    lab = cases.differences([twelve, eighteen], include_noisy=True)
    assert [r.key for r in lab.differ] == ["engine.podman.images.count"]
    assert lab.names == ["tr-open-12", "tr-open-18"]

    mixed = cases.differences([twelve, laptop], include_noisy=True)
    assert [r.key for r in mixed.platform] == ["rootless.subuid"]
    assert mixed.differ == []


# -- diagnosis -------------------------------------------------------------- #
def test_a_diagnosis_must_cite_what_it_rests_on(case):
    with pytest.raises(cases.CaseError) as e:
        cases.diagnose(case, "it was the stale credential", [])
    assert "nobody can check is not one" in str(e.value)
    assert "says what this was" in str(
        pytest.raises(cases.CaseError, cases.diagnose, case, "", ["registry.engine"]).value)


def test_a_diagnosis_recommends_and_has_nowhere_to_record_that_it_acted(case):
    """The agent diagnoses and recommends only. There is no "applied" field, and this test fails
    if one appears — which is the only way that promise survives a later refactor."""
    d = cases.diagnose(case, "one stale credential in the shared home broke pulls on every box",
                       ["registry.engine", "registry.anonymous", "report-7"],
                       ["docker logout docker.io", " "], by="agent", now=99.0)
    assert d.evidence == ("registry.engine", "registry.anonymous", "report-7")
    assert d.remedies == ("docker logout docker.io",)
    assert d.by == "agent" and d.at == 99.0
    assert {f.name for f in dataclasses.fields(cases.Diagnosis)} == {
        "case", "summary", "evidence", "remedies", "by", "at"}
