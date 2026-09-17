#!/bin/sh
# gini-doctor Stage 0 — find a Python that can run the doctor. Linux and macOS.
#
# This is the only shell left in the doctor, and it has one job. GINI needs Python, so a machine
# with no usable Python is not a machine the doctor fails on; it is the diagnosis. Stage 0 looks
# at every interpreter it can find, records what it found as facts, picks the best one that meets
# the floor, and hands off to Stage 1 (Python) with those facts. If nothing qualifies, Stage 0
# writes the same JSON report Stage 1 would, containing what it found, and stops.
#
#   sh stage0.sh [stage 1 arguments…]
#
# Environment:
#   GINI_DOCTOR_PYTHON      try this interpreter first
#   GINI_DOCTOR_MIN_PYTHON  override the floor (e.g. 3.10)
#   GINI_HEALTHCENTER       fetch the floor from <url>/doctor/policy.txt (5 s timeout; offline is fine)
#
# POSIX sh (dash, busybox ash, bash). Never blocks: stdin is closed for every command it runs,
# and on macOS /usr/bin/python3 is not touched without Command Line Tools, because there it is a
# stub that opens an installer dialog.
#
# Windows: stage0.ps1 is the same program in PowerShell.
set -u

BUILTIN_MIN_PYTHON=3.8
FACTS=""
TAB=$(printf '\t')

flat() { printf '%s' "$*" | tr '\n\t\r' '   ' | sed 's/  */ /g; s/^ //; s/ $//'; }

# Usernames and home paths do not leave the machine (the same rule as Stage 1's redact.py).
redact() {
    _u=$(id -un 2>/dev/null || echo "")
    printf '%s' "$1" | awk -v h="${HOME:-}" -v u="$_u" '
        function repl(s, from, to,    out, i) {
            out = ""
            while (length(from) > 0 && (i = index(s, from)) > 0) {
                out = out substr(s, 1, i - 1) to
                s = substr(s, i + length(from))
            }
            return out s
        }
        function word(c) { return c ~ /[A-Za-z0-9_-]/ }
        function user(s,    out, i, before, after) {
            out = ""
            while (length(u) >= 3 && (i = index(s, u)) > 0) {
                before = (i > 1) ? substr(s, i - 1, 1) : ""
                after = substr(s, i + length(u), 1)
                if ((before == "" || !word(before) && before != ".") && (after == "" || !word(after)))
                    out = out substr(s, 1, i - 1) "<user>"
                else
                    out = out substr(s, 1, i - 1 + length(u))
                s = substr(s, i + length(u))
            }
            return out s
        }
        { line = (length(h) > 1) ? repl($0, h, "~") : $0; print user(line) }'
}

fact() {
    _v=$(redact "$(flat "$2")")
    FACTS="${FACTS}$1${TAB}${_v}
"
}

have() { command -v "$1" >/dev/null 2>&1; }

# vge A B — is version A >= version B (major.minor[.patch])?
vge() {
    awk -v a="$1" -v b="$2" 'BEGIN {
        split(a, x, "."); split(b, y, ".")
        for (i = 1; i <= 3; i++) { xi = x[i] + 0; yi = y[i] + 0
            if (xi > yi) exit 0; if (xi < yi) exit 1 }
        exit 0 }'
}

# --------------------------------------------------------------------------- platform
case "$(uname -s 2>/dev/null)" in
    Linux)   PLATFORM=linux ;;
    Darwin)  PLATFORM=macos ;;
    MINGW*|MSYS*|CYGWIN*) PLATFORM=windows ;;
    *)       PLATFORM=linux ;;
esac
fact platform "$PLATFORM"
fact uname "$(uname -srm 2>/dev/null)"

# --------------------------------------------------------------------------- the floor
MIN_PYTHON=$BUILTIN_MIN_PYTHON
MIN_SOURCE=builtin
if [ -n "${GINI_DOCTOR_MIN_PYTHON:-}" ]; then
    MIN_PYTHON=$GINI_DOCTOR_MIN_PYTHON; MIN_SOURCE=environment
elif [ -n "${GINI_HEALTHCENTER:-}" ]; then
    _url="${GINI_HEALTHCENTER%/}/doctor/policy.txt"
    _txt=""
    if have curl; then
        _txt=$(curl -fsS --max-time 5 "$_url" </dev/null 2>/dev/null) || _txt=""
    elif have wget; then
        _txt=$(wget -q -T 5 -O - "$_url" </dev/null 2>/dev/null) || _txt=""
    fi
    _mp=$(printf '%s\n' "$_txt" | sed -n 's/^[[:space:]]*min_python[[:space:]]*=[[:space:]]*\([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | head -n 1)
    if [ -n "$_mp" ]; then
        MIN_PYTHON=$_mp; MIN_SOURCE=live
    else
        fact policy.note "Health Center policy unavailable; using the built-in floor"
    fi
fi
case "$MIN_PYTHON" in
    [0-9]*.[0-9]*) ;;
    *) fact policy.note "ignored malformed floor '$MIN_PYTHON'"; MIN_PYTHON=$BUILTIN_MIN_PYTHON; MIN_SOURCE=builtin ;;
esac
fact min_python "$MIN_PYTHON"
fact min_python.source "$MIN_SOURCE"

# --------------------------------------------------------------------------- candidates
CANDIDATES=""
add_candidate() {
    [ -n "$1" ] || return 0
    case "
$CANDIDATES" in *"
$1
"*) return 0 ;; esac
    CANDIDATES="${CANDIDATES}$1
"
}

# The interpreter that actually runs gBuilder. Reading the launcher is how the legacy doctor
# stopped reporting "No module named PySide6" on every machine from the wrong Python. Two shapes:
# a plain `#!/path/to/python`, or pipx's trampoline, which is `#!/bin/sh` followed by
#     '''exec' "/.../pipx/venvs/gini-toolkit/bin/python" "$0" "$@"
# where the quoted path can contain spaces ("Application Support" on macOS).
gbuilder_python() {
    _g=$(command -v gbuilder 2>/dev/null) || return 0
    [ -r "$_g" ] || return 0
    _line=$(head -n 1 "$_g" 2>/dev/null)
    case "$_line" in '#!'*) ;; *) return 0 ;; esac
    set -- $(printf '%s' "${_line#\#!}")
    [ $# -ge 1 ] || return 0
    _i=$1
    if [ "${1##*/}" = env ]; then
        [ $# -ge 2 ] || return 0
        _i=$(command -v "$2" 2>/dev/null) || return 0
    fi
    case "${_i##*/}" in
        python*) printf '%s\n' "$_i" ;;
        *)
            _p=$(sed -n 's|.*exec[^"]*"\([^"]*/bin/python[0-9.]*\)".*|\1|p' "$_g" 2>/dev/null | head -n 1)
            [ -n "$_p" ] || _p=$(sed -n 's|.*[^A-Za-z0-9_./-]\(/[A-Za-z0-9_./+-]*/bin/python[0-9.]*\).*|\1|p' "$_g" 2>/dev/null | head -n 1)
            printf '%s\n' "$_p" ;;
    esac
}

GBUILDER=$(command -v gbuilder 2>/dev/null || true)
GBUILDER_PY=$(gbuilder_python)
if [ -z "$GBUILDER" ]; then
    fact gbuilder.launcher "not on PATH"
else
    fact gbuilder.launcher "$GBUILDER ($(head -n 1 "$GBUILDER" 2>/dev/null | cut -c1-80))"
    fact gbuilder.python "${GBUILDER_PY:-not identified from the launcher}"
fi

add_candidate "${GINI_DOCTOR_PYTHON:-}"
add_candidate "$GBUILDER_PY"
for _venv in "${PIPX_HOME:-}" "$HOME/.local/share/pipx" "$HOME/.local/pipx" "$HOME/Library/Application Support/pipx"; do
    [ -n "$_venv" ] && [ -x "$_venv/venvs/gini-toolkit/bin/python" ] && add_candidate "$_venv/venvs/gini-toolkit/bin/python"
done
for _name in python3 python; do
    add_candidate "$(command -v "$_name" 2>/dev/null || true)"
done
for _p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    [ -x "$_p" ] && add_candidate "$_p"
done

CHOSEN=""
CHOSEN_VERSION=""
_n=0
_old_ifs=$IFS
IFS='
'
for _c in $CANDIDATES; do
    IFS=$_old_ifs
    _n=$((_n + 1))
    fact "python.$_n.path" "$_c"
    if [ "$PLATFORM" = macos ] && [ "$_c" = /usr/bin/python3 ] && ! xcode-select -p >/dev/null 2>&1; then
        fact "python.$_n.verdict" "skipped: Command Line Tools not installed (this python3 is an installer stub)"
        continue
    fi
    if [ ! -x "$_c" ]; then
        fact "python.$_n.verdict" "not executable"
        continue
    fi
    _ver=$("$_c" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' </dev/null 2>/dev/null) || _ver=""
    case "$_ver" in
        [0-9]*.[0-9]*.[0-9]*) ;;
        *) fact "python.$_n.verdict" "does not run"; continue ;;
    esac
    fact "python.$_n.version" "$_ver"
    if vge "$_ver" "$MIN_PYTHON"; then
        if [ -z "$CHOSEN" ]; then
            CHOSEN=$_c; CHOSEN_VERSION=$_ver
            fact "python.$_n.verdict" "chosen"
        else
            fact "python.$_n.verdict" "usable"
        fi
    else
        fact "python.$_n.verdict" "below floor $MIN_PYTHON"
    fi
done
IFS=$_old_ifs
fact python.candidates "$_n"

# --------------------------------------------------------------------------- Stage 1
# From a checkout or an unpacked wheel, Stage 1 sits next to this file. When piped (curl … | sh)
# $0 is the shell itself and there is no "next to"; then the chosen Python may have it installed.
STAGE1_PATH=""
case "$0" in
    */stage0.sh|stage0.sh)
        _here=$(cd "$(dirname "$0")" 2>/dev/null && pwd) || _here=""
        [ -n "$_here" ] && [ -r "$_here/../stage1/__init__.py" ] && STAGE1_PATH=$(cd "$_here/../.." && pwd)
        ;;
esac

STAGE1=""
if [ -n "$CHOSEN" ]; then
    if [ -n "$STAGE1_PATH" ]; then
        STAGE1=checkout
    elif "$CHOSEN" -c 'import gini_doctor.stage1' </dev/null >/dev/null 2>&1; then
        STAGE1=installed
    fi
fi

if [ -n "$CHOSEN" ] && [ -n "$STAGE1" ]; then
    fact python.chosen "$CHOSEN ($CHOSEN_VERSION)"
    fact stage1 "$STAGE1"
    GINI_DOCTOR_STAGE0=$FACTS
    export GINI_DOCTOR_STAGE0
    if [ -n "$STAGE1_PATH" ]; then
        PYTHONPATH="$STAGE1_PATH${PYTHONPATH:+:$PYTHONPATH}"
        export PYTHONPATH
    fi
    exec "$CHOSEN" -m gini_doctor.stage1 "$@"
fi

# --------------------------------------------------------------------------- no Stage 1: report
if [ -z "$CHOSEN" ]; then
    VERDICT="no usable Python: GINI needs Python $MIN_PYTHON or newer, and none of the $_n interpreter(s) found qualifies"
else
    fact python.chosen "$CHOSEN ($CHOSEN_VERSION)"
    VERDICT="a usable Python was found, but the doctor's Stage 1 is not available to it (install gini-doctor, or run stage0.sh from a checkout)"
fi
fact verdict "$VERDICT"

json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr -d '\000-\037'; }

HOST=$(hostname 2>/dev/null || uname -n 2>/dev/null || echo unknown)
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)

report_json() {
    printf '{\n'
    printf '  "schema": "gini-doctor/1",\n'
    printf '  "doctor": {"engine": "stage0-sh", "version": "1.0.0.dev0"},\n'
    printf '  "host": "%s",\n' "$(json_escape "$HOST")"
    printf '  "collected_at": "%s",\n' "$NOW"
    printf '  "platform": "%s",\n' "$PLATFORM"
    printf '  "policy": {"source": "%s", "version": "stage0"},\n' "$MIN_SOURCE"
    printf '  "groups": ["stage0"],\n'
    printf '  "facts": {\n'
    printf '%s' "$FACTS" | sort | awk -F '\t' '
        { gsub(/\\/, "\\\\", $2); gsub(/"/, "\\\"", $2); lines[++n] = sprintf("    \"stage0.%s\": {\"status\": \"ok\", \"value\": \"%s\"}", $1, $2) }
        END { for (i = 1; i <= n; i++) printf "%s%s\n", lines[i], (i < n ? "," : "") }'
    printf '  }\n}\n'
}

_stdout=0
for _a in "$@"; do [ "$_a" = "--stdout" ] && _stdout=1; done

if [ "$_stdout" = 1 ]; then
    report_json
else
    _stamp=$(printf '%s' "$NOW" | tr -d ':-')
    _file="gini-doctor-$(printf '%s' "$HOST" | tr -c 'A-Za-z0-9._-' '_')-$_stamp.json"
    report_json > "$_file"
    printf '\ngini-doctor (Stage 0)  host=%s  platform=%s\n\n' "$HOST" "$PLATFORM"
    printf '%s' "$FACTS" | awk -F '\t' '{ printf "    %-24s %s\n", $1, $2 }'
    printf '\n  %s\n\n  report saved: %s\n\n' "$VERDICT" "$_file"
fi
exit 3
