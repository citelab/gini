#!/usr/bin/env bash
# Run the Teaching Center for a test drive.
#
#   ./teaching-center/run.sh                     # localhost:8080, course data in ./tc-data
#   PORT=9000 COURSE=comp535 ./teaching-center/run.sh
#
# Why this exists: `gini` is a hard dependency of the Teaching Center (activity authoring needs
# gini.domain.aop* and gini.agent.aop_selector), but nothing installs it — so a bare `python3
# server.py` dies on `import gini.domain` with a traceback that points at the wrong problem.
# Packaging is the real fix; until then this sets the path correctly every time.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"

export PYTHONPATH="$repo/frontend-ng/src:$here${PYTHONPATH:+:$PYTHONPATH}"
export COURSE_ROOT="${COURSE_ROOT:-$repo/tc-data}"
export COURSE="${COURSE:-comp535}"
export PORT="${PORT:-8080}"
export TEACHER_ID="${TEACHER_ID:-teacher}"

# The AI lives here (ProfAI, and the activity drafter). Point these at whatever Ollama you run;
# without a reachable model the console still works and drafting says so plainly, rather than
# guessing at a plan with a word-matcher.
export AI_URL="${AI_URL:-http://127.0.0.1:11434}"
export AI_MODEL="${AI_MODEL:-llama3.1:8b}"

mkdir -p "$COURSE_ROOT"

echo "Teaching Center"
echo "  console   http://127.0.0.1:$PORT/teacher"
echo "  students  http://127.0.0.1:$PORT/getcode?course=$COURSE&lab=lab1"
echo "  data      $COURSE_ROOT"
echo "  model     $AI_MODEL at $AI_URL"
if [ -z "${TEACHER_PASSWORD:-}" ]; then
  echo "  sign-in   a one-time setup token is printed below (or set TEACHER_PASSWORD)"
else
  echo "  sign-in   $TEACHER_ID / \$TEACHER_PASSWORD"
fi
echo

exec python3 "$here/server.py"
