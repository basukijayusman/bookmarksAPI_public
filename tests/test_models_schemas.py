"""Data-layer tests: models.py and schemas.py in isolation.

Runnable before auth.py, routes.py or main.py exist. Builds its own in-memory
SQLite, so it touches neither your real bookmarks.db nor the web app, and needs
no SECRET_KEY.

    pytest tests/test_models_schemas.py -v
"""
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Bookmark, Tag, User
from app.schemas import BookmarkIn, BookmarkOut


@pytest.fixture
def session():
    # StaticPool keeps one connection alive so create_all() and the Session share
    # the same in-memory database. A normal pool would hand the Session a second,
    # empty connection and every table lookup would fail.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# --- schemas.py: validation runs at construction time -----------------------

def test_tags_are_lowercased_stripped_and_deduped():
    body = BookmarkIn(url="https://example.com", title="Hi",
                      tags=["Python", "  python ", "API"])
    assert body.tags == ["api", "python"]  # sorted, unique, lowercased


def test_invalid_url_is_rejected():
    with pytest.raises(ValidationError):
        BookmarkIn(url="not-a-url", title="Hi")


def test_title_is_required():
    with pytest.raises(ValidationError):
        BookmarkIn(url="https://example.com", title="")


def test_description_defaults_to_none():
    body = BookmarkIn(url="https://example.com", title="Hi")
    assert body.description is None


# --- models.py: the ORM, against a real (in-memory) database ----------------

def test_user_persists_with_a_generated_id_and_timestamp(session):
    session.add(User(username="alice", email="alice@example.com", password_hash="x"))
    session.commit()

    user = session.scalar(select(User).where(User.username == "alice"))
    assert user.id is not None
    assert user.created_at is not None  # default=utcnow fired on insert


def test_bookmark_tags_are_many_to_many_and_ordered(session):
    session.add(User(username="alice", email="alice@example.com", password_hash="x"))
    session.commit()
    user = session.scalar(select(User).where(User.username == "alice"))

    session.add(Bookmark(url="https://x.com", title="Hi", user_id=user.id,
                         tags=[Tag(name="tutorial"), Tag(name="python")]))
    session.commit()

    bookmark = session.scalar(select(Bookmark))
    assert [t.name for t in bookmark.tags] == ["python", "tutorial"]  # order_by=Tag.name


def test_bookmark_out_serialises_tag_objects_to_names(session):
    session.add(User(username="alice", email="alice@example.com", password_hash="x"))
    session.commit()
    user = session.scalar(select(User).where(User.username == "alice"))
    session.add(Bookmark(url="https://x.com", title="Hi", user_id=user.id,
                         tags=[Tag(name="python")]))
    session.commit()

    out = BookmarkOut.model_validate(session.scalar(select(Bookmark)))
    assert out.tags == ["python"]  # the tag_names validator turned Tag rows into strings
