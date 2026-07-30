"""ProfAI (and hosted StudentAI) — the server-side personas.

We chose to run personas on the Center rather than on student machines (design D1). The cost is that
the Center now needs a model host. The thing it buys is the reason: **one persona to audit, and one
place to fix a bad answer.** With student-side personas a bad habit lives in sixty local copies.

Three guardrails ship in v1. Behaviour *shaping* is deliberately deferred until we've lived with the
thing — but these are safety, not quality, and they are not deferrable:

  1. **Refuse and escalate** on deadlines, exam content, grades, and policy. A wrong guess about a
     deadline is a real cost to a real student, and it lands on the student, not on us. The refusal
     is a DETERMINISTIC pre-filter, not an instruction in the prompt — you don't ask a model to
     please not do the dangerous thing, you make it unable to.
  2. **Never assert a grade or a fact about a specific student.** Same mechanism.
  3. **Everything is labelled, versioned, logged, and queued for the teacher's review.**

The correction loop is what makes this improve without prompt engineering: the teacher reviews a
ProfAI answer, corrects it; the correction posts to the thread **as Prof**, and can be promoted into
the persona as a standing answer. Experience → persona, mechanically.

Capacity: sixty students in a lab all asking at once will serialize. A bounded queue with a real
concurrency limit and an honest "you're 4th in line" beats a spinner that lies.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

OLLAMA_URL = "http://localhost:11434"
MODEL = "llama3.1"
MAX_CONCURRENT = 2                 # generations at once — a laptop is not a datacentre
RATE_LIMIT_S = 8                   # per student, between AI requests

# --- Guardrail 1 & 2: the deterministic refusal pre-filter ------------------- #
# These never reach the model. Asking a model to decline reliably is a hope; a regex is a mechanism.
_REFUSE = [
    # `due` on its own: contractions and word order made anything cleverer leak ("when's the lab
    # due?" slipped past a pattern that wanted "when is"). The word barely occurs in networking talk,
    # so the bluntness is nearly free — and a missed deadline question is the single most expensive
    # thing this AI could get wrong.
    (re.compile(r"\bdue\b|\b(deadline|extension|resubmit)\b|\blate (penalty|submission)\b", re.I),
     "deadlines and extensions"),
    # Word ORDER must not matter ("what's on the exam" vs "what will be on the final exam"), so this
    # fires on the noun alone. 'test' and 'final' are excluded as bare words — students say "run a
    # test" and "the final router" constantly, and a refusal storm would teach them to ignore ProfAI
    # entirely, which costs more than it saves. Failing safe means erring toward refusing, but not so
    # bluntly that the refusal becomes noise.
    (re.compile(r"\b(exam|midterm|quiz)\b|\bfinal\s+(exam|test)\b", re.I),
     "exams and what they cover"),
    (re.compile(r"\b(what('| i)?s|tell me|show me).{0,20}\b(my|his|her|their)\b.{0,20}\b(grade|mark|"
                r"score|band|result)\b", re.I), "grades"),
    (re.compile(r"\b(regrade|re-grade|appeal|dispute|unfair|remark my)\b", re.I),
     "grading disputes"),
    (re.compile(r"\b(attendance|absent|excused|medical note|academic (integrity|misconduct)|"
                r"plagiaris)\w*\b", re.I), "course policy"),
]


def refusal_topic(text: str) -> str:
    for rx, topic in _REFUSE:
        if rx.search(text or ""):
            return topic
    return ""


def refusal_message(topic: str, teacher_name: str = "your instructor") -> str:
    return (f"I can't answer questions about {topic} — those are {teacher_name}'s to answer, and a "
            f"confident guess from me could cost you. I've flagged this so they see it.")


# --- the persona ------------------------------------------------------------- #
DEFAULT_PERSONA = {
    "version": 1,
    "name": "Prof",
    "voice": ("You are the teaching assistant persona of the course instructor. You explain "
              "networking and cloud concepts the way a good lecturer does: concretely, with the "
              "smallest example that makes the idea click. You are warm but brief."),
    "context": "",                    # syllabus / notes the teacher pastes in
    "standing_answers": [],           # [{q, a}] — promoted from corrections
    "auto_answer": True,              # may ProfAI answer when the teacher is away?
    "answer_when_present": False,     # never pre-empt a present human, unless the teacher says so
}


class Persona:
    def __init__(self, root: str | Path, name: str = "persona") -> None:
        from store import Store
        self.store = Store(root)
        self.key = name.replace(".json", "")

    def get(self) -> dict:
        v = self.store.kv_get(self.key)
        return {**DEFAULT_PERSONA, **v} if v else dict(DEFAULT_PERSONA)

    def save(self, patch: dict) -> dict:
        p = self.get()
        for k in ("name", "voice", "context", "auto_answer", "answer_when_present",
                  "standing_answers"):
            if k in patch:
                p[k] = patch[k]
        p["version"] = int(p.get("version", 1)) + 1     # every edit is a new version, for the log
        self.store.kv_put(self.key, p)
        return p

    def add_standing_answer(self, q: str, a: str) -> dict:
        p = self.get()
        p["standing_answers"] = [x for x in p.get("standing_answers", []) if x.get("q") != q]
        p["standing_answers"].append({"q": q, "a": a})
        return self.save({"standing_answers": p["standing_answers"]})


# --- the model --------------------------------------------------------------- #
class Ollama:
    def __init__(self, url: str = OLLAMA_URL, model: str = MODEL, timeout: float = 90.0) -> None:
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(self.url + "/api/tags", timeout=3):
                return True
        except Exception:                                # noqa: BLE001
            return False

    def chat(self, system: str, user: str) -> str:
        body = json.dumps({"model": self.model, "stream": False,
                           "messages": [{"role": "system", "content": system},
                                        {"role": "user", "content": user}]}).encode()
        req = urllib.request.Request(self.url + "/api/chat", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            obj = json.loads(r.read().decode())
        return (obj.get("message") or {}).get("content", "").strip()


# --- capacity ---------------------------------------------------------------- #
class Capacity:
    """A bounded queue. Refuses honestly rather than queueing forever, and can say where you are in
    line — a spinner that lies is worse than a no."""

    def __init__(self, max_concurrent: int = MAX_CONCURRENT, rate_s: float = RATE_LIMIT_S) -> None:
        self._sem = threading.BoundedSemaphore(max_concurrent)
        self._lock = threading.Lock()
        self._waiting = 0
        self._last: dict[str, float] = {}
        self.rate_s = rate_s

    def rate_limited(self, who: str) -> float:
        with self._lock:
            wait = self.rate_s - (time.time() - self._last.get(who, 0))
            return max(0.0, wait)

    def note(self, who: str) -> None:
        with self._lock:
            self._last[who] = time.time()

    def queue_position(self) -> int:
        with self._lock:
            return self._waiting

    def __enter__(self):
        with self._lock:
            self._waiting += 1
        self._sem.acquire()
        with self._lock:
            self._waiting -= 1
        return self

    def __exit__(self, *a):
        self._sem.release()
        return False


# --- ProfAI ------------------------------------------------------------------ #
class ProfAI:
    def __init__(self, root: str | Path, course, social, *, llm=None,
                 capacity: Capacity | None = None) -> None:
        self.root = Path(root)
        self.course = course
        self.social = social
        self.persona = Persona(root)
        self.llm = llm if llm is not None else Ollama()
        self.cap = capacity or Capacity()

    # -- the reply ladder ----------------------------------------------------- #
    def should_answer(self, teacher_id: str) -> bool:
        """A present human is NEVER pre-empted by their own proxy. If students can't tell which one
        they're talking to, they stop trusting both."""
        p = self.persona.get()
        if not p.get("auto_answer", True):
            return False
        if self.social.online(teacher_id) and not p.get("answer_when_present", False):
            return False
        return True

    def answer(self, student: str, question: str) -> dict:
        """Produce a ProfAI reply (or a refusal). Always returns something postable — silence would
        leave the student staring at a spinner."""
        p = self.persona.get()

        topic = refusal_topic(question)
        if topic:
            # never reaches the model
            return {"kind": "refusal", "topic": topic,
                    "body": refusal_message(topic, p.get("name", "your instructor")),
                    "persona_version": str(p.get("version", 1)), "escalate": True}

        for sa in p.get("standing_answers", []):
            if sa.get("q") and sa["q"].lower() in (question or "").lower():
                return {"kind": "standing", "body": sa["a"],
                        "persona_version": str(p.get("version", 1)), "escalate": False}

        wait = self.cap.rate_limited(student)
        if wait > 0:
            return {"kind": "busy",
                    "body": f"Give me a few seconds — I'm still working on your last question.",
                    "persona_version": str(p.get("version", 1)), "escalate": False}

        with self.cap:
            self.cap.note(student)
            system = self._system(p)
            try:
                text = self.llm.chat(system, question)
            except Exception:                            # noqa: BLE001
                return {"kind": "unavailable",
                        "body": ("I can't reach my model right now, so I'd rather say nothing than "
                                 "guess. Your message is saved — your instructor will see it."),
                        "persona_version": str(p.get("version", 1)), "escalate": True}
        if not text:
            return {"kind": "unavailable",
                    "body": "I don't have a good answer to that. I've flagged it for your instructor.",
                    "persona_version": str(p.get("version", 1)), "escalate": True}
        return {"kind": "ai", "body": text, "persona_version": str(p.get("version", 1)),
                "escalate": False}

    def _system(self, p: dict) -> str:
        bits = [p.get("voice", ""),
                "\nYou are answering ON BEHALF OF the instructor, who is away. You are clearly "
                "labelled as an AI in the interface, so do not pretend to be them.",
                "\nHARD RULES:",
                "- Never state or imply a student's grade, band, or result.",
                "- Never state a deadline, exam content, or course policy. Say the instructor must "
                "answer that, and that you've flagged it.",
                "- If you are not confident, say so plainly. A confident wrong answer costs the "
                "student, not you."]
        if p.get("context"):
            bits.append("\nCOURSE CONTEXT (authoritative):\n" + p["context"])
        if p.get("standing_answers"):
            bits.append("\nThe instructor's own answers to common questions — prefer these:\n"
                        + "\n".join(f"- Q: {x['q']}\n  A: {x['a']}"
                                    for x in p["standing_answers"][:20]))
        return "\n".join(bits)

    # -- review queue (guardrail 3) -------------------------------------------- #
    def log_answer(self, student: str, question: str, reply: dict, message_id: str) -> None:
        self.persona.store.add_review({
            "message_id": message_id, "ts": time.time(), "student": student, "question": question,
            "answer": reply["body"], "kind": reply["kind"],
            "persona_version": reply.get("persona_version", ""),
            "escalate": bool(reply.get("escalate")), "reviewed": False})

    def review_queue(self, only_unreviewed: bool = True) -> list[dict]:
        return self.persona.store.review(only_unreviewed)

    def mark_reviewed(self, message_id: str) -> None:
        self.persona.store.mark_reviewed(message_id)

    # -- the digest: what a professor with no time actually needs --------------- #
    def digest(self) -> dict:
        """Not a transcript — a situation report. 'Group 4 has been stuck on subnetting for 40
        minutes and nobody has asked for help.' This is the mission reteach signal, extended from
        objectives into conversation.

        Works WITHOUT the model: the facts are computed deterministically, and the LLM only phrases
        them. If the model is down you still get the facts, which is the part that matters."""
        facts = self._facts()
        prose = ""
        if facts["stuck"] or facts["quiet"] or facts["unanswered"]:
            try:
                with self.cap:
                    prose = self.llm.chat(
                        "You write a 3-sentence situation report for a busy professor. Be concrete "
                        "and specific. Do not invent anything not in the facts. No preamble.",
                        json.dumps(facts, indent=2))
            except Exception:                            # noqa: BLE001
                prose = ""
        return {"facts": facts, "summary": prose}

    def _facts(self) -> dict:
        now = time.time()
        stuck, quiet = [], []
        for g, rows in self.course.groups().items():
            levels, online = [], 0
            for r in rows:
                pr = self.social.presence_of(r["id"])
                if pr["online"]:
                    online += 1
                if pr["progress"]:
                    levels.append(pr["progress"])
            if not levels:
                continue
            worst = min(l.get("level", 0) for l in levels)
            title = next((l.get("title") for l in levels if l.get("title")), "")
            # "stuck" = everyone in the group is still on the same low rung and nobody has asked
            asked = any(m["chan_kind"] == "teacher" for m in
                        self.social.inbox("", "teacher", since=now - 3600))
            if worst <= 2 and online:
                stuck.append({"group": g, "level": worst, "mission": title,
                              "online": online, "asked_for_help": asked})
            if online == 0:
                quiet.append({"group": g})

        # questions to the teacher that nobody (human or AI) has answered
        msgs = self.social.inbox("", "teacher", since=now - 7 * 86400)
        threads: dict[str, list] = {}
        for m in msgs:
            threads.setdefault(m["channel"], []).append(m)
        unanswered = []
        for chan, ms in threads.items():
            ms.sort(key=lambda m: m["ts"])
            if ms and ms[-1]["kind"] == "human" and ms[-1]["author"] != "teacher":
                unanswered.append({"student": ms[-1]["author"],
                                   "question": ms[-1]["body"][:160],
                                   "waiting_minutes": int((now - ms[-1]["ts"]) / 60)})
        unanswered.sort(key=lambda u: -u["waiting_minutes"])
        return {"stuck": stuck, "quiet": quiet, "unanswered": unanswered[:10]}


# --- Phase E: hosted StudentAI ------------------------------------------------ #
class StudentAI:
    """A student's own proxy, hosted on the Center — but ONLY if the teacher granted it (capacity is
    theirs to give) AND the student turned it on. Not granted → nothing breaks: their AI simply runs
    locally, as it always has. The fallback is graceful by construction, so this phase cannot hurt
    anyone."""

    def __init__(self, root: str | Path, course, social, *, llm=None,
                 capacity: Capacity | None = None) -> None:
        self.root = Path(root)
        self.course = course
        self.social = social
        self.llm = llm if llm is not None else Ollama()
        self.cap = capacity or Capacity()
        from store import Store
        self.store = Store(root)

    def _prefs(self) -> dict:
        return self.store.kv_get("student_ai") or {}

    def set_pref(self, who: str, on: bool, blurb: str = "") -> dict:
        prefs = self._prefs()
        rec = prefs.get(who, {})
        rec["on"] = bool(on)
        if blurb:
            rec["blurb"] = blurb[:500]
        prefs[who] = rec
        self.store.kv_put("student_ai", prefs)
        return {"ok": True, **rec}

    def granted(self, who: str) -> bool:
        row = next((r for r in self.course.roster() if r["id"] == who), {})
        return bool(row.get("ai_hosted"))

    def enabled(self, who: str) -> bool:
        """Both must be true: the teacher granted the capacity, and the student consented to speak
        through a proxy. Either one alone is not consent."""
        return self.granted(who) and bool(self._prefs().get(who, {}).get("on"))

    def should_answer(self, who: str) -> bool:
        return self.enabled(who) and not self.social.online(who)

    def answer(self, owner: str, asker: str, question: str) -> dict:
        topic = refusal_topic(question)
        if topic:
            return {"kind": "refusal",
                    "body": f"That's not something I can answer for {owner} — ask the instructor."}
        blurb = self._prefs().get(owner, {}).get("blurb", "")
        system = (f"You are an AI standing in for the student '{owner}', who is away. You are "
                  f"labelled as an AI, so never pretend to be them. Answer only about coursework and "
                  f"what {owner} has been working on. If you don't know, say so — do not speak for "
                  f"them about opinions, plans, or anything personal."
                  + (f"\n\n{owner} describes themselves: {blurb}" if blurb else ""))
        try:
            with self.cap:
                text = self.llm.chat(system, question)
        except Exception:                                # noqa: BLE001
            return {"kind": "unavailable",
                    "body": f"{owner} is away and I can't reach my model. They'll see your message."}
        return {"kind": "ai", "body": text or f"{owner} is away — they'll see your message."}
