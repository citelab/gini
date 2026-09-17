import sys

from gini_doctor.stage1 import runner

PY = sys.executable


def test_a_missing_program_is_absent_not_an_exception():
    r = runner.run(["definitely-not-a-real-program-gini"])
    assert r.status == runner.ABSENT and r.returncode is None


def test_success_and_failure_keep_output_and_evidence():
    ok = runner.run([PY, "-c", "print('hello')"])
    assert ok.ok and ok.text() == "hello"
    bad = runner.run([PY, "-c", "import sys; sys.stderr.write('boom: no daemon\\n'); sys.exit(4)"])
    assert bad.status == runner.FAILED and bad.returncode == 4
    assert bad.evidence() == "boom: no daemon"


def test_a_hanging_command_is_stopped():
    r = runner.run([PY, "-c", "import time; time.sleep(30)"], timeout=1)
    assert r.status == runner.TIMEOUT


def test_a_command_waiting_for_input_does_not_block_the_doctor():
    r = runner.run([PY, "-c", "import sys; print(repr(sys.stdin.read()))"], timeout=10)
    assert r.ok and r.text() == "''"


def test_arguments_are_never_interpreted_by_a_shell():
    r = runner.run([PY, "-c", "import sys; print(sys.argv[1])", "$(echo hi); rm -rf /"])
    assert r.text() == "$(echo hi); rm -rf /"


def test_empty_command():
    assert runner.run([]).status == runner.ERROR
