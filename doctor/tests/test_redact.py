from gini_doctor.stage1.redact import Redactor


def test_home_paths_and_usernames_do_not_leave_the_machine():
    red = Redactor(homes=["/home/alice", "C:\\Users\\alice", "C:/Users/alice"], users=["alice"])
    assert red.text("/home/alice/.local/pipx/venvs/gini-toolkit/bin/python") == \
        "~/.local/pipx/venvs/gini-toolkit/bin/python"
    assert red.text("C:\\Users\\alice\\pipx\\python.exe") == "~\\pipx\\python.exe"
    assert red.text("owned by alice (uid 1000)") == "owned by <user> (uid 1000)"


def test_a_username_inside_another_word_is_left_alone():
    red = Redactor(homes=[], users=["lab"])
    assert red.text("label lab-01 lab") == "label lab-01 <user>"


def test_structured_values_are_redacted_throughout():
    red = Redactor(homes=["/home/bob"], users=["bob"])
    assert red.value(["/home/bob/x", 3, {"k": "bob"}]) == ["~/x", 3, {"k": "<user>"}]
