#!/usr/bin/env bash
# Run the Teaching Center (v1) for a test drive.
#
#   ./teaching-center/run.sh                     # localhost:8080, course data in ./tc-data
#   PORT=9000 ADMIN_PASSWORD=secret ./teaching-center/run.sh
#
# For DEVELOPMENT, straight from a checkout: no install, edits take effect immediately.
# For a server, use the published package instead:  pip install gini-teaching-center
#
# v1 has NO AI: there is no model URL to set and no model call to fail.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"

# core/src carries gini.domain (the gini-core distribution); teaching-center/src carries this
# package. Both are needed to run straight from a checkout with nothing installed.
export PYTHONPATH="$repo/core/src:$here/src${PYTHONPATH:+:$PYTHONPATH}"
export COURSE_ROOT="${COURSE_ROOT:-$repo/tc-data}"
export PORT="${PORT:-8080}"
export ADMIN_ID="${ADMIN_ID:-admin}"

mkdir -p "$COURSE_ROOT"

# v0 names, still in people's shell history and in the old README. Silently ignoring them means the
# password you typed did nothing and you cannot sign in, with no clue as to why.
if [ -n "${TEACHER_PASSWORD:-}" ] && [ -z "${ADMIN_PASSWORD:-}" ]; then
  echo "note: TEACHER_PASSWORD is now ADMIN_PASSWORD — using it as the admin password."
  export ADMIN_PASSWORD="$TEACHER_PASSWORD"
fi
if [ -n "${TEACHER_ID:-}" ] && [ "${ADMIN_ID}" = "admin" ]; then
  echo "note: TEACHER_ID is now ADMIN_ID — using '$TEACHER_ID'."
  export ADMIN_ID="$TEACHER_ID"
fi
if [ -n "${AI_MODEL:-}${AI_URL:-}" ]; then
  echo "note: AI_MODEL/AI_URL are ignored — v1 has no AI and makes no model calls."
fi

# The Teaching Center serves HTTPS and nothing else, loopback included — a password and a
# twelve-hour session token are not things to put on a wire in the clear, and "it is only
# localhost" stopped being an excuse the moment we established loopback can hold a certificate.
# So make one here rather than making TLS something you have to arrange before you can try it.
TLS_DIR="${TLS_DIR:-$COURSE_ROOT/tls}"
export TLS_CERT="${TLS_CERT:-$TLS_DIR/cert.pem}"
export TLS_KEY="${TLS_KEY:-$TLS_DIR/key.pem}"
if [ ! -s "$TLS_CERT" ] || [ ! -s "$TLS_KEY" ]; then
  mkdir -p "$TLS_DIR"
  # subjectAltName is required, not decoration: a bare CN is rejected by OpenSSL 3 and by macOS
  # whatever the trust, and it fails looking exactly like an untrusted certificate.
  printf '[req]\ndistinguished_name=dn\nx509_extensions=v3\nprompt=no\n[dn]\nCN=localhost\n[v3]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\n' \
    > "$TLS_DIR/openssl.cnf"
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -keyout "$TLS_KEY" -out "$TLS_CERT" -config "$TLS_DIR/openssl.cnf" 2>/dev/null
  chmod 600 "$TLS_KEY"
  echo "note: made a self-signed certificate for localhost in $TLS_DIR"
fi

echo "Teaching Center"
echo "  console   https://127.0.0.1:$PORT/"
echo "  students  https://127.0.0.1:$PORT/getcode?course=<course>&lab=<lab>"
echo "  data      $COURSE_ROOT"
echo "  tls       $TLS_CERT  (self-signed — see below)"
if [ -z "${ADMIN_PASSWORD:-}" ]; then
  echo "  sign-in   $ADMIN_ID + the one-time claim token printed below"
else
  echo "  sign-in   $ADMIN_ID / \$ADMIN_PASSWORD"
fi
echo

# -m, not a path: the modules import each other as a package now (`from . import accounts`), and
# running server.py directly would break every one of those.
# A self-signed certificate is encryption without trust, so say plainly how to get the trust —
# a browser warning is dismissable, but gBuilder verifies properly and will refuse.
echo "  Browsers will warn once (self-signed). To make gBuilder trust it, either:"
echo "      SSL_CERT_FILE=$TLS_CERT gbuilder"
echo "  or install a local CA once:  mkcert -install && mkcert localhost 127.0.0.1"
echo

exec python3 -m gini_teaching_center.cli --data "$COURSE_ROOT" --port "$PORT" --admin "$ADMIN_ID" \
     --tls-cert "$TLS_CERT" --tls-key "$TLS_KEY"
