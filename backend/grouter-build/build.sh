#!/usr/bin/env bash
# Build the real gRouter with a plain C compiler (clang or gcc).
#
# Zig was removed from GINI: the router is one systems language — C — with Lua for student
# modules and Python for the app. The router always builds INSIDE a Linux Docker image, so the
# cross-compilation that `zig cc` offered was never used here; a normal clang/gcc is equivalent
# and has one fewer dependency (no pip `ziglang`).
#
#   CC=clang ./build.sh          # or CC=gcc ./build.sh   (default: cc)
#
# (This directory is still named grouter-build/ for now — its path is referenced from the
#  frontend's image layout; a rename is a separate, coordinated change.)
set -euo pipefail

CC="${CC:-cc}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${SRC:-$HERE/../src/grouter}"
INC="${INC:-$HERE/../include}"
OUT="${OUT:-$HERE/grouter}"
# Where libslack/readline headers+libs live. Docker installs them to /usr/local;
# override for a user-prefix build (e.g. PREFIX=/tmp/prefix ./build.sh).
PREFIX="${PREFIX:-/usr/local}"

# Lua is REQUIRED, not optional. It is the gRouter's only control-plane surface: protocols
# are Lua modules loaded with `cp add lua <script>` (the C protocol modules that once sat
# beside them were removed, so a no-Lua build would have no control plane at all). It is
# also the scripting tier for data-plane modules. Debian's liblua5.4-dev puts headers in
# /usr/include/lua5.4, NOT on the default include path, so -I must be explicit.
SRCS=$(ls "$SRC"/*.c)
LUA_FLAGS="-DGR_LUA"
if command -v pkg-config >/dev/null 2>&1 && pkg-config --exists lua5.4 2>/dev/null; then
    LUA_FLAGS="$LUA_FLAGS $(pkg-config --cflags lua5.4)"
    LUA_LIBS="$(pkg-config --libs lua5.4)"
else
    LUA_INC=""
    for d in /usr/include/lua5.4 /usr/local/include/lua5.4 \
             /opt/homebrew/include/lua5.4 /usr/local/include /usr/include; do
        if [ -f "$d/lua.h" ]; then LUA_INC="-I$d"; break; fi
    done
    if [ -z "$LUA_INC" ]; then
        echo "build.sh: lua.h not found, and Lua is required." >&2
        echo "          The gRouter's control plane IS Lua: without it the router still" >&2
        echo "          forwards packets, but no protocol can be loaded and every" >&2
        echo "          'cp add lua' fails. Install liblua5.4-dev and build again." >&2
        exit 1
    fi
    LUA_FLAGS="$LUA_FLAGS $LUA_INC"
    LUA_LIBS="-llua5.4"
fi

# Legacy-C build flags (this is ~20k lines of pre-C99-style GINI code that predates modern
# compiler hardening; these make it build *and run* under a modern clang/gcc the way it did
# under the original gcc):
#   -fcommon                            tentative globals defined in headers (route_tbl,
#                                       MTU_tbl, …) must merge, not collide — modern clang
#                                       defaults to -fno-common.
#   -Wno-implicit-function-declaration  K&R-style calls across TUs (consoleRestart,
#                                       openflow_config_* …) are warnings, not errors.
#   -Wno-int-conversion                 legacy int<->pointer casts.
#   -fno-stack-protector -D_FORTIFY_SOURCE=0
#                                       the code predates stack-protector/_FORTIFY and has
#                                       latent small overwrites the canary aborts on. (Fix the
#                                       overwrites incrementally, then re-enable — that, not a
#                                       language change, is the real memory-safety win.)
# NOTE: `-fno-sanitize=undefined` is GONE — it existed only because `zig cc` traps UB by
#       default at startup; clang/gcc don't, so the flag is unnecessary now.
echo "building gRouter with: $CC"
$CC -o "$OUT" $SRCS \
    -I "$INC" -I "$PREFIX/include" -L "$PREFIX/lib" \
    -rdynamic \
    -DHAVE_PTHREAD_RWLOCK=1 -DHAVE_GETOPT_LONG -DGR_LEGACY_MODULES $LUA_FLAGS -g -w \
    -fcommon -Wno-implicit-function-declaration -Wno-int-conversion \
    -fno-stack-protector -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 \
    -lreadline -ltermcap -lslack -lpthread -lutil -lm $LUA_LIBS

echo "built: $OUT"
