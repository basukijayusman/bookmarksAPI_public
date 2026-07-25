#!/usr/bin/env bash
# One-command bootstrap from a clean clone: venv, deps, .env, database, sample data.
set -euo pipefail
cd "$(dirname "$0")/.."

python -m venv .venv
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=.venv/Scripts/python.exe; fi

"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r requirements.txt

# The app refuses to start without SECRET_KEY, so generate one if there's no .env yet.
if [ ! -f .env ]; then
    KEY=$("$PY" -c "import secrets; print(secrets.token_urlsafe(32))")
    printf 'SECRET_KEY=%s\nDATABASE_URL=sqlite:///./bookmarks.db\n' "$KEY" > .env
    echo "wrote .env with a generated SECRET_KEY"
fi

"$PY" -m alembic upgrade head
"$PY" seed.py
echo "Setup complete. Start the app with scripts/run.sh"
