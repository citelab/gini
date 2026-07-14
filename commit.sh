#!/usr/bin/env bash
#
# Commit everything and push, in one shot.
#
#   ./commit.sh "Missions: progressive ladder + probe fix"
#   ./commit.sh Missions: progressive ladder      # quotes optional
#
set -euo pipefail

if [ $# -eq 0 ]; then
  echo "usage: $(basename "$0") \"commit message\"" >&2
  exit 1
fi
MSG="$*"                     # join all args, so quoting is optional

# always operate on the repo this script lives in, wherever it's invoked from
cd "$(dirname "$0")"
cd "$(git rev-parse --show-toplevel)"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"

# a stale lock (left by a crashed/killed git) blocks every command — clear it if no git is running
if [ -f .git/index.lock ] && ! pgrep -x git >/dev/null 2>&1; then
  echo "• clearing stale .git/index.lock"
  rm -f .git/index.lock
fi

git add -A                   # everything: new, modified, deleted

if git diff --cached --quiet; then
  echo "nothing to commit — working tree is clean"
  exit 0
fi

echo "── staging on branch '$BRANCH' ──────────────────────────────"
git diff --cached --stat
echo "─────────────────────────────────────────────────────────────"

git commit -m "$MSG"

# push; set the upstream automatically the first time this branch is pushed
if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
  git push
else
  echo "• no upstream yet — setting origin/$BRANCH"
  git push -u origin "$BRANCH"
fi

echo "✅ committed and pushed to '$BRANCH'"
