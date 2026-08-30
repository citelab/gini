"""What is parked, why, and what the Teaching Center owes us before it comes back.

The v1 Teaching Center is a deliberate simplification: activity codes, submissions, materials and
staff accounts, and nothing else. A whole set of gBuilder capabilities was built against the v0
server — assigned Missions, messaging, profile sync, a shared fragment library — and every endpoint
they call has been gone since that rewrite. They fail silently, because each call site is wrapped
in `try/except`, so the menus still offer them and nothing happens.

**These are parked, not abandoned.** Missions in particular is a headline feature; the plan is to
bring it back over a v1 server, not to lose it. So the code stays exactly where it is — compiled,
imported, refactored with everything around it, and covered by the suite. Only the doors are shut.

Commenting the code out was the alternative and it is a trap: commented code does not compile, is
not touched by refactors, is not covered by any test, and quietly stops matching the APIs around
it. Restoring it six months later means restoring something that no longer fits. A flag costs one
`if` and keeps the code honest.

Each entry says what would let it return, in the only terms that matter: the server endpoints that
must exist again. That list IS the specification for a future Teaching Center release.

Two tests keep this file from rotting — that every `code:` reference still exists, and that no
parked capability is reachable from a menu (`tests/test_parked.py`).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Parked:
    what: str                       # one line, in words the person who wants it back would use
    why: str
    needs: tuple[str, ...]          # server endpoints that must exist again
    code: tuple[str, ...]           # where the implementation still lives, untouched


#: Everything currently switched off, keyed by capability name.
PARKED: dict[str, Parked] = {
    "missions.server": Parked(
        what="Missions handed out by the course — the due list, and playing an assigned one.",
        why="The v0 endpoints that carried the lesson manifest and packs were removed in the v1 "
            "rewrite. The LOCAL practice catalog is unaffected and stays live; only the "
            "server-delivered half is parked.",
        needs=("GET /courses/{course}/manifest",
               "GET /lessons/{id}/pack",
               "GET /students/{id}/profile"),
        code=("agent/teaching_center.py: available_lessons, list_lessons, fetch_lesson, manifest",
              "ui/main_window.py: _add_mission_items, _play_assigned",
              "ui/assistant.py: _assigned_missions, _start_assigned_mission"),
    ),
    "missions.submit": Parked(
        what="Sending a finished mission's result to the course.",
        why="Same rewrite. Proof-of-activity hand-in (services/tc_submit.py) is the v1 path and is "
            "live — this is the separate, older mission-result channel.",
        needs=("POST /courses/{course}/submissions",),
        code=("agent/teaching_center.py: submit, flush, sync",
              "ui/assistant.py: _submit_to_center"),
    ),
    "profile.sync": Parked(
        what="Carrying your mastery profile between machines.",
        why="No server side. The profile still works locally, per machine.",
        needs=("GET /students/{id}/profile", "PUT /students/{id}/profile"),
        code=("agent/teaching_center.py: checkout_profile, checkin_profile",),
    ),
    "messaging": Parked(
        what="Channels, group chat, and messaging an instructor from inside gBuilder.",
        why="v1 has no messaging at all — it was cut deliberately, not lost.",
        needs=("GET /courses/{course}/channels",
               "GET|POST /courses/{course}/messages",
               "POST /courses/{course}/messages/report",
               "POST /courses/{course}/messages/delete"),
        code=("agent/teaching_center.py: channels, messages, send_message, report_message, "
              "delete_message",
              "ui/assistant.py: the conversation pane",
              "ui/main_window.py: _open_messages, _add_group_items"),
    ),
    "groups": Parked(
        what="Your team, and where they are on the mission.",
        why="No group model in v1.",
        needs=("GET /courses/{course}/group",),
        code=("agent/teaching_center.py: my_group", "ui/main_window.py: _add_group_items"),
    ),
    "fragments.library": Parked(
        what="Sharing authored mission fragments through the course, rather than one machine.",
        why="No fragment endpoints in v1. Authoring and playing fragments LOCALLY is unaffected — "
            "only upload/browse/delete against the server is parked.",
        needs=("GET|POST /api/fragments", "POST /api/fragments/delete"),
        code=("agent/teaching_center.py: upload_fragment, fragment_library, delete_fragment",
              "ui/fragment_manager.py: the Library tab"),
    ),
    "ai.proxy": Parked(
        what="Letting the tutor answer on your behalf when you are away.",
        why="Belongs to the messaging model, which v1 does not have.",
        needs=("POST /courses/{course}/ai/pref",),
        code=("agent/teaching_center.py: set_ai_proxy", "ui/main_window.py: _ai_proxy_consent"),
    ),
    "user.photo": Parked(
        what="A profile photo on the User pill.",
        why="No endpoint, and nothing in v1 displays one to anybody else.",
        needs=("POST /courses/{course}/photo",),
        code=("agent/teaching_center.py: set_photo", "ui/main_window.py: _set_photo"),
    ),
    "teacher.issue_codes": Parked(
        what="Minting assignment codes from gBuilder's Teacher menu.",
        why="NOT a missing endpoint — a conflict. These codes are minted locally and the course "
            "server has never heard of them, so a student who types one is told 'That code was not "
            "issued by this course.' The Teaching Center vends codes (/getcode), and two "
            "independent authorities is the bug. Kept because the minting itself is sound and a "
            "future offline mode may want it.",
        needs=("nothing — needs gBuilder to vend THROUGH the server instead",),
        code=("ui/proof_issue_dialog.py", "ui/main_window.py: _issue_codes"),
    ),
}


def on(name: str) -> bool:
    """Is this capability live?

    Unknown names are treated as PARKED rather than live: a typo in a gate should hide something
    that works, not expose something that does not. `tests/test_parked.py` catches the typo itself,
    so this only decides which way a mistake fails in the meantime.
    """
    return name not in PARKED and name in LIVE


#: Capabilities that are on. Listed rather than implied, so `on()` can fail closed and a test can
#: check that every name used in the code is a name declared here or in PARKED.
LIVE: frozenset[str] = frozenset({
    "missions.local",        # the practice catalog — never depended on a server
    "proof.submit",          # services/tc_submit.py: the v1 hand-in path
    "proof.verify",          # reading a proof file, offline
    "staff.signin",          # /auth/login, /auth/claim, /auth/logout — the surviving v0 third
})


def explain(name: str) -> str:
    """One line for a log or a disabled menu item."""
    p = PARKED.get(name)
    return f"{p.what} Parked: {p.why}" if p else ""
