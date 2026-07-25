from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


bookmark_tags = Table(
    "bookmark_tags",
    Base.metadata,
    Column("bookmark_id", ForeignKey("bookmarks.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Tags are global and deduplicated by name, so per-user counts must always
    # join through bookmarks rather than counting this table (see /stats).
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True)


class Bookmark(Base):
    __tablename__ = "bookmarks"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    # lazy="selectin" turns the list endpoint's N+1 tag lookup into one extra query.
    # TODO: selectin still fires that tag query even when the caller never reads
    #   .tags (DELETE is the clear case). Override per query for paths that do not
    #   serialize BookmarkOut (which always needs tags):
    #       select(Bookmark).options(raiseload(Bookmark.tags))  # error if .tags is touched
    #       select(Bookmark).options(lazyload(Bookmark.tags))   # load only on access
    tags: Mapped[list[Tag]] = relationship(
        secondary=bookmark_tags, lazy="selectin", order_by=Tag.name
    )

    # Covers the default listing: filter by owner, order by recency.
    __table_args__ = (Index("ix_bookmarks_user_created", "user_id", "created_at"),)
