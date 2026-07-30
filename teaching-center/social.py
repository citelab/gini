"""Presence, group progress, and chat — the social plane of the Teaching Center.

Two rules are enforced HERE, in the server, not in the UI, because a rule that lives in a client is a
suggestion:

  1. **Student↔student DMs are outside the staff plane.** Not readable by the teacher, not readable
     by ProfAI, not surfaced in the console. Not even "auditable on demand". The only way a staff
     member ever sees one is if a student **reports** it — consent-based disclosure: you see it
     because someone showed you, not because you were watching.

     This is a CHOSEN blind spot. Harassment and collusion, if they happen, happen there. The trade
     is deliberate: a backchannel students don't trust is one they don't use — they move to WhatsApp,
     where the instructor has no recourse at all.

  2. **Course-facing channels are read by staff.** The group channel is a shared workspace, and
     ProfAI reading it is the entire point of the digest ("group 4 has been stuck for 40 minutes").
     Students are told this at sign-in, plainly.

Retention: course channels keep the term; student↔student DMs are short-lived (DM_TTL_DAYS) — less
to leak, less to regret.

Storage (COURSE_ROOT/data/): messages.jsonl · presence.json · reports.jsonl
Boring on purpose. Classroom scale. The functions are the seam if this ever needs a real datastore.
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

DM_TTL_DAYS = 14
ONLINE_WINDOW = 90          # seconds since last heartbeat to still count as "here"

# channel kinds
TEACHER, GROUP, DM = "teacher", "group", "dm"


def _now() -> float:
    return time.time()


class Social:
    def __init__(self, root: str | Path, course) -> None:
        self.course = course                      # teacher.Course — roster/groups live there
        from store import Store
        self.store = Store(root)

    # -- presence ------------------------------------------------------------ #
    def heartbeat(self, who: str, progress: dict | None = None) -> dict:
        """A client says 'I'm here', and (optionally) where it is on the current mission. Progress is
        what makes a group view useful — 'Ana is on Level 3' beats a green dot."""
        prog = None
        if progress:
            prog = {
                "lesson_id": str(progress.get("lesson_id", ""))[:64],
                "title": str(progress.get("title", ""))[:120],
                "level": int(progress.get("level", 0) or 0),
                "met": int(progress.get("met", 0) or 0),
                "total": int(progress.get("total", 0) or 0),
                "band": str(progress.get("band", ""))[:16],
            }
        self.store.put_presence(who, _now(), prog)
        return {"ok": True}

    def online(self, who: str) -> bool:
        rec = self.store.presence(who)
        return (_now() - rec.get("ts", 0)) < ONLINE_WINDOW

    def presence_of(self, who: str) -> dict:
        rec = self.store.presence(who)
        return {"online": (_now() - rec.get("ts", 0)) < ONLINE_WINDOW,
                "last_seen": rec.get("ts", 0), "progress": rec.get("progress", {})}

    # -- group view ---------------------------------------------------------- #
    def my_group(self, who: str) -> dict:
        """Who's on my team, are they here, and where are they on the mission. A class with no groups
        simply gets {group: '', members: []} — absence, not an error."""
        g = self.course.group_of(who)
        if not g:
            return {"group": "", "members": []}
        names = {r["id"]: r.get("name", r["id"]) for r in self.course.roster()}
        members = []
        for sid in self.course.members_of(g):
            pr = self.presence_of(sid)
            members.append({"id": sid, "name": names.get(sid, sid), "me": sid == who,
                            "online": pr["online"], "progress": pr["progress"],
                            "photo": self.store.photo(sid)})
        members.sort(key=lambda m: (not m["me"], m["id"]))
        return {"group": g, "members": members}

    # -- chat: addressing + access control ------------------------------------ #
    def _peers(self, who: str) -> set[str]:
        g = self.course.group_of(who)
        return set(self.course.members_of(g)) - {who} if g else set()

    def can_send(self, who: str, to: str) -> tuple[bool, str]:
        """`to` is 'teacher' | 'group' | a student id."""
        if to == TEACHER:
            return True, ""
        if to == GROUP:
            return (True, "") if self.course.group_of(who) else (False, "You're not in a group.")
        if to in self._peers(who):
            return True, ""
        return False, "You can only message people in your own group."

    def send(self, who: str, to: str, body: str, *, kind: str = "human",
             persona_version: str = "", from_label: str = "") -> dict:
        ok, err = self.can_send(who, to)
        if not ok and kind == "human":
            return {"ok": False, "error": err}
        body = (body or "").strip()
        if not body:
            return {"ok": False, "error": "Empty message."}
        if to == GROUP:
            channel, chan_kind = f"group:{self.course.group_of(who)}", GROUP
        elif to == TEACHER:
            channel, chan_kind = f"teacher:{who if kind == 'human' else from_label or who}", TEACHER
        else:
            channel, chan_kind = f"dm:{'|'.join(sorted((who, to)))}", DM
        rec = {"id": secrets.token_urlsafe(8), "ts": _now(),
               "channel": channel, "chan_kind": chan_kind,
               "from": from_label or who, "author": who, "to": to,
               "kind": kind, "persona_version": persona_version, "body": body[:4000]}
        self.store.add_message(rec)
        return {"ok": True, "message": rec}

    def reply_to_student(self, student: str, body: str, *, from_label: str, kind: str = "ai",
                         persona_version: str = "") -> dict:
        """Post INTO A STUDENT'S instructor thread. Addressed explicitly, because the channel is
        keyed by the student — a reply must land where they asked, not in a new thread.

        `kind` is load-bearing: 'ai' renders as **ProfAI** and is queued for review; 'human' is the
        teacher speaking as themselves (a correction). The student must always be able to tell which
        of the two just answered them."""
        rec = {"id": secrets.token_urlsafe(8), "ts": _now(),
               "channel": f"teacher:{student}", "chan_kind": TEACHER,
               "from": from_label, "author": from_label, "to": student,
               "kind": kind, "persona_version": persona_version, "body": (body or "")[:4000]}
        self.store.add_message(rec)
        return {"ok": True, "message": rec}

    def post_to_channel(self, channel: str, body: str, *, from_label: str,
                        kind: str = "human", persona_version: str = "") -> dict:
        """Post directly into a known channel id (teacher:<student> or group:<g>). Used by the
        instructor console to reply from the mailbox, including to a whole group."""
        chan_kind = GROUP if channel.startswith("group:") else TEACHER
        rec = {"id": secrets.token_urlsafe(8), "ts": _now(),
               "channel": channel, "chan_kind": chan_kind,
               "from": from_label, "author": from_label, "to": channel,
               "kind": kind, "persona_version": persona_version, "body": (body or "")[:4000]}
        self.store.add_message(rec)
        return {"ok": True, "message": rec}

    def _visible_channels(self, who: str, role: str) -> callable:
        """The access-control heart. A predicate over a message record."""
        if role == "teacher":
            # Staff see COURSE-FACING channels only. A student↔student DM is not theirs to read —
            # and this is enforced here, in the server, so no console change can quietly widen it.
            return lambda m: m["chan_kind"] in (TEACHER, GROUP)
        mine_dm = {f"dm:{'|'.join(sorted((who, p)))}" for p in self._peers(who)}
        g = self.course.group_of(who)
        allowed = set(mine_dm)
        allowed.add(f"teacher:{who}")
        if g:
            allowed.add(f"group:{g}")
        return lambda m: m["channel"] in allowed

    def inbox(self, who: str, role: str = "student", since: float = 0.0,
              limit: int = 500, include_deleted: bool = False) -> list[dict]:
        self._purge_expired_dms()
        visible = self._visible_channels(who, role)
        out = [m for m in self.store.messages(include_deleted=include_deleted)
               if m.get("ts", 0) > since and visible(m)]
        return out[-limit:]

    def channels(self, who: str, role: str = "student") -> list[dict]:
        """The channel list the client shows. Derived, so it can't drift from what's readable."""
        if role == "teacher":
            out = [{"id": f"teacher:{r['id']}", "kind": TEACHER, "title": r.get("name", r["id"])}
                   for r in self.course.roster()]
            out += [{"id": f"group:{g}", "kind": GROUP, "title": f"Group {g}"}
                    for g in sorted(self.course.groups())]
            return out
        out = [{"id": f"teacher:{who}", "kind": TEACHER, "title": "Instructor"}]
        g = self.course.group_of(who)
        if g:
            out.append({"id": f"group:{g}", "kind": GROUP, "title": f"Group {g}"})
            names = {r["id"]: r.get("name", r["id"]) for r in self.course.roster()}
            for p in sorted(self._peers(who)):
                out.append({"id": f"dm:{'|'.join(sorted((who, p)))}", "kind": DM,
                            "title": names.get(p, p), "peer": p})
        return out

    # -- report: the escape valve for the blind spot --------------------------- #
    def report(self, who: str, message_id: str, note: str = "") -> dict:
        """A student SHOWS the instructor a message. This is the only path by which a private DM ever
        reaches staff — consent-based disclosure, and it is logged as such."""
        msg = self.store.message(message_id)
        if msg is None:
            return {"ok": False, "error": "No such message."}
        visible = self._visible_channels(who, "student")
        if not visible(msg):
            return {"ok": False, "error": "That isn't your message to report."}
        self.store.add_report(_now(), who, note[:500], msg)
        return {"ok": True}

    def reports(self) -> list[dict]:
        return self.store.reports()

    # -- delete / restore (the Gmail-style trash) ------------------------------ #
    def set_deleted(self, who: str, role: str, message_id: str, deleted: bool) -> dict:
        """Soft-delete: a message goes to Trash, it isn't destroyed. Only someone who can SEE the
        message may bin it — you can't delete out of a thread you're not part of."""
        msg = self.store.message(message_id)
        if msg is None:
            return {"ok": False, "error": "No such message."}
        if not self._visible_channels(who, role)(msg):
            return {"ok": False, "error": "That isn't yours to delete."}
        self.store.set_deleted(message_id, deleted)
        return {"ok": True}

    # -- retention ------------------------------------------------------------- #
    def _purge_expired_dms(self) -> int:
        """Student↔student DMs age out. Course channels keep the term."""
        return self.store.purge_deleted_before(_now() - DM_TTL_DAYS * 86400)
