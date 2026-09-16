#!/bin/sh
# gini-doctor — what is different about THIS machine?
#
# Written for a lab rollout where one machine works and thirty do not. Guessing does not scale to
# thirty boxes, so this does not guess: it collects the same facts everywhere, in a stable
# key<TAB>value form, and then DIFFS them. The output that matters is not any single report, it is
# the short list of fields on which the broken machines disagree with the working one.
#
#   sh gini-doctor.sh                      # probe this machine; human summary + a report file
#   sh gini-doctor.sh --report             # just the report, to stdout (what --fanout collects)
#   sh gini-doctor.sh --no-run             # skip the live container/compose round trip
#   sh gini-doctor.sh --summarise report.txt       # read a collected report in human form
#   sh gini-doctor.sh --compare a.txt b.txt …      # show ONLY the fields that differ
#   sh gini-doctor.sh --fanout hosts.txt [dir]     # ssh each host, collect, then compare
#
# --fanout pipes THIS FILE over ssh (`ssh host sh -s -- --report < gini-doctor.sh`), so nothing
# has to be installed or copied to the thirty machines first.
#
# POSIX sh on purpose: dash, ash and bash all run it, and a lab image is not guaranteed to have
# bash. Every probe is guarded — a missing tool reports "absent" and never aborts the run, because
# a doctor that dies on the first missing binary is useless on exactly the machine that needs it.
set -u

VERSION=1
SELF=$0

# --------------------------------------------------------------------------- #
# report plumbing
# --------------------------------------------------------------------------- #
# One line per fact. Values are flattened to a single line: the whole point is diffability, and a
# multi-line value would break the key<TAB>value contract the comparer depends on.
emit() {
    _k=$1; shift
    _v=$(printf '%s' "$*" | tr '\n\t' '  ' | sed 's/  */ /g; s/^ //; s/ $//')
    [ -z "$_v" ] && _v="(empty)"
    printf '%s\t%s\n' "$_k" "$_v"
}

# `cap prog args…` — run something that may not exist, capture stdout, never fail the script.
cap() {
    if command -v "$1" >/dev/null 2>&1; then
        "$@" 2>/dev/null || printf 'ERROR(exit=%s)' "$?"
    else
        printf 'absent'
    fi
}

have() { command -v "$1" >/dev/null 2>&1; }

# --------------------------------------------------------------------------- #
# probes
# --------------------------------------------------------------------------- #
probe_identity() {
    emit host "$(hostname 2>/dev/null || echo unknown)"
    emit doctor.version "$VERSION"
    emit user "$(id -un 2>/dev/null)"
    emit uid "$(id -u 2>/dev/null)"
    emit kernel "$(uname -sr 2>/dev/null)"
    emit arch "$(uname -m 2>/dev/null)"
    if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release 2>/dev/null
        emit os.id "${ID:-?}"
        emit os.version "${VERSION_ID:-?}"
        emit os.pretty "${PRETTY_NAME:-?}"
    else
        emit os.id "$(uname -s)"
        emit os.version "$(uname -r)"
        emit os.pretty "no /etc/os-release"
    fi
    emit cpus "$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo ?)"
    emit mem.total.kb "$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo ?)"
    emit disk.home.avail "$(df -Pk "$HOME" 2>/dev/null | awk 'NR==2{print $4}')"
}

probe_engine() {
    emit podman.path "$(command -v podman || echo absent)"
    emit docker.path "$(command -v docker || echo absent)"
    emit podman.version "$(cap podman --version)"
    emit docker.version "$(cap docker --version)"

    # GINI's own rule, replicated: GINI_ENGINE wins, else the settings file, else Docker first and
    # Podman only when there is no docker binary at all. Reported so the report explains what
    # gBuilder will DO, not merely what is installed.
    _forced=${GINI_ENGINE:-}
    emit gini.engine.env "${_forced:-(unset)}"
    if [ -n "$_forced" ]; then
        emit gini.engine.effective "$_forced (forced by GINI_ENGINE)"
    elif have docker && docker info >/dev/null 2>&1; then
        emit gini.engine.effective "docker"
    elif have podman && podman info >/dev/null 2>&1; then
        emit gini.engine.effective "podman"
    elif have docker; then
        emit gini.engine.effective "docker (present but not answering)"
    else
        emit gini.engine.effective "none"
    fi

    if have podman && podman info >/dev/null 2>&1; then
        emit podman.info.ok yes
        for f in \
            'Host.Security.Rootless:podman.rootless' \
            'Host.NetworkBackend:podman.network.backend' \
            'Host.CgroupsVersion:podman.cgroups.version' \
            'Host.CgroupManager:podman.cgroup.manager' \
            'Host.OCIRuntime.Name:podman.oci.runtime' \
            'Host.Conmon.Version:podman.conmon' \
            'Host.Slirp4netns.Executable:podman.slirp4netns' \
            'Store.GraphDriverName:podman.storage.driver' \
            'Store.GraphRoot:podman.storage.graphroot' \
            'Store.RunRoot:podman.storage.runroot'
        do
            _tpl=${f%%:*}; _key=${f##*:}
            emit "$_key" "$(podman info --format "{{.$_tpl}}" 2>/dev/null || echo '?')"
        done
        emit podman.images.count "$(podman images -q 2>/dev/null | wc -l | tr -d ' ')"

        # Does the id mapping podman is ACTUALLY using match what /etc/subuid grants today?
        #
        # These come apart silently. The storage records its mapping when it is first created, so
        # a machine whose subuid range was assigned (or changed) afterwards keeps the old one, and
        # every field a comparison can see still looks right: subuid is present, the range is
        # valid, podman info is happy. It surfaces only when a layer wants a high uid:
        #
        #   potentially insufficient UIDs or GIDs available in user namespace
        #   (requested 65534:65534 for /home) … lchown /home: invalid argument
        #
        # which reads as a subuid problem and is not one — the range is fine, the STORAGE is
        # stale. `podman system migrate` is the fix and podman names it in the error, but only
        # after a pull gets far enough to unpack, so a machine with an unrelated earlier failure
        # (a bad credential, say) never gets to see it.
        _map=$(podman unshare cat /proc/self/uid_map 2>/dev/null | awk 'NR==2{print $2}')
        _sub=$(grep "^$(id -un 2>/dev/null):" /etc/subuid 2>/dev/null | head -1 | cut -d: -f2)
        emit podman.idmap.inuse "${_map:-unknown}"
        emit podman.idmap.subuid "${_sub:-unknown}"
        if [ -n "$_map" ] && [ -n "$_sub" ]; then
            if [ "$_map" = "$_sub" ]; then
                emit podman.idmap.matches "yes"
            else
                emit podman.idmap.matches "NO — /etc/subuid grants $_sub, podman storage uses $_map; run: podman system migrate"
            fi
        else
            emit podman.idmap.matches "unknown"
        fi
    elif have podman; then
        emit podman.info.ok "NO — podman is installed but not answering"
    fi
}

probe_compose() {
    # `podman compose` is a PASS-THROUGH to an external provider, and which provider you get is
    # the single biggest behavioural difference between two podman machines: podman-compose 1.0.6
    # and docker-compose v2 disagree about container naming, `ps --format json`, and what an exit
    # code means. Two machines both "having podman compose" tells you nothing.
    if have podman && podman info >/dev/null 2>&1; then
        _out=$(podman compose version 2>&1 | tr '\n' ' ')
        emit compose.podman.raw "$_out"
        case "$_out" in
            *podman-compose*) emit compose.provider "podman-compose" ;;
            *[Dd]ocker*ompose*) emit compose.provider "docker-compose" ;;
            *) emit compose.provider "unknown-or-missing" ;;
        esac
        # the provider path podman announces in its banner
        emit compose.provider.path "$(printf '%s' "$_out" | sed -n 's/.*provider "\([^"]*\)".*/\1/p')"
    elif have docker && docker info >/dev/null 2>&1; then
        # A Docker machine has no "provider" to choose: compose v2 is a plugin of the CLI.
        emit compose.provider "docker-compose-plugin"
        emit compose.provider.path "(built in)"
    else
        emit compose.provider "no-engine-answering"
    fi
    emit compose.podman_compose.version "$(cap podman-compose --version)"
    emit compose.docker_compose.version "$(cap docker-compose --version)"
    if have docker; then
        emit compose.docker.plugin "$(docker compose version 2>/dev/null | head -1 || echo absent)"
    fi
    for p in /usr/libexec/docker/cli-plugins/docker-compose \
             /usr/lib/docker/cli-plugins/docker-compose \
             "$HOME/.docker/cli-plugins/docker-compose"; do
        [ -x "$p" ] && emit "compose.plugin$(printf '%s' "$p" | tr '/' '.')" present
    done
}

probe_rootless_prereqs() {
    # Rootless podman needs four things that are easy to have on one machine and not the next,
    # and every one of them fails LATE — at `up`, not at install.
    _u=$(id -un 2>/dev/null)
    # `grep … | head -1 || echo MISSING` does NOT work: head exits 0 on empty input, so a machine
    # with no mapping at all reported an empty value that read as fine.
    for m in subuid subgid; do
        if [ ! -r "/etc/$m" ]; then
            emit "$m" "n/a (no /etc/$m)"
        else
            _line=$(grep "^${_u}:" "/etc/$m" 2>/dev/null | head -1)
            emit "$m" "${_line:-MISSING for $_u}"
        fi
    done
    emit userns.max "$(cat /proc/sys/user/max_user_namespaces 2>/dev/null || echo ?)"
    emit userns.unprivileged_clone "$(cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null || echo n/a)"
    emit xdg.runtime.dir "${XDG_RUNTIME_DIR:-(unset)}"
    if [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -d "${XDG_RUNTIME_DIR}" ]; then
        emit xdg.runtime.exists yes
    else
        emit xdg.runtime.exists NO
    fi
    emit session.type "${XDG_SESSION_TYPE:-(unset)}"
    emit linger "$(cap loginctl show-user "$_u" --property=Linger)"
    # cgroup v2 delegation decides whether a CPU cap can be applied at all, which is exactly what
    # a sized element asks for.
    if [ -r /sys/fs/cgroup/cgroup.controllers ]; then
        emit cgroup2.root.controllers "$(cat /sys/fs/cgroup/cgroup.controllers 2>/dev/null)"
        _d=/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers
        emit cgroup2.user.controllers "$(cat "$_d" 2>/dev/null || echo unreadable)"
    else
        emit cgroup2.root.controllers "cgroup v1 or unreadable"
    fi
    emit tool.fuse-overlayfs "$(command -v fuse-overlayfs || echo absent)"
    emit tool.slirp4netns "$(cap slirp4netns --version)"
    emit tool.pasta "$(command -v pasta || echo absent)"
    emit tool.newuidmap "$(command -v newuidmap || echo absent)"
    emit tool.crun "$(cap crun --version)"
    emit tool.runc "$(cap runc --version)"
}

probe_registries_auth() {
    # Both of these bit this rollout already: a short name that will not resolve, and a stale
    # system auth file that turns an anonymous pull into "invalid username/password".
    for f in /etc/containers/registries.conf "$HOME/.config/containers/registries.conf"; do
        if [ -r "$f" ]; then
            emit "reg$(printf '%s' "$f" | tr '/' '.').unqualified" \
                 "$(grep -v '^ *#' "$f" 2>/dev/null | grep -i 'unqualified-search-registries' | head -1)"
            emit "reg$(printf '%s' "$f" | tr '/' '.').shortname" \
                 "$(grep -v '^ *#' "$f" 2>/dev/null | grep -i 'short-name-mode' | head -1)"
        else
            emit "reg$(printf '%s' "$f" | tr '/' '.')" absent
        fi
    done
    for f in /etc/containers/auth.json \
             "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/containers/auth.json" \
             "$HOME/.docker/config.json"; do
        if [ -r "$f" ]; then
            # Size and whether it declares any credential — never the contents.
            emit "auth$(printf '%s' "$f" | tr '/' '.')" \
                 "present bytes=$(wc -c < "$f" 2>/dev/null | tr -d ' ') creds=$(grep -c '"auth"' "$f" 2>/dev/null || echo 0)"
        else
            emit "auth$(printf '%s' "$f" | tr '/' '.')" absent
        fi
    done
    emit containers.conf "$( [ -r /etc/containers/containers.conf ] && echo present || echo absent)"
    emit storage.conf "$( [ -r /etc/containers/storage.conf ] && echo present || echo absent)"
}

# The interpreter that actually runs gBuilder, which is usually NOT /usr/bin/python3: a pipx or
# `pip install --user` install puts gbuilder in ~/.local/bin with a shebang pointing at its own
# venv. Probing the system python instead reports "No module named PySide6" on a machine where
# gBuilder starts perfectly — and reports it on EVERY machine, so it survives the comparison as a
# permanent false alarm, which is worse than not checking at all.
resolve_gini_python() {
    [ -n "${GPY:-}" ] && return 0
    GPY=python3
    _gb=$(command -v gbuilder 2>/dev/null)
    [ -n "$_gb" ] || return 0
    # Two shapes, and the second is the common one. A plain `#!/path/to/python`; or pipx's
    # trampoline, which is `#!/bin/sh` followed by
    #     '''exec' "/…/pipx/venvs/gini-toolkit/bin/python" "$0" "$@"
    # and exists precisely so the shebang can stay short. Reading line 1 there yields /bin/sh, and
    # every probe then runs the wrong interpreter and reports that Python has no Python in it.
    _c1=$(sed -n '1s|^#! *\([^ ]*\).*|\1|p' "$_gb" 2>/dev/null)
    _c2=$(sed -n "2,3s|.*exec' *\"\\([^\"]*\\)\".*|\\1|p" "$_gb" 2>/dev/null | head -1)
    # Verified, not guessed: a candidate counts only if it answers as a Python. The venv path
    # can contain spaces ("Application Support"), so it stays quoted throughout.
    for _c in "$_c2" "$_c1"; do
        [ -n "$_c" ] || continue
        [ -x "$_c" ] || continue
        if [ "$("$_c" -c 'import sys; print("PY")' 2>/dev/null)" = "PY" ]; then
            GPY=$_c
            return 0
        fi
    done
}

probe_python_qt() {
    resolve_gini_python
    emit gbuilder.python "$GPY"
    emit python3.path "$(command -v python3 || echo absent)"
    emit python3.version "$(cap python3 --version)"
    emit python3.venv_module "$(python3 -c 'import venv' 2>/dev/null && echo yes || echo NO)"
    emit pip.version "$(cap python3 -m pip --version)"
    # PySide6 is where a headless-looking machine actually fails, and the message names a library
    # rather than a package, which is why this reports the import error verbatim.
    if [ -x "$GPY" ] || have "$GPY"; then
        emit gbuilder.python.version "$("$GPY" --version 2>&1 | tail -1)"
        _q=$("$GPY" -c 'import PySide6, PySide6.QtWidgets; print(PySide6.__version__)' 2>&1 | tail -1)
        emit pyside6 "$_q"
        _off=$(QT_QPA_PLATFORM=offscreen "$GPY" -c 'from PySide6.QtWidgets import QApplication; QApplication([]); print("ok")' 2>&1 | tail -1)
        emit pyside6.offscreen "$_off"
    fi
    if have ldconfig; then
        for lib in libxcb-cursor libxcb-xinerama libxkbcommon-x11 libEGL libGL; do
            emit "lib.$lib" "$(ldconfig -p 2>/dev/null | grep -c "$lib" | tr -d ' ')"
        done
    fi
    emit display "${DISPLAY:-(unset)}"
    emit wayland "${WAYLAND_DISPLAY:-(unset)}"
    emit qt.qpa "${QT_QPA_PLATFORM:-(unset)}"
}

probe_gini() {
    emit gbuilder.path "$(command -v gbuilder || echo absent)"
    resolve_gini_python
    if [ -x "$GPY" ] || have "$GPY"; then
        for d in gini-core gini-toolkit gini-teaching-center; do
            emit "pkg.$d" "$("$GPY" -m pip show "$d" 2>/dev/null | awk '/^Version:/{v=$2} /^Location:/{sub(/^Location: */,""); l=$0} END{print (v?v:"absent") " @ " (l?l:"-")}')"
        done
        emit gini.import "$("$GPY" -c 'import gini.domain, gini.services.bootstrap; print("ok")' 2>&1 | tail -1)"
    fi
    emit gini.home "$( [ -d "$HOME/.gini" ] && echo present || echo absent)"
    if [ -r "$HOME/.gini/config.json" ]; then
        emit gini.config.engine "$(grep -o '"engine"[^,}]*' "$HOME/.gini/config.json" 2>/dev/null | head -1)"
    fi
    if have podman || have docker; then
        _e=podman; have docker && docker info >/dev/null 2>&1 && _e=docker
        for img in gini-xv6 gini-grouter gini-pox gini-oszoo; do
            emit "image.$img" "$($_e images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep "$img" | tr '\n' ' ' | cut -c1-120)"
            [ -z "$($_e images -q "$img" 2>/dev/null)" ] && emit "image.$img.local" MISSING
        done
    fi
}

# --------------------------------------------------------------------------- #
# the live round trip — the part that actually reproduces the reported failures
# --------------------------------------------------------------------------- #
probe_live() {
    _e=""
    have docker && docker info >/dev/null 2>&1 && _e="docker"
    [ -z "$_e" ] && have podman && podman info >/dev/null 2>&1 && _e="podman"
    if [ -z "$_e" ]; then
        emit live.skipped "no engine answering"
        return
    fi
    # CAN THIS MACHINE TALK TO A REGISTRY? Asked on every machine, always, even when it already
    # has every image it needs. The first version skipped this whenever a local image was present,
    # which meant the WORKING machine never answered it — and "can the broken one pull?" is not a
    # question you can answer without knowing whether the working one can.
    _perr=$($_e pull docker.io/library/busybox:latest 2>&1)
    if [ $? -eq 0 ]; then
        emit live.pull ok
    else
        emit live.pull FAILED
        emit live.pull.error "$(printf '%s' "$_perr" | tail -2 | cut -c1-200)"
        # Retry with EVERY credential source neutralised. --authfile alone is not enough and
        # saying so matters: podman falls back to $DOCKER_CONFIG/config.json (default
        # ~/.docker/config.json) when the authfile has no entry for the registry, so an
        # "--authfile /dev/null" test still sends the stored docker credential and still fails —
        # which reads as "not a credential problem" when it is precisely a credential problem.
        _cfg=${TMPDIR:-/tmp}/gini-doctor-emptycfg.$$
        mkdir -p "$_cfg" 2>/dev/null
        printf '{}' > "$_cfg/config.json" 2>/dev/null
        printf '{}' > "$_cfg/auth.json" 2>/dev/null
        if DOCKER_CONFIG="$_cfg" REGISTRY_AUTH_FILE="$_cfg/auth.json" \
           $_e pull --authfile "$_cfg/auth.json" docker.io/library/busybox:latest >/dev/null 2>&1
        then
            emit live.pull.without_creds "OK — a STORED CREDENTIAL is what blocks the pull"
        else
            emit live.pull.without_creds "still fails with no credentials at all"
        fi
        rm -rf "$_cfg" 2>/dev/null
    fi

    # An image for the round trip: one already here, so a broken pull does not mask everything
    # else on a machine that has plenty of images and one bad credential.
    _img=$($_e images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null \
           | grep -v '<none>' | head -1)
    if [ -z "$_img" ]; then
        emit live.skipped "no local image to exercise, and the pull above failed"
        return
    fi
    emit live.image "$_img"

    emit live.run "$($_e run --rm "$_img" true >/dev/null 2>&1 && echo ok || echo FAILED)"

    _proj="ginidoctor$$"
    _dir=${TMPDIR:-/tmp}/$_proj
    mkdir -p "$_dir" 2>/dev/null || { emit live.compose "no tmp dir"; return; }
    cat > "$_dir/docker-compose.yml" <<YML
name: $_proj
services:
  probe:
    image: $_img
    command: sh -c "sleep 120"
YML
    ( cd "$_dir" && $_e compose up -d >/dev/null 2>&1 )
    _up=$?
    emit live.compose.up "$( [ $_up -eq 0 ] && echo "exit 0" || echo "exit $_up" )"

    # THE question. `compose up` exiting 0 is not the same as a container existing, and on
    # podman-compose it is routinely false.
    _cid=$($_e ps -q --filter "label=com.docker.compose.project=$_proj" \
                     --filter "label=com.docker.compose.service=probe" 2>/dev/null | head -1)
    emit live.container.by_label "$( [ -n "$_cid" ] && echo "found $_cid" || echo NOT-FOUND )"
    emit live.container.name "$($_e ps -a --filter "label=com.docker.compose.project=$_proj" --format '{{.Names}}' 2>/dev/null | head -1)"

    # The two ways GINI runs a command inside a container. They fail independently, and on
    # podman-compose the second one is what broke: it builds a container NAME and the name it
    # builds does not exist.
    if [ -n "$_cid" ]; then
        emit live.exec.engine "$($_e exec -i "$_cid" sh -c 'echo ok' 2>&1 | tail -1)"
    else
        emit live.exec.engine "skipped (no container)"
    fi
    emit live.exec.compose "$( (cd "$_dir" && $_e compose exec -T probe sh -c 'echo ok' 2>&1) | tail -1 | cut -c1-140)"

    # `compose ps -q <svc>` is what decides "did it start?" — an empty answer here with a
    # container present by label is precisely the false "did not start" report.
    _psq=$( (cd "$_dir" && $_e compose ps -q probe 2>/dev/null) | head -1)
    emit live.compose.ps_q "$( [ -n "$_psq" ] && echo "returned an id" || echo EMPTY )"

    ( cd "$_dir" && $_e compose down >/dev/null 2>&1 )
    emit live.compose.down "$( [ $? -eq 0 ] && echo "exit 0" || echo "exit $?" )"
    # leave nothing behind, even if `down` failed
    _left=$($_e ps -aq --filter "label=com.docker.compose.project=$_proj" 2>/dev/null)
    if [ -n "$_left" ]; then
        emit live.compose.down.leftovers "$(printf '%s' "$_left" | wc -l | tr -d ' ')"
        # shellcheck disable=SC2086
        $_e rm -f $_left >/dev/null 2>&1
    else
        emit live.compose.down.leftovers 0
    fi
    rm -rf "$_dir" 2>/dev/null
}

run_probes() {
    probe_identity
    probe_engine
    probe_compose
    probe_rootless_prereqs
    probe_registries_auth
    probe_python_qt
    probe_gini
    [ "${NORUN:-0}" = "1" ] || probe_live
}

# --------------------------------------------------------------------------- #
# human summary — the handful of facts that decide whether GINI works
# --------------------------------------------------------------------------- #
summarise() {
    _f=$1
    get()  { awk -F'\t' -v k="$1" '$1==k{print $2; exit}' "$_f"; }
    line() { printf '  %-5s %-26s %s\n' "$2" "$1" "$3"; }
    # A real `case` writing into a variable, never a `case` inside $( ) — the closing paren of a
    # case pattern ends the command substitution first, and every one of these lines becomes a
    # syntax error that still prints something plausible next to it.

    printf '\n\033[1mgini-doctor — %s\033[0m\n' "$(get host)"
    printf '  %s · %s · %s\n\n' "$(get os.pretty)" "$(get kernel)" "$(get arch)"

    v=$(get gini.engine.effective)
    case "$v" in none|*"not answering"*) s=FAIL ;; *) s=ok ;; esac
    line "engine gBuilder uses" "$s" "$v"

    v=$(get compose.provider)
    case "$v" in unknown*) s=FAIL ;; *) s=ok ;; esac
    line "compose provider" "$s" "$v"

    v=$(get podman.rootless); [ -n "$v" ] && line "podman rootless" "info" "$v"

    v=$(get subuid)
    case "$v" in MISSING*) s=FAIL ;; n/a*) s=info ;; *) s=ok ;; esac
    line "subuid mapping" "$s" "$v"

    v=$(get podman.idmap.matches)
    if [ -n "$v" ]; then
        case "$v" in yes) s=ok ;; unknown) s=info ;; *) s=FAIL ;; esac
        line "podman id mapping" "$s" "$v"
    fi

    v=$(get xdg.runtime.exists)
    case "$v" in NO) s=warn ;; *) s=ok ;; esac
    line "XDG_RUNTIME_DIR" "$s" "$v ($(get xdg.runtime.dir))"

    v=$(get pyside6)
    case "$v" in *rror*) s=FAIL ;; absent|"") s=warn ;; *) s=ok ;; esac
    line "PySide6 import" "$s" "$v"

    v=$(get pyside6.offscreen)
    case "$v" in ok) s=ok ;; "") s=warn ;; *) s=FAIL ;; esac
    line "Qt starts headless" "$s" "$v"

    v=$(get gini.import)
    case "$v" in ok) s=ok ;; "") s=warn ;; *) s=FAIL ;; esac
    line "gini imports" "$s" "$v"

    v=$(get image.gini-xv6.local)
    if [ -n "$v" ]; then
        line "GINI images present" "FAIL" "$v — nothing can launch until these are here"
    fi

    v=$(get live.pull)
    if [ -n "$v" ]; then
        case "$v" in ok) s=ok ;; *) s=FAIL ;; esac
        line "pull from a registry" "$s" "$v"
        w=$(get live.pull.error);        [ -n "$w" ] && line "  pull error" "info" "$w"
        w=$(get live.pull.without_creds);[ -n "$w" ] && line "  without credentials" "info" "$w"
    fi

    v=$(get live.run)
    if [ -n "$v" ]; then
        case "$v" in ok) s=ok ;; *) s=FAIL ;; esac
        line "run a container" "$s" "$v"
    fi

    v=$(get live.container.by_label)
    if [ -n "$v" ]; then
        case "$v" in found*) s=ok ;; *) s=FAIL ;; esac
        line "found by label" "$s" "$v"
    fi

    v=$(get live.exec.engine)
    if [ -n "$v" ]; then
        case "$v" in ok) s=ok ;; skipped*) s=warn ;; *) s=FAIL ;; esac
        line "exec via engine" "$s" "$v"
    fi

    # This one is EXPECTED to fail on podman-compose, and gBuilder no longer depends on it.
    # It is reported so the difference between two machines is visible, not as a verdict.
    v=$(get live.exec.compose)
    if [ -n "$v" ]; then
        # podman-compose prints "exit code: 0" rather than the command's own output, so the
        # success case does not look like one.
        case "$v" in ok|"exit code: 0") s=ok ;; *) s=warn ;; esac
        line "exec via compose" "$s" "$v"
    fi

    v=$(get live.compose.ps_q)
    if [ -n "$v" ]; then
        case "$v" in EMPTY) s=warn ;; *) s=ok ;; esac
        line "compose ps -q answers" "$s" "$v"
    fi

    v=$(get live.compose.down.leftovers)
    if [ -n "$v" ]; then
        case "$v" in 0) s=ok ;; *) s=warn ;; esac
        line "teardown leftovers" "$s" "$v"
    fi

    printf '\n  full report: %s  (%s facts)\n' "$_f" "$(wc -l < "$_f" | tr -d ' ')"
    printf '  compare with:  sh %s --compare r1.txt r2.txt …\n\n' "$SELF"
}

# --------------------------------------------------------------------------- #
# compare — the actual deliverable when thirty machines disagree
# --------------------------------------------------------------------------- #
compare() {
    [ $# -lt 2 ] && { echo "need at least two reports to compare" >&2; exit 2; }
    awk -F'\t' -v all="${SHOWALL:-0}" '
      FNR==1 { n++; file[n]=FILENAME; name[n]=FILENAME }
      $1=="host" { name[n]=$2 }
      { v[$1 SUBSEP n]=$2; if (!($1 in seen)) { seen[$1]=1; order[++m]=$1 } }
      END {
        printf "\n%d reports: ", n
        for (i=1;i<=n;i++) printf "%s%s", name[i], (i<n?", ":"\n")
        printf "\n"
        diff=0
        for (j=1;j<=m;j++) {
          k=order[j]
          if (k=="host" || k=="doctor.version") continue
          # Facts that differ on every machine for reasons nobody cares about. Across thirty
          # boxes these would be the first thirty lines of output and would bury the one that
          # matters. `--compare-all` keeps them.
          if (!all && (k=="disk.home.avail" || k=="podman.images.count")) continue
          first=""; same=1; got=0
          for (i=1;i<=n;i++) {
            val = ((k SUBSEP i) in v) ? v[k SUBSEP i] : "(absent)"
            if (!got) { first=val; got=1 } else if (val!=first) same=0
          }
          if (same) continue
          diff++
          printf "\033[1m%s\033[0m\n", k
          for (i=1;i<=n;i++) {
            val = ((k SUBSEP i) in v) ? v[k SUBSEP i] : "(absent)"
            printf "    %-22s %s\n", name[i], substr(val,1,110)
          }
        }
        if (diff==0) printf "  The machines are identical on every fact collected.\n"
        else printf "\n%d field(s) differ, out of %d collected.\n", diff, m
        printf "\n"
      }' "$@"
}

# --------------------------------------------------------------------------- #
# fanout — thirty machines, one command, nothing to install
# --------------------------------------------------------------------------- #
fanout() {
    _hosts=$1
    _out=${2:-gini-doctor-reports}
    [ -r "$_hosts" ] || { echo "cannot read host list: $_hosts" >&2; exit 2; }
    mkdir -p "$_out" || exit 2
    while IFS= read -r h; do
        case "$h" in ''|\#*) continue ;; esac
        printf 'collecting %-24s ' "$h"
        if ssh -o BatchMode=yes -o ConnectTimeout=10 "$h" 'sh -s -- --report' \
               < "$SELF" > "$_out/$h.txt" 2>"$_out/$h.err"; then
            printf 'ok (%s facts)\n' "$(wc -l < "$_out/$h.txt" | tr -d ' ')"
        else
            printf 'FAILED — see %s\n' "$_out/$h.err"
            rm -f "$_out/$h.txt"
        fi
    done < "$_hosts"
    set -- "$_out"/*.txt
    [ -r "$1" ] || { echo "no reports collected" >&2; exit 1; }
    compare "$@"
}

# --------------------------------------------------------------------------- #
case "${1:-}" in
  --summarise|--summarize)
                 shift; [ -r "${1:-}" ] || { echo "usage: $0 --summarise report.txt" >&2; exit 2; }
                 summarise "$1" ;;
  --compare)     shift; compare "$@" ;;
  --compare-all) shift; SHOWALL=1 compare "$@" ;;
  --fanout)  shift; [ $# -ge 1 ] || { echo "usage: $0 --fanout hosts.txt [outdir]" >&2; exit 2; }
             fanout "$@" ;;
  --report)  NORUN=${NORUN:-0}; [ "${2:-}" = "--no-run" ] && NORUN=1; run_probes ;;
  --no-run)  NORUN=1
             _f="gini-doctor-$(hostname 2>/dev/null || echo host).txt"
             run_probes > "$_f"; summarise "$_f" ;;
  -h|--help) sed -n '2,20p' "$SELF" ;;
  "")        _f="gini-doctor-$(hostname 2>/dev/null || echo host).txt"
             run_probes > "$_f"; summarise "$_f" ;;
  *)         echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
esac
