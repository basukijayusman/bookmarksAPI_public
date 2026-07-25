#!/usr/bin/env bash
# Stage everything and commit, but only after the suite passes. Enforces the
# conventional-commit prefix the project uses.
#   scripts/commit.sh "feat: add cursor pagination"
#   SKIP_TESTS=1 scripts/commit.sh "docs: fix a typo"   # skip the test gate
source "$(dirname "$0")/_env.sh"

MSG="${1:-}"
[ -n "$MSG" ] || { echo "usage: scripts/commit.sh \"<type>: <message>\"" >&2; exit 1; }

if ! printf '%s' "$MSG" | grep -qE '^(feat|fix|refactor|docs|test|chore|perf|ci): '; then
    echo "message must start with: feat|fix|refactor|docs|test|chore|perf|ci followed by ': '" >&2
    exit 1
fi

[ -n "$(git status --porcelain)" ] || { echo "nothing to commit" >&2; exit 1; }

if [ "${SKIP_TESTS:-}" != "1" ]; then
    echo "running tests before commit..."
    "$PY" -m pytest -q     # set -e aborts the commit if this fails
fi

git add -A
git commit -m "$MSG"
