import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TABLES = {"users", "tags", "bookmarks", "bookmark_tags"}


def alembic_binary() -> str | None:
    """Locate the alembic console script.

    PATH only has it when the venv is activated, and a test that quietly skips is
    barely a test — so fall back to the directory holding the running interpreter,
    which is where pip installed it.
    """
    on_path = shutil.which("alembic")
    if on_path:
        return on_path
    for name in ("alembic.exe", "alembic"):
        candidate = Path(sys.executable).parent / name
        if candidate.exists():
            return str(candidate)
    return None


@pytest.mark.skipif(alembic_binary() is None, reason="alembic console script not installed")
def test_migrations_build_the_schema_from_empty(tmp_path):
    """Runs the `alembic` binary, not `python -m alembic`.

    Only the module form prepends the working directory to sys.path, so invoking the
    console script is what catches env.py failing to import the app package — which is
    exactly the command the README tells a reviewer to run.
    """
    db = tmp_path / "migration-check.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db.as_posix()}"}

    result = subprocess.run([alembic_binary(), "upgrade", "head"], cwd=PROJECT_ROOT, env=env,
                            capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= tables, f"missing: {EXPECTED_TABLES - tables}"
