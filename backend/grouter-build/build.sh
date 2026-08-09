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

# gr_mod_lua.c needs lua.h and is only built with -DGR_LUA; exclude it from the default glob
# unless LUA=1 (the default): the chapter-7 scripting tier needs it, and the Docker image
# installs liblua5.4-dev. Set LUA=0 for a no-Lua build.
LUA="${LUA:-1}"
if [ "$LUA" = "1" ]; then
    SRCS=$(ls "$SRC"/*.c)
    LUA_FLAGS="-DGR_LUA"
    # locate lua.h + lib. Debian's liblua5.4-dev puts headers in /usr/include/lua5.4
    # (NOT on the default include path), so we must add -I explicitly. Prefer
    # pkg-config; fall back to common Debian/Homebrew locations.
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
            echo "build.sh: lua.h not found — building WITHOUT the Lua module "
            echo "          (install liblua5.4-dev, or run with LUA=0 to silence this)." >&2
            SRCS=$(ls "$SRC"/*.c | grep -vE '/gr_mod_lua\.c$|/gr_cp_lua\.c$')
            LUA_FLAGS=""; LUA_LIBS=""
        else
            LUA_FLAGS="$LUA_FLAGS $LUA_INC"
            LUA_LIBS="-llua5.4"
        fi
    fi
else
    SRCS=$(ls "$SRC"/*.c | grep -vE '/gr_mod_lua\.c$|/gr_cp_lua\.c$')
    LUA_FLAGS=""
    LUA_LIBS=""
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
