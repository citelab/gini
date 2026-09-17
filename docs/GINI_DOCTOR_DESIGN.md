# gini-doctor and the GINI Health Center — design decisions

Decided in a design session on 2026-09-17, on top of `docs/GINI_DOCTOR_STATUS.md` (what existed at
v6.14.0). `docs/GINI_DOCTOR_PLAN.md` is the build order; this is the why. Where they disagree, this
file is the intent and the plan is the schedule.

## Shape

1. **Python only, no Go.** One client, on PyPI.
2. **Two stages.** Stage 0 is shell with no Python dependency (POSIX `sh`; PowerShell 5.1 on
   Windows) and has one job: find a Python that can run Stage 1. Stage 1 is the doctor, standard
   library only, Python 3.8+. No new shell after Stage 0.
   *Why:* GINI needs Python, so "no usable Python" is a diagnosis, not a failure to run. Everything
   that must be correct, comparable and tested lives in Python, where tests can reach it.
3. **The three platforms are equals.** Every probe declares the platforms it applies to. A fact that
   cannot exist on a platform is `n/a`, never "missing", so a Mac-to-Linux comparison does not fill
   with differences that were never supposed to match.
4. **The doctor runs fully offline.** The Health Center adds to a local run; it is never required.
   Policy precedence is **live → cached → built-in**, and every report records which it ran under.
5. **Uploading is the person's act.** A report gathered offline is uploaded only when they choose
   to. Never automatic. A report is a self-contained file, so it can be uploaded from another
   machine — which is how a machine with no network takes part in a case.
6. **No fanout in v1.** One machine at a time. (The legacy engine's `--fanout` still exists until
   the shell engine is deleted.)

## What the doctor may do

7. **The doctor never writes.** Probes are read-only. Anything that goes beyond looking — `live`
   and `xv6`, which start containers — states what it will run and why, and runs only on Y at the
   prompt or `--yes`. With no terminal to ask on it is skipped, never assumed.
8. **Fixes are proposed, never applied.** A finding is: the exact command, the reason tied to
   evidence, the side effects, and which facts to re-check. The person runs it. The doctor then
   re-probes those facts.
9. **Nothing is downloaded to make a diagnosis.** The legacy real `pull` is gone: registry access is
   an anonymous HTTPS manifest check plus `<engine> manifest inspect`. `live` uses only images
   already present.
10. **No sandbox in v1, deliberately.** A sandbox depends on the very facilities the doctor is
    diagnosing (kernel features, user namespaces), and a doctor that cannot start is worse than one
    with fewer guarantees. So "read-only" is a property of how probes are written and reviewed, not
    something the OS enforces. Accepted, and recorded here rather than assumed. Sandboxing is
    deferred, not dropped.

## What leaves the machine

11. Hostname yes; usernames and home paths no (`<user>`, `~`), applied when a fact is recorded so the
    file on disk and the file uploaded are the same file. Credential files are never opened at all.
12. **JSON** for reports, policy and tasks (schema `gini-doctor/1`). Not YAML (needs a dependency)
    and not TOML (the standard library only reads it, and only from 3.11, which would raise the
    floor on exactly the older machines the doctor must reach). TOML is fine for policy authored on
    the Health Center, which serves JSON.
13. A fact's `status` is what makes machines comparable: `ok` carries a value, `absent` means the
    thing is not there, `error` keeps the machine's own words, `n/a` means it cannot exist here.

## The Health Center (S4–S6, not built yet)

14. **gini-healthcenter is a separate instance derived from the Teaching Center** (own data root,
    identity, secrets, port). Health data never enters the teaching database.
15. **The Health Center is trusted.** Treating it as hostile would remove most of its value,
    including growing the probe set from the server. Trust is directed at the *real* one: TLS with
    its identity pinned, case-scoped codes.
16. **The agent is a client, not part of the server.** The Teaching Center is deliberately model-free
    so a model failure cannot break a teacher at a deadline; that guarantee stays. If the model
    fails, a case stalls, loses nothing, and a person can continue from the portal.
17. **A case is the unit of work,** and its code is the consent: opening a case vends a code, and
    running the doctor with that code is what sends anything. Doctors connect outbound only and poll
    (lab machines and laptops are behind NAT); they never talk to each other directly.
18. **The agent diagnoses and recommends only.** It may post probes; it may never change a machine.
19. Policy is served two ways: `GET /doctor/policy.txt` (Stage 0, which has no JSON parser) and
    `GET /doctor/policy.json` (Stage 1).

## Deferred

Fanout; sandboxed execution of server-pushed probes; signed probe packs; a full privacy review
(retention, who may read a case corpus).
