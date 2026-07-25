#!/usr/bin/env bash
# Sourced by the other scripts. Moves to the repo root and picks the venv's python
# so the scripts work whether or not the venv is activated, on Unix or Windows.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -x .venv/bin/python ]; then
    PY=.venv/bin/python                 # Linux / macOS
elif [ -x .venv/Scripts/python.exe ]; then
    PY=.venv/Scripts/python.exe         # Windows
else
    echo "No .venv found. Run scripts/setup.sh first." >&2
    exit 1
fi
