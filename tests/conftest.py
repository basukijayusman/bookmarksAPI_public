"""API test harness: a throwaway database per test, wired into the real app.

SECRET_KEY is set before app.main is imported, because app.auth validates it at
import time and raises without it.
"""
import os

os.environ.setdefault("SECRET_KEY", "test-only-signing-key-not-for-any-real-deployment")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import auth  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def client():
    """A TestClient talking to a fresh in-memory database.

    StaticPool hands every connection the *same* in-memory database. The default pool
    gives each new connection its own, so the schema created here would vanish and the
    request would find no tables.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # The limiter is process-global and every request here comes from the same
    # host ("testclient"), so without this the later tests in a run get 429s.
    auth._attempts.clear()

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    engine.dispose()
