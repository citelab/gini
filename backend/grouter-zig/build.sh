#!/usr/bin/env bash
# Z0 — build the real gRouter with `zig cc` (robust, version-stable).
# Used by the Docker build; also runnable directly once libslack/readline exist.
#
#   ZIG="python3 -m ziglang" ./build.sh        # zig via the pip 'ziglang' package
#   ZIG="zig" ./build.sh                        # zig on PATH
set -euo pipefail

ZIG="${ZIG:-zig}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${SRC:-$HERE/../src/grouter}"
INC="${INC:-$HERE/../include}"
OUT="${OUT:-$HERE/grouter}"
# Where libslack/readline headers+libs live. Docker installs them to /usr/local;
# override for a user-prefix build (e.g. PREFIX=/tmp/prefix ./build.sh).
PREFIX="${PREFIX:-/usr/local}"

# gr_mod_lua.c needs lua.h and is only built with -Dlua (zig build); exclude it
# from the default glob unless LUA=1 (the default here): the chapter-7 scripting tier needs
# it, and the Docker image installs liblua5.4-dev. Set LUA=0 for a no-Lua build.
LUA="${LUA:-1}"
if [ "$LUA" = "1" ]; then
    SRCS=$(ls "$SRC"/*.c)
    LUA_FLAGS="-DGR_LUA"
    LUA_LIBS="-llua5.4"
else
    SRCS=$(ls "$SRC"/*.c | grep -v '/gr_mod_lua\.c$')
    LUA_FLAGS=""
    LUA_LIBS=""
fi

# Z3: modules ported to the Zig language. Each <name>.zig is compiled to a relocatable
# object (ReleaseSafe = memory-safe: bounds/overflow checks) and linked alongside the C.
# It exports the same C ABI, so the C callers don't change.
ZIG_OBJS=""
for z in "$SRC"/*.zig; do
    [ -e "$z" ] || continue
    obj="${z%.zig}.zigobj.o"
    echo "  zig module: $(basename "$z") -> $(basename "$obj")"
    $ZIG build-obj "$z" -OReleaseSafe -femit-bin="$obj"
    ZIG_OBJS="$ZIG_OBJS $obj"
done

# Legacy-C build flags (this is ~20k lines of pre-C99-style GINI code that
# predates modern compiler hardening; these make it build *and run* under a
# modern clang/lld the way it did under the original gcc):
#   -fcommon                            tentative globals defined in headers
#                                       (route_tbl, MTU_tbl, …) must merge, not
#                                       collide — modern clang defaults to -fno-common.
#   -Wno-implicit-function-declaration  K&R-style calls across TUs (consoleRestart,
#                                       openflow_config_* …) are warnings, not errors.
#   -Wno-int-conversion                 legacy int<->pointer casts.
#   -fno-sanitize=undefined             zig cc traps on UB by default; the legacy
#                                       code has benign UB (unaligned casts, bitfields)
#                                       that would SIGILL/SIGTRAP at runtime otherwise.
#   -fno-stack-protector -D_FORTIFY_SOURCE=0
#                                       the code predates stack-protector/_FORTIFY and
#                                       has latent small overwrites the canary aborts on.
echo "building gRouter with: $ZIG cc"
$ZIG cc -o "$OUT" $SRCS $ZIG_OBJS \
    -I "$INC" -I "$PREFIX/include" -L "$PREFIX/lib" \
    -rdynamic \
    -DHAVE_PTHREAD_RWLOCK=1 -DHAVE_GETOPT_LONG -DGR_LEGACY_MODULES $LUA_FLAGS -g -w \
    -fcommon -Wno-implicit-function-declaration -Wno-int-conversion \
    -fno-sanitize=undefined -fno-stack-protector -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 \
    -lreadline -ltermcap -lslack -lpthread -lutil -lm $LUA_LIBS

echo "built: $OUT"
