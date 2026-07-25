#!/usr/bin/env bash
# Push the current branch to origin.
#
# Sets credential.useHttpPath so a global github.com login for a *different*
# account isn't reused for this repo by mistake. The first push then prompts for
# credentials that can write to origin.
set -euo pipefail
cd "$(dirname "$0")/.."   # git scripts don't need the venv, only the repo root

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
REMOTE="$(git remote get-url origin 2>/dev/null || echo '<no origin set>')"

git config credential.useHttpPath true
echo "pushing $BRANCH -> $REMOTE"
exec git push -u origin "$BRANCH"
