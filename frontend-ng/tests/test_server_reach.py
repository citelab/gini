"""The GINI server pill, the words for "not on the VPN", and Resume after a quit.

Three field reports, one week. Students off campus were shown
`<urlopen error [Errno 8] nodename nor servname provided, or not known>` at hand-in and at every
Resubmit, and reported it as "retry does not work"; the course server is reachable only on the
campus VPN, and nothing on screen said so. Students who quit gBuilder (or had it crash) while
recording came back to an empty Record box, and reported "pause and resume is unreliable" — the
Resume button appeared only after an explicit Pause. And a code armed off the VPN said
"recording locally" and nothing else, so the lab's questions never came.

So: a toolbar pill that says whether the course server can be reached and why not
(`services/tc_reach`), the same words in the hand-in errors (`tc_submit._wrap`), a bookmark written
at arm, and an offline arm that says why and fetches what it missed once the server is back.
"""
from __future__ import annotations

import os
import re
import socket
import urllib.error
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from gini.domain import proof as P
from gini.domain.ticket import mint

from gini.services import tc_reach as R
from gini.services import tc_submit

URL = "https://gini.example.edu"
CODE = mint(lambda n: bytes((i * 13 + 1) % 256 for i in range(n))).pretty


def _resolves(addr="10.1.2.3"):
    return lambda host, port, type=0: [(socket.AF_INET, type, 6, "", (addr, port))]


def _not_found(host, port, type=0):
    raise socket.gaierror(8, "nodename nor servname provided, or not known")


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _connects(*a, **k):
    return _Conn()


def _raises(exc):
    def connect(*a, **k):
        raise exc
    return connect


def _probe(resolve=None, connect=_connects, route=None, procs=()):
    return R.probe(URL, resolve=resolve or _resolves(), connect=connect,
                   route=lambda ip: dict(route or {"interface": "en0"}),
                   procs=lambda: list(procs), now=lambda: 0.0)


# --------------------------------------------------------------------------- #
# online, or offline and why
# --------------------------------------------------------------------------- #

def test_a_server_that_answers_is_online_and_says_nothing_more():
    r = _probe()
    assert r["online"] and r["reason"] == "" and R.pill_text(r) == "GINI server"
    assert R.advice(r) == ""


def test_a_name_that_does_not_resolve_is_not_found_and_points_at_the_vpn():
    r = _probe(resolve=_not_found)
    assert not r["online"] and r["reason"] == R.NOT_FOUND
    assert R.pill_text(r) == "GINI server · not found"
    assert "campus VPN" in R.advice(r) and "gini.example.edu" in R.advice(r)


def test_the_campus_client_running_but_not_connected_is_told_to_connect_it():
    """The commonest case on a student's laptop, and the one a first draft got wrong by saying
    'that is not the campus VPN' about the campus VPN's own client."""
    r = _probe(resolve=_not_found, procs=["pangps"])
    assert "GlobalProtect is running but not connected" in R.advice(r)
    assert R.network_line(r).startswith("direct — GlobalProtect is running")


def test_a_different_vpn_connected_is_named_as_not_the_campus_one():
    r = _probe(resolve=_not_found, route={"interface": "utun5"}, procs=["nordvpn"])
    assert r["tunnel"] and R.network_line(r) == "VPN (NordVPN)"
    assert "does not seem to be the campus VPN" in R.advice(r)


def test_no_answer_and_refused_are_told_apart():
    slow = _probe(connect=_raises(TimeoutError("timed out")))
    assert slow["reason"] == R.NO_ANSWER and "half-connected VPN" in R.advice(slow)
    down = _probe(connect=_raises(ConnectionRefusedError(61, "Connection refused")))
    assert down["reason"] == R.REFUSED and "Tell your instructor" in R.advice(down)


def test_no_course_server_configured_hides_the_pill():
    r = R.probe("", resolve=_not_found)
    assert not r["configured"] and R.advice(r) == ""


def test_a_broken_route_or_process_list_never_costs_the_answer():
    def boom(*a):
        raise RuntimeError("no route command here")
    r = R.probe(URL, resolve=_resolves(), connect=_connects, route=boom, procs=boom)
    assert r["online"] and r["route"] == {} and r["vpn"] == []


def test_the_details_are_what_a_report_needs():
    r = _probe(resolve=_not_found, procs=["pangps"])
    text = R.details(r, "6.22.17")
    for want in ("gini.example.edu:443", "OFFLINE — not found", "GlobalProtect",
                 "nodename nor servname", "6.22.17"):
        assert want in text


# --------------------------------------------------------------------------- #
# is it a VPN: the route decides, not the presence of a tunnel
# --------------------------------------------------------------------------- #

def test_a_route_through_a_tunnel_interface_is_a_vpn_and_wifi_is_not():
    """Every Mac has utun0..utunN at all times; only the ROUTE going through one means a VPN."""
    assert R.is_tunnel({"interface": "utun4"})
    assert R.is_tunnel({"interface": "cscotun0"}) and R.is_tunnel({"interface": "wg0"})
    assert not R.is_tunnel({"interface": "en0"}) and not R.is_tunnel({})


def test_on_windows_the_adapter_description_decides():
    assert R.is_tunnel({"interface": "Ethernet 3",
                        "description": "PANGP Virtual Ethernet Adapter"})
    assert R.is_tunnel({"interface": "Ethernet 2", "description": "Cisco AnyConnect Secure "
                                                                  "Mobility Client Virtual Miniport"})
    assert not R.is_tunnel({"interface": "Wi-Fi", "description": "Intel(R) Wi-Fi 6 AX201"})
    assert R.vpn_clients([], {"description": "PANGP Virtual Ethernet Adapter"}) == \
        ["GlobalProtect"]


def test_vpn_clients_are_recognised_by_process_name():
    assert R.vpn_clients(["vpnagentd", "finder"]) == ["Cisco Secure Client"]
    assert R.vpn_clients(["forticlient.exe", "tailscaled"]) == ["FortiClient", "Tailscale"]
    assert R.vpn_clients(["python3", "zsh"]) == []


# --------------------------------------------------------------------------- #
# the hand-in's own error says the same thing, in words
# --------------------------------------------------------------------------- #

def test_a_name_that_does_not_resolve_is_explained_not_quoted():
    e = tc_submit._wrap(urllib.error.URLError(socket.gaierror(8, "nodename nor servname")), URL)
    assert isinstance(e, tc_submit.Unreachable)
    assert "cannot find gini.example.edu" in str(e) and "campus VPN" in str(e)


def test_a_timeout_and_a_refusal_are_explained_too():
    slow = tc_submit._wrap(urllib.error.URLError(TimeoutError("timed out")), URL)
    assert "did not answer in time" in str(slow) and "VPN" in str(slow)
    down = tc_submit._wrap(urllib.error.URLError(ConnectionRefusedError(61, "refused")), URL)
    assert "turned the connection away" in str(down)


# --------------------------------------------------------------------------- #
# Resume after a quit or a crash
# --------------------------------------------------------------------------- #

def _recorder(store):
    from gini.domain.topology import Topology

    from gini.services.proof_recorder import ProofRecorder

    class Ctx:
        topology = Topology()
    return ProofRecorder(Ctx(), store=store)


def test_quitting_without_pause_still_offers_resume_next_launch(tmp_path):
    store = P.ChainStore(tmp_path)
    assert _recorder(store).arm(CODE)[0]
    again = _recorder(P.ChainStore(tmp_path))                 # gBuilder quit, or crashed
    assert not again.armed and again.paused_code() == CODE
    ok, msg = again.arm(again.paused_code())
    assert ok and "Resumed" in msg


def test_a_handed_in_code_is_not_offered_back(tmp_path):
    rec = _recorder(P.ChainStore(tmp_path))
    rec.arm(CODE)
    assert rec.generate_proof()["ok"]
    assert _recorder(P.ChainStore(tmp_path)).paused_code() == ""


# --------------------------------------------------------------------------- #
# the strip: an offline arm says why, and catches up later
# --------------------------------------------------------------------------- #

@pytest.fixture()
def strip(tmp_path):
    from gini.domain.topology import Topology
    from PySide6.QtWidgets import QApplication

    from gini.services.proof_recorder import ProofRecorder
    from gini.ui.proof_strip import ProofStrip
    from gini.ui.theme.manager import ThemeManager
    app = QApplication.instance() or QApplication([])

    class Ctx:
        settings = SimpleNamespace(tc_url=URL, tc_course="")
        topology = Topology("lab1")
    return ProofStrip(ThemeManager(app), ProofRecorder(Ctx(), store=P.ChainStore(tmp_path)))


def _hint(s):
    return re.sub("<[^>]+>", "", s.hint.text())


def test_an_offline_arm_records_and_says_why_and_that_questions_will_follow(strip):
    trouble = []
    strip.serverTrouble.connect(lambda: trouble.append(1))
    strip._on_arm_checked(CODE, {"unreachable": True,
                                 "error": "this computer cannot find gini.example.edu"})
    assert strip.recorder.armed and trouble
    text = _hint(strip)
    assert "cannot find gini.example.edu" in text and "questions will arrive" in text


def test_questions_fetched_later_also_say_which_lab_it_is(strip):
    """A re-ask gets the whole arm reply; hand-in grades the GINI lab it names."""
    strip._on_arm_checked(CODE, {"unreachable": True, "error": "x"})
    assert strip.recorder.gini_lab is None                  # armed offline: not known yet
    strip._on_questions_fetched({"ok": True, "activity": "comp310/a-lab-04", "title": "Sems",
                                 "questions": []})
    assert strip.recorder.gini_lab == "a-lab-04" and strip.recorder.activity_title == "Sems"


# --------------------------------------------------------------------------- #
# the pill and the main window's reaction to it
# --------------------------------------------------------------------------- #

def test_the_pill_is_hidden_without_a_server_and_clickable_with_one():
    from PySide6.QtWidgets import QApplication

    from gini.ui.mode_indicator import ModeIndicator
    from gini.ui.theme.manager import ThemeManager
    app = QApplication.instance() or QApplication([])
    m = ModeIndicator(ThemeManager(app))
    assert [p[0] for p in m._pills()] == ["model", "user"]
    m.set_server("GINI server · not found", False)
    assert [p[0] for p in m._pills()] == ["model", "server", "user"]
    x0, x1 = m._ranges()["server"]
    assert m._hit((x0 + x1) / 2) == "server"


def _window(armed=True, title=""):
    from gini.ui.main_window import MainWindow
    calls = []
    w = SimpleNamespace(
        _server_last={}, _server_checking=True,
        mode_indicator=SimpleNamespace(set_server=lambda *a: calls.append(("pill",) + a)),
        ctx=SimpleNamespace(log=lambda *a: calls.append(("log",) + a)),
        proof_strip=SimpleNamespace(flush_outbox=lambda: calls.append(("flush",)),
                                    fetch_questions=lambda: calls.append(("fetch",))),
        proof_recorder=SimpleNamespace(armed=armed, activity_title=title))
    return w, calls, (lambda r: MainWindow._on_server_checked(w, r))


def test_when_the_server_comes_back_the_work_owed_is_sent_and_questions_fetched():
    _w, calls, checked = _window()
    checked(_probe(resolve=_not_found))
    assert calls[0][:3] == ("pill", "GINI server · not found", False)
    calls.clear()
    checked(_probe())
    kinds = [c[0] for c in calls]
    assert "flush" in kinds and "fetch" in kinds and "log" in kinds


def test_a_code_that_already_has_its_reply_is_not_asked_again():
    _w, calls, checked = _window(title="Sems")
    checked(_probe(resolve=_not_found))
    checked(_probe())
    assert ("fetch",) not in calls and ("flush",) in calls
