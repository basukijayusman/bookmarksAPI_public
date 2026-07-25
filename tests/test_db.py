"""Tests for app/db.py: the engine config, Base, session factory, and get_db dependency.

Standalone: imports only app.db, so it runs before the API layer exists.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase, Session

from app.db import DATABASE_URL, Base, SessionLocal, get_db


def test_database_url_defaults_to_a_local_sqlite_file():
    assert DATABASE_URL.startswith("sqlite")


def test_base_is_a_declarative_base_with_metadata():
    assert issubclass(Base, DeclarativeBase)
    assert hasattr(Base, "metadata")


def test_session_factory_produces_a_usable_session():
    with SessionLocal() as db:
        assert isinstance(db, Session)
        assert db.execute(text("SELECT 1")).scalar() == 1


def test_get_db_yields_a_session_then_runs_its_finally():
    gen = get_db()
    db = next(gen)
    assert isinstance(db, Session)
    assert db.execute(text("SELECT 1")).scalar() == 1
    # Exhausting the generator runs the finally block, which calls db.close().
    with pytest.raises(StopIteration):
        next(gen)


def test_get_db_hands_out_a_distinct_session_each_call():
    first = get_db()
    second = get_db()
    a, b = next(first), next(second)
    try:
        assert a is not b
    finally:
        first.close()
        second.close()
