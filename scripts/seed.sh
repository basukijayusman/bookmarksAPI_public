#!/usr/bin/env bash
# Populate the database with a demo user and sample bookmarks. Safe to re-run.
source "$(dirname "$0")/_env.sh"
exec "$PY" seed.py
