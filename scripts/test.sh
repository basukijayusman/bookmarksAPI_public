#!/usr/bin/env bash
# Run the test suite. Extra args pass through:
#   scripts/test.sh -v
#   scripts/test.sh -k stats
#   scripts/test.sh tests/test_models_schemas.py
source "$(dirname "$0")/_env.sh"
exec "$PY" -m pytest "$@"
