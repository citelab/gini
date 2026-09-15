# Test plan — student code in the xv6 kernel, on rootless Podman

**For an agent running on the Linux box (and again on campus). Branch: `xv6-user-code`.**

Everything below has been run on **macOS / Docker 27.4**. The point of this pass is the part
macOS cannot answer: **rootless Podman, user-namespace file ownership, and SELinux**. A second
question worth watching is whether the container sees host file writes immediately — on macOS it
sometimes does not, and on a native Linux bind mount it should always.

Work through the parts in order. **Report results verbatim**, including anything that passes.
A green run is a result; so is a green run that took three attempts.

## Ground rules

- **Do not commit, push, or tag anything.** Report findings; the maintainer decides what lands.
- **Do not edit `backend/xv6/lab_feasibility.sh`.** If a check looks wrong, say so and quote it —
  changing it to go green destroys the measurement.
- **Do not rebuild `gini-xv6` from `backend/xv6/Dockerfile`.** It re-clones xv6 and takes ~10
  minutes. Pull the published image instead (Part 0).
- Quote exact output for anything that fails or warns. "It didn't work" is not a result.
- The script cleans up after itself. If it is interrupted, see Cleanup at the end.

---

## Part 0 — Setup

```bash
cd ~/Programs/gini            # wherever the checkout lives
git fetch --all --tags
git checkout xv6-user-code
git log --oneline -3          # expect a0b2127 "Two writers, one inode..." on top
```

Record the environment:

```bash
podman --version
podman info --format '{{.Host.Security.Rootless}}'      # expect: true
id -u; id -un
getenforce 2>/dev/null || echo "no SELinux"
uname -r
df -T "${TMPDIR:-/tmp}" | tail -1                        # the fs the test folder lands on
```

Get the base image. It must be tagged **`gini-xv6:latest`** locally, because that is the name
gBuilder uses:

```bash
podman images | grep xv6
# if gini-xv6:latest is absent:
podman pull ghcr.io/gini-toolkit/gini-xv6:6.13.1
podman tag  ghcr.io/gini-toolkit/gini-xv6:6.13.1 gini-xv6:latest
```

### Build the branch image — this step is not optional

The **published image carries the OLD in-container agent**. Three of the fixes under test live in
`backend/xv6/gini_agent.py`, so testing only `gini-xv6:latest` tests the bug, not the fix. Layer
this branch's agent over the published image (a ~2 second build, no toolchain work):

```bash
cd backend/xv6
printf 'FROM gini-xv6:latest\nCOPY gini_agent.py /opt/gini_agent.py\n' > /tmp/Dockerfile.branch
podman build -f /tmp/Dockerfile.branch -t gini-xv6:branch .
cd ../..
podman images | grep -E "gini-xv6.*(latest|branch)"
```

---

## Part 1 — Unit tests (fast, no container)

```bash
python3 -m venv .venv && . .venv/bin/activate
./scripts/dev.sh install
QT_QPA_PLATFORM=offscreen ./scripts/dev.sh test tests/test_xv6_rebuild_safety.py
QT_QPA_PLATFORM=offscreen ./scripts/dev.sh test tests/test_xv6_build_errors.py
```

**Expect 11 passed and 9 passed.** These are pure Python and engine-independent — if they fail
here they would fail anywhere, and that is a real finding, not an environment quirk.

Then the whole suite, because adding a file to this repo has broken it before:

```bash
QT_QPA_PLATFORM=offscreen ./scripts/dev.sh test
```

The reference figure is **3572 passed, 20 skipped, 0 failed**, but that was measured as

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=core/src:frontend-ng/src \
    python3 -m pytest frontend-ng/tests backend/tests -q
```

and `dev.sh test` defaults to `tests/ ../bot/tests/ ../reason/tests/` — a different set, so the
totals will not match. **Report the actual numbers and the command you ran.** What matters is
`0 failed`, not the total; per `CLAUDE.md`, counts in this repo go stale within days.

---

## Part 2 — Baseline: the published image (shows the bug)

Run it **three times**. Repetition is the point: the failure is intermittent.

```bash
for i in 1 2 3; do
  echo "=== run $i ==="
  IMAGE=gini-xv6:latest sh backend/xv6/lab_feasibility.sh 2>&1 | tail -30
done
```

On macOS/Docker, four runs gave: `15 passed/1 failed/1 warned`, `16/0/1`, `17/0/0`, `16/0/1`.
The failure, when it appears, is:

```
FAIL  Load reported SUCCESS for code that does not compile (stale build reached the student)
```

That is the bug in its natural habitat: `make` did not notice a host edit, built nothing, and the
agent answered `{"ok": true, "log": "loaded"}` for code containing a syntax error.

**What to record:** the verdict line from each of the three runs, and the full section 6 block.

**If all three runs are clean (`17 passed, 0 failed, 0 warned`) — that is the expected Linux
result and an important finding.** It means host and container share one page cache, as a native
bind mount should, and the hazard is a macOS artefact. Say so explicitly.

---

## Part 3 — The branch image (shows the fix)

Same three runs, against the agent under test:

```bash
for i in 1 2 3; do
  echo "=== run $i ==="
  IMAGE=gini-xv6:branch sh backend/xv6/lab_feasibility.sh 2>&1 | tail -30
done
```

**Required outcome: `0 failed` in all three runs.** A `WARN` in section 6 is acceptable and is
not a failure — it reports that this engine's mount caches attributes, which the agent works
around; the check immediately after it (`Load reported the error, scoped to what the student
needs`) is the one that must pass.

**If Part 3 ever reports `0 failed` while Part 2 reported a failure, the fix is doing its job on
this engine.** That comparison is the single most valuable output of this whole plan.

### What each check means

| Section | Check | Meaning if it fails |
|---|---|---|
| 1 | 6 lab files copied out of the image | the image layout changed, or the mount is not writable |
| 1 | host can write files the container created | **rootless uid mapping or SELinux** — the headline risk |
| 2 | linked 6 tree files | symlink creation blocked in the container |
| 2 | re-running the link step is safe | idempotence broken; a second Run would destroy the pristine copy |
| 2 | tree reads the host's file through the symlink | the mount is not visible where expected |
| 3 | host edits written / test app linked | the host-side edit did not land |
| 4 | incremental build succeeded | the toolchain cannot build a student's kernel in-container |
| 4 | make saw the host edit and recompiled | **stale build** — make did not notice the change |
| 5 | /rebuild accepted | the agent's Load endpoint is broken |
| 5 | syscall ran and returned correctly | end-to-end failure; usually a stale kernel |
| 5 | Syscall Lab counter sees it | `gini_sccount` is not counting the new number |
| 6 | make says 'up to date' (WARN) | attribute-cache lag on this engine — mitigated, not fatal |
| 6 | Load reported the error | **the agent told a student their broken code was fine** |
| 7 | work still on the host / fresh container pristine / relink recovered | persistence across Stop/Run |

---

## Part 4 — Podman-specific probes

These are the questions macOS cannot answer. Run them by hand and report the raw output.

### 4a. Ownership, both directions

```bash
rm -rf /tmp/giniuid && mkdir -p /tmp/giniuid
podman run --rm -v /tmp/giniuid:/x:z --entrypoint sh gini-xv6:latest \
  -c 'touch /x/from-container; echo "in-container uid=$(id -u)"'
ls -ln /tmp/giniuid/
echo "host append" >> /tmp/giniuid/from-container && echo "HOST CAN WRITE: ok"

touch /tmp/giniuid/from-host
podman run --rm -v /tmp/giniuid:/x:z --entrypoint sh gini-xv6:latest \
  -c 'echo hi >> /x/from-host && echo "CONTAINER CAN WRITE: ok"'
```

**Expect** `from-container` owned by *your* uid on the host (rootless maps container root to you),
and both writes to succeed. If the host file shows a high subuid (e.g. `100000`), say so — that
would mean a student cannot edit their own kernel file in their editor, which is fatal to the
design and needs `--userns=keep-id`.

### 4b. Is `:z` load-bearing?

```bash
podman run --rm -v /tmp/giniuid:/x --entrypoint sh gini-xv6:latest \
  -c 'cat /x/from-host' && echo "WITHOUT :z — worked" || echo "WITHOUT :z — BLOCKED"
```

Either answer is fine; we need to know which. If it is blocked, `services/compiler.py` must add
`:z` to the xv6 volume for Podman — today it writes a plain `host:container` mount, so **the
existing shadow lab would be broken on this box too**. Check that specifically and say so.

### 4c. Symlinks inside the mount, under rootless

```bash
podman run --rm -v /tmp/giniuid:/x:z --entrypoint sh gini-xv6:latest \
  -c 'ln -sf /x/from-host /tmp/link && cat /tmp/link >/dev/null && echo "SYMLINK OK"'
```

---

## Part 5 — The two fixes, live

The unit tests cover the logic; this checks the real thing on this engine. Uses `gini-xv6:branch`.

Every file edit below goes in through a **heredoc**. That is deliberate: the same edits written
as `sh -c "... python3 -c '...'"` do not survive three levels of quoting, and they fail by
silently writing a mangled file rather than by erroring. Each block prints something you can
check.

```bash
podman rm -f xv6chk 2>/dev/null
podman run -d --name xv6chk gini-xv6:branch && sleep 10

api() { podman exec xv6chk python3 -c "
import urllib.request as u,sys
d=(sys.argv[2].encode() if len(sys.argv)>2 else None)
print(u.urlopen('http://127.0.0.1:5000'+sys.argv[1],data=d,timeout=200).read().decode()[-400:])" "$@"; }

api /procs | head -2            # sanity: the machine is up before anything else
```

### 5a. A kernel-only Load must not touch the disk

```bash
api /input '
' >/dev/null; sleep 1                                  # a bare Enter, to get a prompt
api /input 'echo hello-from-student > mywork.txt
' >/dev/null; sleep 3

echo "inode BEFORE: $(podman exec xv6chk stat -c %i /opt/xv6-riscv/fs.img)"
podman exec xv6chk touch /opt/xv6-riscv/kernel/shadows/gini_sched.c
api /rebuild ''                                        # expect {"ok": true, "log": "loaded"}
echo "inode AFTER:  $(podman exec xv6chk stat -c %i /opt/xv6-riscv/fs.img)"

sleep 9; api /input '
' >/dev/null; sleep 1
api /input 'cat mywork.txt
' >/dev/null; sleep 4; api /console | tail -4
```

**Expect:** the inode is **unchanged**, and `hello-from-student` still prints. A changed inode
means a kernel-only Load regenerated the disk, which would have deleted the student's files.

### 5b. A new user program forces a staged rebuild

```bash
podman exec -i xv6chk sh -c 'cat > /opt/xv6-riscv/user/hi.c' <<'EOF'
#include "kernel/types.h"
#include "user/user.h"
int main(void){ printf("hi from a new program\n"); exit(0); }
EOF

podman exec -i xv6chk python3 - <<'EOF'
import re, pathlib
p = pathlib.Path("/opt/xv6-riscv/Makefile"); s = p.read_text()
if "_hi\\" not in s:
    p.write_text(re.sub(r"(UPROGS=\\\n)", "\\1\t$U/_hi\\\\\n", s, count=1))
print("registered:", "_hi\\" in p.read_text())         # must print True
EOF

echo "inode BEFORE: $(podman exec xv6chk stat -c %i /opt/xv6-riscv/fs.img)"
api /rebuild ''
echo "inode AFTER:  $(podman exec xv6chk stat -c %i /opt/xv6-riscv/fs.img)"
podman exec xv6chk sh -c 'cd /opt/xv6-riscv && ls fs*.img'    # expect fs.img ONLY
```

**Expect:** `registered: True`, the inode **changes** (a new image was built and swapped in),
and **no `fs-new.img` or `fs-live.img` is left behind**.

### 5c. A build that fails after the live image is parked must restore it

This is the path a broken **user program** takes — the failure happens inside the disk stage,
after the live image has been renamed aside. It is the one that could lose a student's disk.

```bash
podman exec -i xv6chk sh -c 'cat > /opt/xv6-riscv/user/hi.c' <<'EOF'
#include "kernel/types.h"
int main(void){ return notdeclared; }
EOF

echo "inode BEFORE: $(podman exec xv6chk stat -c %i /opt/xv6-riscv/fs.img)"
api /rebuild '' | head -c 300                          # expect the error, NOT {"ok": true}
echo "inode AFTER:  $(podman exec xv6chk stat -c %i /opt/xv6-riscv/fs.img)"
podman exec xv6chk sh -c 'cd /opt/xv6-riscv && ls fs*.img'
api /procs | head -2                                   # the machine must still be alive
```

**Expect:** a legible `notdeclared` error rather than `"ok": true`, inode **unchanged**, no
leftovers, and `/procs` still answering. A failed build must never take the student's machine
down.

### 5d. Crash recovery

```bash
# the agent died while mkfs was running: the live image is parked, fs.img is missing
podman exec xv6chk sh -c 'cd /opt/xv6-riscv && mv fs.img fs-live.img && ls fs*.img'
podman restart xv6chk >/dev/null && sleep 10
podman exec xv6chk sh -c 'cd /opt/xv6-riscv && ls fs*.img'    # expect fs.img, nothing else
api /procs | head -2                                          # expect the machine booted

# the agent died after a successful build but before the restart
podman exec xv6chk sh -c 'cd /opt/xv6-riscv && cp fs.img fs-new.img && stat -c "staged=%i" fs-new.img'
podman restart xv6chk >/dev/null && sleep 10
podman exec xv6chk sh -c 'cd /opt/xv6-riscv && stat -c "fs.img=%i" fs.img && ls fs*.img'

podman rm -f xv6chk
```

**Expect:** the parked image is restored and the machine boots; then the staged image is applied
— `fs.img` ends up with the inode `fs-new.img` had — and nothing is left over.

## Part 6 — Report

Fill this in and return it:

```
MACHINE:            home linux / campus
podman version:     
rootless:           
SELinux:            enforcing / permissive / absent
kernel / distro:    
TMPDIR filesystem:  

PART 1  unit tests:        rebuild_safety __/11   build_errors __/9   full suite ____ passed, ____ failed
PART 2  published image:   run1 __/__/__   run2 __/__/__   run3 __/__/__     (passed/failed/warned)
        stale build seen?  yes / no        (quote section 6 if yes)
PART 3  branch image:      run1 __/__/__   run2 __/__/__   run3 __/__/__
        any FAIL?          yes / no        (quote it)
PART 4a ownership:         host uid on container-created file = ______   both writes ok? 
PART 4b without :z:        worked / BLOCKED
PART 4c symlink:           ok / failed
PART 5  kernel-only Load:  inode unchanged? ___   student's file survived? ___
        new user program:  inode changed? ___     leftovers? ___
        failed build:      ok:false? ___  inode unchanged? ___  machine alive? ___
        crash recovery:    parked restored? ___   staged applied? ___

ANYTHING SURPRISING:
```

---

## Troubleshooting

**`Error: short-name resolution enforced`** — use the fully qualified name
(`ghcr.io/gini-toolkit/gini-xv6:6.13.1`) or add `docker.io`/`ghcr.io` to
`unqualified-search-registries` in `/etc/containers/registries.conf`. See `docs/PODMAN_SETUP.md`.

**`invalid username/password` on a pull that needs no auth** — a stale
`$XDG_RUNTIME_DIR/containers/auth.json`. Work around it per-command with
`--authfile <(echo '{}')`, and report it; this bit the campus machine before.

**The script hangs at section 5** — the agent did not come up. Check
`podman logs <name>` and whether QEMU can run (`qemu-system-riscv64` is inside the image, but
nested virtualisation is not required — it is pure emulation, so this should always work).

**`permission denied` touching the mounted folder** — SELinux. Part 4b is the diagnostic; report
it, do not `setenforce 0`.

**A check fails once and passes on retry** — that is a finding, not noise. Record how many
attempts it took; intermittency is exactly what Part 2 is measuring.

## Cleanup

```bash
podman rm -f xv6chk 2>/dev/null
podman ps -a | grep -E 'ginilabcheck|xv6chk'          # expect nothing
rm -rf /tmp/giniuid /tmp/gini-labcheck-* /tmp/Dockerfile.branch
podman rmi gini-xv6:branch                            # optional
```

## What this is testing, and where it is written up

`docs/design/xv6-student-code.md` — the feasibility analysis, the mechanism (one mounted folder
plus symlinks into the tree), the two silent failures and what they do instead, and the inventory
of what the Machine Lab still lacks.

`backend/xv6/lab_feasibility.sh` — the script Parts 2 and 3 run.
`frontend-ng/tests/test_xv6_rebuild_safety.py`, `test_xv6_build_errors.py` — Part 1.
