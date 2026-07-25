#!/usr/bin/env bash
# Run Alembic. Defaults to `upgrade head`; any args pass straight through:
#   scripts/migrate.sh                              # upgrade head
#   scripts/migrate.sh downgrade -1                 # roll back one revision
#   scripts/migrate.sh revision --autogenerate -m "add column"
source "$(dirname "$0")/_env.sh"
if [ $# -eq 0 ]; then set -- upgrade head; fi
exec "$PY" -m alembic "$@"
