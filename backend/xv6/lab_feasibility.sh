#!/bin/sh
# Can this machine run an xv6 lab where the STUDENT's code is compiled inside the shipped image?
#
# The syscall lab (and any later "modify the kernel" lab) needs six files out of the xv6 tree to
# come from the host instead of the image, so the student's work survives Stop/Run and so a new
# assignment never means a new container image. The mechanism is: mount ONE host directory, then
# symlink the tree's files at it. A symlink resolves by PATH, so it survives an editor saving via
# rename — which is the same reason kernel/shadows/ is mounted as a directory and not as a file.
#
# This script proves that mechanism works on THIS machine and engine, end to end, and leaves
# nothing behind. It is deliberately POSIX sh + awk: it runs the same on macOS/Docker,
# Linux/Docker and rootless Podman, which is exactly the comparison it exists to make.
#
#   sh backend/xv6/lab_feasibility.sh                 # auto-detect the engine
#   ENGINE=podman sh backend/xv6/lab_feasibility.sh   # force one
#
# Exit status is the number of checks that FAILED, so it is usable from CI.
set -u

ENGINE="${ENGINE:-}"
IMAGE="${IMAGE:-gini-xv6:latest}"
NAME="ginilabcheck$$"
WORK="${TMPDIR:-/tmp}/gini-labcheck-$$"
PASS=0; FAIL=0; SKIP=0; WARN=0

say()  { printf '%s\n' "$*"; }
ok()   { PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m  %s\n' "$*"; }
bad()  { FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m  %s\n' "$*"; }
skip() { SKIP=$((SKIP+1)); printf '  \033[33mSKIP\033[0m  %s\n' "$*"; }
# A property of this engine that GINI already works around. Loud, but not a reason to stop:
# the verdict is "can this machine run the lab", and a mitigated hazard does not change it.
warn() { WARN=$((WARN+1)); printf '  \033[33mWARN\033[0m  %s\n' "$*"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

cleanup() {
    [ -n "${NAME:-}" ] && $ENGINE rm -f "$NAME" >/dev/null 2>&1
    [ -n "${WORK:-}" ] && rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

# -- 0. engine ------------------------------------------------------------- #
head_ "0. Container engine"
if [ -z "$ENGINE" ]; then
    for e in docker podman; do
        if command -v "$e" >/dev/null 2>&1 && "$e" info >/dev/null 2>&1; then ENGINE="$e"; break; fi
    done
fi
if [ -z "$ENGINE" ]; then say "  no working docker or podman found"; exit 1; fi
say "  engine: $ENGINE ($($ENGINE --version 2>/dev/null | head -1))"
if [ "$ENGINE" = "podman" ]; then
    say "  rootless: $($ENGINE info --format '{{.Host.Security.Rootless}}' 2>/dev/null)"
fi

if ! $ENGINE image exists "$IMAGE" >/dev/null 2>&1 && \
   ! $ENGINE image inspect "$IMAGE" >/dev/null 2>&1; then
    say "  image $IMAGE not present locally — pulling"
    $ENGINE pull "$IMAGE" >/dev/null 2>&1 || { say "  cannot get $IMAGE"; exit 1; }
fi
say "  image:  $IMAGE"

# -- 1. seed the host lab folder from the image ---------------------------- #
head_ "1. Seed a host lab folder from the image's pristine tree"
mkdir -p "$WORK/M1" || exit 1
$ENGINE run --rm --entrypoint sh -v "$WORK/M1:/out" "$IMAGE" -c \
  'cd /opt/xv6-riscv && for f in kernel/syscall.h kernel/syscall.c kernel/sysproc.c \
     user/user.h user/usys.pl Makefile; do cp "$f" "/out/$(basename $f)"; done' >/dev/null 2>&1
n=$(ls "$WORK/M1" 2>/dev/null | wc -l | tr -d ' ')
[ "$n" = "6" ] && ok "6 lab files copied out of the image" \
                || bad "expected 6 files in the lab folder, got $n"

# THE rootless/SELinux question: the container wrote these — can the HOST still edit them?
if [ -w "$WORK/M1/syscall.h" ] && printf '\n' >> "$WORK/M1/syscall.h" 2>/dev/null; then
    ok "host can write files the container created (no uid/SELinux block)"
else
    bad "host CANNOT write container-created files — check rootless uid mapping / SELinux (:z)"
    say "       owner: $(ls -l "$WORK/M1/syscall.h" 2>/dev/null)"
fi

# -- 2. start a container with the lab folder mounted ---------------------- #
head_ "2. Mount the lab folder and link the tree at it"
MOUNT="$WORK/M1:/opt/xv6-lab"
[ "$ENGINE" = "podman" ] && MOUNT="$MOUNT:z"     # SELinux relabel; harmless where unused
$ENGINE run -d --name "$NAME" -v "$MOUNT" "$IMAGE" >/dev/null 2>&1 \
    || { bad "could not start the container"; exit $FAIL; }
i=0; while [ $i -lt 30 ]; do
    $ENGINE exec "$NAME" sh -c 'test -f /opt/xv6-riscv/kernel/kernel' >/dev/null 2>&1 && break
    i=$((i+1)); sleep 1
done

$ENGINE exec "$NAME" sh -c '
cd /opt/xv6-riscv || exit 1
mkdir -p /opt/gini_orig
for pair in "kernel/syscall.h:syscall.h" "kernel/syscall.c:syscall.c" \
            "kernel/sysproc.c:sysproc.c" "user/user.h:user.h" \
            "user/usys.pl:usys.pl" "Makefile:Makefile"; do
    tgt=${pair%%:*}; base=${pair##*:}
    [ -f "/opt/xv6-lab/$base" ] || { echo "MISSING $base"; exit 1; }
    if [ ! -L "$tgt" ]; then cp "$tgt" "/opt/gini_orig/$base"; ln -sf "/opt/xv6-lab/$base" "$tgt"; fi
done' >/dev/null 2>&1 \
  && ok "linked 6 tree files at the mounted folder" || bad "linking failed"

# Idempotence: the link step runs on EVERY Run, and a second pass must not save a symlink as
# the "pristine" copy — that would destroy the only good original.
$ENGINE exec "$NAME" sh -c '
cd /opt/xv6-riscv
for pair in "kernel/syscall.h:syscall.h"; do
    tgt=${pair%%:*}; base=${pair##*:}
    if [ ! -L "$tgt" ]; then cp "$tgt" "/opt/gini_orig/$base"; ln -sf "/opt/xv6-lab/$base" "$tgt"; fi
done
test -f /opt/gini_orig/syscall.h && ! test -L /opt/gini_orig/syscall.h' >/dev/null 2>&1 \
  && ok "re-running the link step is safe (pristine copy still a real file)" \
  || bad "re-linking clobbered the pristine copy"

$ENGINE exec "$NAME" sh -c 'grep -q "define SYS_sync" /opt/xv6-riscv/kernel/syscall.h' >/dev/null 2>&1 \
  && ok "the tree reads the host's file through the symlink" || bad "symlink does not resolve"

# -- 3. student edits, on the host ----------------------------------------- #
head_ "3. Edit on the host (as a student's editor would)"
D="$WORK/M1"
awk '{print} /#define SYS_sync/{print "#define SYS_labtest 23"}' "$D/syscall.h" > "$D/.t" && mv "$D/.t" "$D/syscall.h"
awk '{print}
     /extern uint64 sys_sync\(void\);/{print "extern uint64 sys_labtest(void);"}
     /\[SYS_sync\].*sys_sync,/{print "[SYS_labtest] sys_labtest,"}' "$D/syscall.c" > "$D/.t" && mv "$D/.t" "$D/syscall.c"
cat >> "$D/sysproc.c" <<'EOC'

uint64
sys_labtest(void)
{
  int mask;
  argint(0, &mask);
  return 1000 + mask;
}
EOC
awk '{print} /^int sync\(void\);/{print "int labtest(int mask);"}' "$D/user.h" > "$D/.t" && mv "$D/.t" "$D/user.h"
awk '{print} /entry\("sync"\);/{print "entry(\"labtest\");"}' "$D/usys.pl" > "$D/.t" && mv "$D/.t" "$D/usys.pl"
awk '{print} /^UPROGS=\\$/{print "\t$U/_labtest\\"}' "$D/Makefile" > "$D/.t" && mv "$D/.t" "$D/Makefile"
cat > "$D/labtest.c" <<'EOC'
#include "kernel/types.h"
#include "user/user.h"
int main(int argc, char *argv[]) {
  int n = argc > 1 ? atoi(argv[1]) : 1;
  for (int i = 0; i < n; i++) printf("labtest(%d) = %d\n", i, labtest(i));
  exit(0);
}
EOC
grep -q SYS_labtest "$D/syscall.h" && grep -q "SYS_labtest\] sys_labtest" "$D/syscall.c" \
  && ok "host edits written (syscall + its test app)" || bad "host edits did not apply"

$ENGINE exec "$NAME" sh -c 'ln -sf /opt/xv6-lab/labtest.c /opt/xv6-riscv/user/labtest.c' >/dev/null 2>&1 \
  && ok "the student's new test app is linked into user/" || bad "could not link the test app"

# -- 4. the build ---------------------------------------------------------- #
head_ "4. Compile the student's kernel INSIDE the shipped image"
S=$(date +%s)
$ENGINE exec "$NAME" sh -c 'cd /opt/xv6-riscv && make kernel/kernel fs.img > /tmp/b.log 2>&1'
RC=$?; E=$(date +%s)
if [ $RC -eq 0 ]; then ok "incremental build succeeded in $((E-S))s (no image rebuild)"
else
    bad "build failed — scoped log follows"
    $ENGINE exec "$NAME" sh -c 'grep -E "error:|undefined reference|No rule" /tmp/b.log | head -5'
fi
# Did make actually recompile, or did it think it was up to date? A silent stale build is the
# worst outcome: the student is told "loaded" and runs the OLD kernel.
$ENGINE exec "$NAME" sh -c 'grep -q "syscall.o" /tmp/b.log' >/dev/null 2>&1 \
  && ok "make saw the host edit and recompiled" \
  || bad "make did NOT recompile — host mtime not visible in the container (stale-build risk)"

# -- 5. does it run? ------------------------------------------------------- #
head_ "5. Boot the student's kernel and run their app"
$ENGINE exec "$NAME" sh -c \
  'python3 -c "
import urllib.request as u
print(u.urlopen(\"http://127.0.0.1:5000/rebuild\", data=b\"\", timeout=200).read().decode())"' \
  >/dev/null 2>&1 && ok "agent /rebuild accepted (Load button path)" || bad "/rebuild failed"
sleep 8
OUT=$($ENGINE exec "$NAME" sh -c 'python3 -c "
import urllib.request as u, time
u.urlopen(\"http://127.0.0.1:5000/input\", data=b\"\\n\", timeout=10).read()
time.sleep(1)
u.urlopen(\"http://127.0.0.1:5000/input\", data=b\"labtest 2\\n\", timeout=10).read()
time.sleep(4)
print(u.urlopen(\"http://127.0.0.1:5000/console\", timeout=10).read().decode()[-300:])"' 2>/dev/null)
case "$OUT" in
  *"labtest(0) = 1000"*) ok "the student's system call ran under QEMU and returned correctly" ;;
  *"unknown sys call"*)  bad "kernel does not know the syscall — a STALE build was loaded" ;;
  *)                     bad "no expected output; console tail: $(printf '%s' "$OUT" | tr '\n' ' ')" ;;
esac

SC=$($ENGINE exec "$NAME" sh -c 'python3 -c "
import urllib.request as u
print(u.urlopen(\"http://127.0.0.1:5000/sc\", timeout=10).read().decode())"' 2>/dev/null | grep '^SC 23')
[ -n "$SC" ] && ok "the Syscall Lab counter sees it: $SC" \
             || bad "syscall 23 not counted (visualization would be blank)"

# -- 6. the stale-build hazard, and the error display ---------------------- #
head_ "6. A fresh host edit against a just-built object file"
cp "$D/sysproc.c" "$D/sysproc.c.keep"
printf '\nuint64\nsys_broken(void)\n{\n  return notdeclared;\n}\n' >> "$D/sysproc.c"

# THE hazard. A .o built seconds ago in check 4, a .c just written on the host. If the mount
# still reports the file's OLD mtime, `make` concludes there is nothing to do -- and /rebuild
# restarts QEMU on the PREVIOUS kernel while answering {"ok": true, "log": "loaded"}. Silent,
# and it sends the student looking at their own code. On a native Linux bind mount host and
# container share one page cache and this cannot happen; that is what this line measures.
if $ENGINE exec "$NAME" sh -c 'cd /opt/xv6-riscv && make -q kernel/kernel' >/dev/null 2>&1; then
    warn "make says 'up to date' right after a host edit — attribute-cache lag on this engine"
    say "       Unmitigated, this is the silent stale build: Load answers \"loaded\" and QEMU"
    say "       comes back on the PREVIOUS kernel. The agent forces the issue in _touch_sources,"
    say "       and the next check proves it works. Expected to PASS outright on Linux."
else
    ok "make saw the host edit on its own — no attribute-cache lag on this engine"
fi

# Whatever the answer above, pressing Load must report the error. Go through the AGENT, because
# that is what the button does, and it is the agent's scoped log the student actually reads.
LOG=$($ENGINE exec "$NAME" sh -c 'python3 -c "
import urllib.request as u
print(u.urlopen(\"http://127.0.0.1:5000/rebuild\", data=b\"\", timeout=200).read().decode())"' 2>/dev/null)
case "$LOG" in
  *notdeclared*)
    ok "Load reported the error, scoped to what the student needs:"
    printf '%s' "$LOG" | tr ',' '\n' | grep -o "sysproc.c:[0-9]*:[0-9]*: error: [^\\]*" | head -2 \
        | sed 's/^/          /'
    ;;
  *'"ok": true'*)
    bad "Load reported SUCCESS for code that does not compile (stale build reached the student)"
    ;;
  *) bad "Load gave no usable diagnostic: $(printf '%s' "$LOG" | head -c 160)" ;;
esac

mv "$D/sysproc.c.keep" "$D/sysproc.c"
$ENGINE exec "$NAME" sh -c 'cd /opt/xv6-riscv && touch kernel/sysproc.c && make kernel/kernel fs.img >/dev/null 2>&1'

# -- 7. survive a Stop/Run ------------------------------------------------- #
head_ "7. Survive Stop/Run (compose down destroys the container)"
$ENGINE rm -f "$NAME" >/dev/null 2>&1
grep -q SYS_labtest "$D/syscall.h" && ok "the student's work is still on the host" \
                                   || bad "the student's work was lost with the container"
$ENGINE run -d --name "$NAME" -v "$MOUNT" "$IMAGE" >/dev/null 2>&1
i=0; while [ $i -lt 30 ]; do
    $ENGINE exec "$NAME" sh -c 'test -f /opt/xv6-riscv/kernel/kernel' >/dev/null 2>&1 && break
    i=$((i+1)); sleep 1
done
$ENGINE exec "$NAME" sh -c 'grep -q SYS_labtest /opt/xv6-riscv/kernel/syscall.h' >/dev/null 2>&1 \
  && bad "a fresh container already has the edit (unexpected)" \
  || ok "a fresh container starts pristine — the link step must re-run on every Run"
$ENGINE exec "$NAME" sh -c '
cd /opt/xv6-riscv; mkdir -p /opt/gini_orig
for pair in "kernel/syscall.h:syscall.h" "kernel/syscall.c:syscall.c" "kernel/sysproc.c:sysproc.c" \
            "user/user.h:user.h" "user/usys.pl:usys.pl" "Makefile:Makefile" "user/labtest.c:labtest.c"; do
    tgt=${pair%%:*}; base=${pair##*:}
    [ -f "/opt/xv6-lab/$base" ] || continue
    if [ ! -L "$tgt" ]; then [ -f "$tgt" ] && cp "$tgt" "/opt/gini_orig/$base"; ln -sf "/opt/xv6-lab/$base" "$tgt"; fi
done
make kernel/kernel fs.img >/tmp/r.log 2>&1' >/dev/null 2>&1 \
  && ok "relink + rebuild recovered the student's work in the new container" \
  || bad "could not recover the student's work after Stop/Run"

# -- verdict --------------------------------------------------------------- #
head_ "Verdict"
say "  $PASS passed, $FAIL failed, $WARN warned, $SKIP skipped   ($ENGINE, $IMAGE)"
[ $FAIL -eq 0 ] && say "  This machine can run a student-code xv6 lab with the SHIPPED image." \
                || say "  Something above needs fixing before a lab ships on this machine."
exit $FAIL
