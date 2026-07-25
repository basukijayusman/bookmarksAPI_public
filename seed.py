"""Populate the database with a demo user and sample bookmarks.

    python seed.py          # after: alembic upgrade head

Re-running is safe: it deletes the demo user's data first.
"""
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.auth import hash_password
from app.db import SessionLocal
from app.models import Bookmark, Tag, User

EMAIL, PASSWORD = "demo@example.com", "correct-horse"
TAGS = ["python", "sql", "backend", "tutorial", "fastapi", "testing", "devops"]
TITLES = [
    ("Async SQLAlchemy in practice", "https://example.com/async-sqlalchemy"),
    ("Indexing for query performance", "https://example.com/indexing"),
    ("A tour of pytest fixtures", "https://example.com/pytest-fixtures"),
    ("Designing consistent error payloads", "https://example.com/errors"),
    ("JWT expiry and rotation", "https://example.com/jwt"),
    ("OpenAPI-driven contract tests", "https://example.com/openapi"),
    ("Pagination beyond OFFSET", "https://example.com/pagination"),
    ("Migrations without downtime", "https://example.com/migrations"),
]


def main() -> None:
    random.seed(0)  # reproducible sample data
    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == EMAIL))
        if user:
            for b in db.scalars(select(Bookmark).where(Bookmark.user_id == user.id)):
                db.delete(b)
        else:
            user = User(username="demo", email=EMAIL, password_hash=hash_password(PASSWORD))
            db.add(user)
        db.flush()

        tags = {}
        for name in TAGS:
            tags[name] = db.scalar(select(Tag).where(Tag.name == name)) or Tag(name=name)
            db.add(tags[name])

        for i, (title, url) in enumerate(TITLES):
            created = now - timedelta(days=random.randint(0, 150))
            db.add(Bookmark(
                url=url, title=title, description=f"Sample bookmark #{i + 1}.",
                user_id=user.id, created_at=created, updated_at=created,
                tags=[tags[n] for n in random.sample(TAGS, k=random.randint(1, 3))],
            ))
        db.commit()

    print(f"Seeded {len(TITLES)} bookmarks. Log in as {EMAIL} / {PASSWORD}")


if __name__ == "__main__":
    main()
