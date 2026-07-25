#!/usr/bin/env bash
# Run tests. With no args, runs the whole suite. The first arg may be a layer name,
# which expands to that layer's test file; extra args after it still pass through.
# Anything that is not a known layer is handed straight to pytest.
#
#   scripts/test.sh                 # whole suite
#   scripts/test.sh db              # tests/test_db.py
#   scripts/test.sh models          # tests/test_models_schemas.py
#   scripts/test.sh auth            # tests/test_auth.py
#   scripts/test.sh migrations      # tests/test_migrations.py
#   scripts/test.sh api             # tests/test_api.py  (once it exists)
#   scripts/test.sh auth -v         # a layer plus raw pytest flags
#   scripts/test.sh -k stats        # no layer -> args pass straight to pytest
source "$(dirname "$0")/_env.sh"

case "${1:-}" in
    db)          FILE=tests/test_db.py;             shift ;;
    models)      FILE=tests/test_models_schemas.py; shift ;;
    auth)        FILE=tests/test_auth.py;           shift ;;
    migrations)  FILE=tests/test_migrations.py;     shift ;;
    api)         FILE=tests/test_api.py;            shift ;;
    *)           FILE="" ;;                         # no layer: pass everything through
esac

exec "$PY" -m pytest ${FILE:+"$FILE"} "$@"
