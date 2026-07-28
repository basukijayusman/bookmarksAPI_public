"""API test harness: a throwaway database per test, wired into the real app.

SECRET_KEY is set before app.main is imported, because app.auth validates it at
import time and raises without it.
"""
import os

os.environ.setdefault("SECRET_KEY", "test-only-signing-key-not-for-any-real-deployment")

import jsonschema  # noqa: E402
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


@pytest.fixture
def conforms(client):
    """Assert a real response body matches the shape the served spec promises for it.

    The spec-shape tests in test_api.py check the OpenAPI document is right; this
    checks the live responses match it, so neither side can drift from the other unseen.
    """
    spec = client.get("/openapi.json").json()

    def check(response, path: str, method: str, status) -> None:
        entry = spec["paths"][path][method.lower()]["responses"][str(status)]
        content = entry.get("content")
        if content is None:
            # e.g. 204: the spec advertises no body, so the response must not carry one.
            assert not response.content
            return
        # Carry the whole components block along as the root document so the $ref and
        # every nested $ref resolve by plain JSON-pointer walk, no custom resolver.
        schema = {**content["application/json"]["schema"], "components": spec["components"]}
        jsonschema.validate(response.json(), schema)

    return check
