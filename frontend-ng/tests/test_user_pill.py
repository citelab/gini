"""The toolbar's USER pill — your Teaching Center enrolment at a glance.

gBuilder is fully usable with no course at all, so "signed out" must read as a calm, neutral state,
never as an error. When you ARE enrolled, the pill answers the two questions a student actually has:
am I connected, and how much work is still due (0 is a real answer and worth showing).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gini.ui.main_window import MainWindow
from gini.ui.mode_indicator import ModeIndicator
from gini.ui.theme import ThemeManager


def _app():
    return QApplication.instance() or QApplication([])


def _pill():
    app = _app()
    return ModeIndicator(ThemeManager(app, "Dark"))


def _kinds(p):
    return {k: (text, w) for k, text, _c, w in p._pills()}


def test_signed_out_is_calm_not_an_error():
    p = _pill()
    p.set_enrolment("", False, 0)
    text, _ = _kinds(p)["user"]
    assert text == "sign in"
    assert p._user_color() == p.theme.theme.muted            # grey — you're not doing anything wrong
    assert "works fully without one" in p.toolTip()


def test_signed_in_shows_what_is_due():
    p = _pill()
    p.set_enrolment("mahesh", True, 3)
    text, _ = _kinds(p)["user"]
    assert text == "mahesh · 3 due"
    assert "3 assigned missions still to do" in p.toolTip()


def test_zero_due_is_shown_not_hidden():
    """'Nothing due' is information, not an empty state — the student should be able to SEE that
    they're clear rather than infer it from an absent badge."""
    p = _pill()
    p.set_enrolment("mahesh", True, 0)
    text, _ = _kinds(p)["user"]
    assert text == "mahesh · clear"
    assert p._user_color() == p.theme.theme.success
    assert "nothing due" in p.toolTip().lower()


def test_offline_keeps_your_identity_and_your_homework():
    """An unreachable course server must not make you look signed out — the assignments are cached."""
    p = _pill()
    p.set_enrolment("mahesh", False, 2)
    text, _ = _kinds(p)["user"]
    assert text == "mahesh · offline"
    assert p._user_color() == p.theme.theme.warning
    assert "cached" in p.toolTip()


def test_the_pill_is_a_click_target_before_it_is_ever_painted():
    """Hit boxes come from the layout, not from a recorded paint — so the pill is clickable the
    instant it exists. (It used to be dead until the first paintEvent had run.)"""
    p = _pill()
    p.set_enrolment("mahesh", True, 1)
    x0, x1 = p._ranges()["user"]
    assert x1 > x0
    assert p._hit((x0 + x1) / 2) == "user"
    assert p._hit(p._ranges()["model"][0] + 2) == "model"
    # Both remaining pills are click targets, so the dead spot is the gap between them.
    assert p._hit((p._ranges()["model"][1] + p._ranges()["user"][0]) / 2) == ""


def test_signing_in_with_no_credentials_sends_you_to_settings():
    app = _app()
    w = MainWindow(app)                      # conftest: fresh, un-enrolled home
    opened = []
    w._open_settings = lambda: opened.append(1)
    w._sign_in()
    assert opened                            # nothing to sign in WITH → Settings, not a silent no-op
    assert w.ctx.teaching_center is None


def test_not_enrolled_at_startup_leaves_the_pill_signed_out():
    app = _app()
    w = MainWindow(app)                      # conftest gives every test a fresh, un-enrolled home
    w._connect_teaching_center()
    app.processEvents()
    assert w.mode_indicator._student == ""
    assert _kinds(w.mode_indicator)["user"][0] == "sign in"


# -- explicit sign-in -------------------------------------------------------- #
def test_launching_gbuilder_does_NOT_sign_you_in():
    """Signing in is an act, not a side effect of launching an app. Even with saved credentials you
    start signed out — you might be demoing, on a shared machine, or simply not want to appear
    online to your instructor."""
    app = _app()
    w = MainWindow(app)
    s = w.ctx.settings
    s.tc_url, s.tc_course, s.tc_student = "http://localhost:8080", "cs4480", "mahesh"
    assert w.ctx.teaching_center is None            # credentials present, connection absent
    assert w.mode_indicator._student == ""


def _enrolled(w, *, session=""):
    """Configure the course, and optionally plant a cached session (as a previous sign-in would)."""
    from gini.app.paths import gini_home
    s = w.ctx.settings
    s.tc_url, s.tc_course, s.tc_student = "http://localhost:8080", "cs4480", "mahesh"
    if session:
        cache = gini_home() / "teaching_center"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "session_cs4480_mahesh.json").write_text('{"session": "%s"}' % session)
    return s


def test_a_cached_session_signs_you_in_without_asking_for_a_password_again():
    """You type your password once, not every launch. (If this ever asks, the dialog would BLOCK —
    which is exactly how this test caught it.)"""
    app = _app()
    w = MainWindow(app)
    _enrolled(w, session="LIVE-SESSION")
    w._sign_in()
    assert w.ctx.teaching_center is not None
    assert w.ctx.teaching_center.signed_in()


def test_signing_out_goes_local_without_forgetting_you():
    app = _app()
    w = MainWindow(app)
    s = _enrolled(w, session="LIVE-SESSION")
    w._sign_in()
    w._sign_out()
    assert w.ctx.teaching_center is None            # disconnected…
    assert s.tc_student == "mahesh"                 # …but your student id is still in Settings
    assert w.mode_indicator._student == ""


def test_signing_in_without_a_session_asks_for_a_password():
    """No cached session → the sign-in dialog. We stub it, but the point is that it IS reached: a
    silent no-op here would look like "the server is down" to a student who's simply not signed in."""
    app = _app()
    w = MainWindow(app)
    _enrolled(w)                                   # enrolled, but never signed in
    asked = []

    class FakeDialog:
        def __init__(self, *a, **k): pass
        def exec(self):
            asked.append(1)
            return 0                                # they hit Cancel

    from gini.ui import signin_dialog
    signin_dialog.SignInDialog = FakeDialog
    try:
        w._sign_in()
    finally:
        import importlib
        importlib.reload(signin_dialog)
    assert asked                                   # the password was asked for
    assert w.ctx.teaching_center is None           # cancelled → not signed in


def test_the_menu_lists_what_is_due_and_what_you_have_done():
    """The menu reads the CACHED manifest + local profile, so it opens instantly and works offline."""
    from PySide6.QtWidgets import QMenu
    from gini.domain import profile as P
    app = _app()
    w = MainWindow(app)

    class FakeTC:
        def available_lessons(self):
            return [{"id": "lab01", "title": "Build a LAN"},
                    {"id": "lab03", "title": "Private DB"}]
        def checkout_profile(self):
            prof = P.Profile("mahesh")
            prof.lessons["lab01"] = P.LessonRecord(lesson_id="lab01", concept="networking-basics",
                                                   best_band="gold", completed=True)
            return prof

    menu = QMenu(w)
    w._add_mission_items(menu, FakeTC())
    texts = [a.text() for a in menu.actions()]
    assert any("Due — 1" in t for t in texts)             # lab03 is outstanding…
    assert any("Private DB" in t for t in texts)
    assert any("Completed — 1" in t for t in texts)       # …lab01 is history, with its band
    assert any("Build a LAN" in t and "GOLD" in t for t in texts)


def test_sign_in_as_another_user_is_offered():
    """The signed-out menu offers both the saved user AND a 'type another username' option, so you can
    switch to e.g. the teacher account without editing Settings. Tested via the extracted helper (the
    full menu build crashes offscreen)."""
    from PySide6.QtWidgets import QMenu
    app = _app()
    w = MainWindow(app)
    w.ctx.settings.tc_student = "ravi"
    m = QMenu()
    w._add_signin_items(m)
    labels = [a.text() for a in m.actions()]
    assert any("Sign in as ravi" in l for l in labels)          # the saved user
    assert any("Sign in as another user" in l for l in labels)  # …and the type-a-name option


def test_sign_in_as_switches_the_username_and_forces_the_dialog(monkeypatch):
    app = _app()
    w = MainWindow(app)
    w.ctx.settings.tc_url = "http://x"; w.ctx.settings.tc_course = "c1"
    w.ctx.settings.tc_student = "ravi"

    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("prof", True)))
    called = {}
    monkeypatch.setattr(w, "_sign_in", lambda: called.setdefault("force", w._force_new_signin))

    w._sign_in_as()
    assert w.ctx.settings.tc_student == "prof"    # switched identity without touching Settings
    assert called.get("force") is True            # …and it forces the dialog, not a cached resume


def test_a_teacher_shows_the_role_not_a_due_count():
    """A teacher has no 'assignments due' — that's a student notion. The pill shows the role."""
    p = _pill()
    p.set_enrolment("prof", True, -1)                 # -1 = teacher sentinel
    text, _ = _kinds(p)["user"]
    assert text == "prof · teacher"
    assert "teacher" in p.toolTip()

    p.set_enrolment("ravi", True, 3)                  # a student still shows the count
    assert _kinds(p)["user"][0] == "ravi · 3 due"


def test_the_conversation_ribbon_scrolls_instead_of_widening_the_panel():
    """A teacher sees a channel per student — the ribbon must never force the dock wide."""
    app = _app()
    w = MainWindow(app)
    a = w.assistant
    a._channels = [{"id": f"teacher:s{i}", "kind": "teacher", "title": f"Student {i}"}
                   for i in range(15)]
    a._human_msgs = []
    a._rebuild_convo_ribbon()
    assert a._convo_scroll.isVisibleTo(w)
    assert a._convo_scroll.minimumWidth() <= 200       # a small floor — never demands to be wide
    assert a._convo_scroll.height() == 40              # fixed strip, horizontal scroll
