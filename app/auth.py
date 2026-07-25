import os
import time
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .errors import ApiError
from .models import User

# RFC 7518 §3.2: an HMAC key should be at least as long as the hash it feeds.
MIN_SECRET_BYTES = 32

SECRET_KEY = os.getenv("SECRET_KEY", "")
if len(SECRET_KEY) < MIN_SECRET_BYTES:
    # Deliberately fatal. Defaulting to a generated key would "work" in dev and then
    # silently break in production: each uvicorn worker would sign with a different
    # key, so a token would validate or fail depending on which worker answered.
    raise RuntimeError(
        f"SECRET_KEY must be set and at least {MIN_SECRET_BYTES} characters. "
        "Copy .env.example to .env, or export it."
    )

ALGORITHM = "HS256"
TOKEN_TTL = timedelta(hours=12)

bearer = HTTPBearer(auto_error=False, description="JWT issued by /api/auth/login")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


# Compared against when an email is unknown, so login costs one bcrypt round either way.
# Same work factor as a real hash, otherwise the timing difference survives.
DUMMY_HASH = hash_password("no-account-matches-this")


def make_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(user.id), "iat": now, "exp": now + TOKEN_TTL}, SECRET_KEY, ALGORITHM
    )


def current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if cred is None:
        raise ApiError(401, "UNAUTHENTICATED", "Missing bearer token.")
    try:
        subject = jwt.decode(cred.credentials, SECRET_KEY, [ALGORITHM])["sub"]
    except jwt.PyJWTError:
        raise ApiError(401, "INVALID_TOKEN", "Token is invalid or expired.")
    user = db.scalar(select(User).where(User.id == int(subject)))
    if user is None:
        raise ApiError(401, "INVALID_TOKEN", "Token subject no longer exists.")
    return user


ATTEMPT_LIMIT = 20
ATTEMPT_WINDOW = 60.0
_attempts: dict[str, list[float]] = {}


def rate_limit(request: Request) -> None:
    """Throttle credential endpoints against brute force.

    Keyed on the peer socket, not X-Forwarded-For: behind no proxy that header is
    attacker-controlled, so honouring it would let anyone reset their own limit by
    sending a fresh value. Run uvicorn with --proxy-headers and a trusted-host list
    if this ever sits behind a load balancer.

    The counter lives in this process, so it resets on restart and is per-worker.
    Redis would be the move if this ever runs multi-process.
    """
    now = time.monotonic()
    # Evict stale buckets, or a stream of distinct client IPs grows this without bound.
    for stale in [k for k, hits in _attempts.items() if now - hits[-1] >= ATTEMPT_WINDOW]:
        del _attempts[stale]

    key = request.client.host if request.client else "unknown"
    recent = [t for t in _attempts.get(key, []) if now - t < ATTEMPT_WINDOW]
    if len(recent) >= ATTEMPT_LIMIT:
        raise ApiError(429, "RATE_LIMITED", "Too many attempts. Try again in a minute.")
    _attempts[key] = [*recent, now]
