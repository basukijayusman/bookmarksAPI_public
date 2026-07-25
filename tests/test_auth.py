"""Tests for app/auth.py: password hashing, JWT, current_user, and the rate limiter.

app.auth validates SECRET_KEY at import time, so the env var is set before the import.
No web server involved: current_user is called directly with a fake credential and a
real in-memory session.
"""
import os

os.environ.setdefault("SECRET_KEY", "test-only-signing-key-not-for-any-real-deployment")

from datetime import datetime, timedelta, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import auth  # noqa: E402
from app.auth import (  # noqa: E402
    ALGORITHM,
    DUMMY_HASH,
    SECRET_KEY,
    current_user,
    hash_password,
    make_token,
    rate_limit,
    verify_password,
)
from app.db import Base  # noqa: E402
from app.errors import ApiError  # noqa: E402
from app.models import User  # noqa: E402


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def make_user(db, user_id_email="alice") -> User:
    user = User(username=user_id_email, email=f"{user_id_email}@example.com",
                password_hash="x")
    db.add(user)
    db.commit()
    return user


def cred(token: str):
    return SimpleNamespace(credentials=token)


def request_from(host: str):
    return SimpleNamespace(client=SimpleNamespace(host=host))


# --- password hashing -------------------------------------------------------

def test_hash_is_not_the_plaintext_and_is_salted():
    first = hash_password("hunter2")
    second = hash_password("hunter2")
    assert "hunter2" not in first
    assert first != second  # a fresh salt each time -> different hashes


def test_verify_accepts_the_right_password_and_rejects_the_wrong_one():
    stored = hash_password("hunter2")
    assert verify_password("hunter2", stored) is True
    assert verify_password("wrong", stored) is False


def test_dummy_hash_is_a_real_verifiable_bcrypt_hash():
    # It must behave like a genuine hash so the unknown-email login path still costs
    # one bcrypt round and the timing does not reveal that the account was missing.
    assert verify_password("no-account-matches-this", DUMMY_HASH) is True
    assert verify_password("something-else", DUMMY_HASH) is False


# --- JWT --------------------------------------------------------------------

def test_make_token_encodes_the_user_id_with_iat_and_exp():
    token = make_token(SimpleNamespace(id=42))
    payload = jwt.decode(token, SECRET_KEY, [ALGORITHM])
    assert payload["sub"] == "42"
    assert "iat" in payload and "exp" in payload


# --- current_user -----------------------------------------------------------

def test_current_user_returns_the_user_for_a_valid_token(db):
    user = make_user(db)
    assert current_user(cred(make_token(user)), db).id == user.id


def test_current_user_rejects_a_missing_token(db):
    with pytest.raises(ApiError) as raised:
        current_user(None, db)
    assert raised.value.status == 401
    assert raised.value.code == "UNAUTHENTICATED"


def test_current_user_rejects_a_malformed_token(db):
    with pytest.raises(ApiError) as raised:
        current_user(cred("not-a-jwt"), db)
    assert raised.value.code == "INVALID_TOKEN"


def test_current_user_rejects_a_token_signed_with_another_key(db):
    user = make_user(db)
    forged = jwt.encode({"sub": str(user.id)}, "a-different-key-32-characters-long!", ALGORITHM)
    with pytest.raises(ApiError) as raised:
        current_user(cred(forged), db)
    assert raised.value.code == "INVALID_TOKEN"


def test_current_user_rejects_an_expired_token(db):
    user = make_user(db)
    now = datetime.now(timezone.utc)
    expired = jwt.encode(
        {"sub": str(user.id), "iat": now - timedelta(hours=13), "exp": now - timedelta(hours=1)},
        SECRET_KEY, ALGORITHM,
    )
    with pytest.raises(ApiError) as raised:
        current_user(cred(expired), db)
    assert raised.value.code == "INVALID_TOKEN"


def test_current_user_rejects_a_valid_token_for_a_deleted_user(db):
    # Correctly signed, but no such row: the account was removed after the token issued.
    token = make_token(SimpleNamespace(id=9999))
    with pytest.raises(ApiError) as raised:
        current_user(cred(token), db)
    assert raised.value.code == "INVALID_TOKEN"


# --- rate limiter -----------------------------------------------------------

def test_rate_limit_allows_up_to_the_limit_then_blocks():
    auth._attempts.clear()
    req = request_from("10.0.0.1")
    for _ in range(auth.ATTEMPT_LIMIT):
        rate_limit(req)  # must not raise
    with pytest.raises(ApiError) as raised:
        rate_limit(req)
    assert raised.value.status == 429


def test_rate_limit_is_scoped_per_client():
    auth._attempts.clear()
    for _ in range(auth.ATTEMPT_LIMIT):
        rate_limit(request_from("1.1.1.1"))
    rate_limit(request_from("2.2.2.2"))  # a different client is unaffected


def test_rate_limit_evicts_stale_buckets(monkeypatch):
    auth._attempts.clear()
    clock = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])

    rate_limit(request_from("1.1.1.1"))
    clock[0] += auth.ATTEMPT_WINDOW + 1
    rate_limit(request_from("2.2.2.2"))

    # The expired bucket is gone, so distinct clients cannot grow the dict without bound.
    assert list(auth._attempts) == ["2.2.2.2"]
