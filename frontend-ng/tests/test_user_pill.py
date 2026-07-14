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
    assert p._hit(2) == ""                   # the Mode pill isn't clickable


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


def test_the_user_menu_signs_you_in_on_demand():
    app = _app()
    w = MainWindow(app)
    s = w.ctx.settings
    s.tc_url, s.tc_course, s.tc_student = "http://localhost:8080", "cs4480", "mahesh"
    w._sign_in()
    assert w.ctx.teaching_center is not None        # the client exists (the probe runs off-thread)


def test_signing_out_goes_local_without_forgetting_you():
    app = _app()
    w = MainWindow(app)
    s = w.ctx.settings
    s.tc_url, s.tc_course, s.tc_student = "http://localhost:8080", "cs4480", "mahesh"
    w._sign_in()
    w._sign_out()
    assert w.ctx.teaching_center is None            # disconnected…
    assert s.tc_student == "mahesh"                 # …but your credentials are still in Settings
    assert w.mode_indicator._student == ""


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
