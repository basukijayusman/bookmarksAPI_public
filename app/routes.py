"""HTTP layer: registration, login, and bookmark CRUD.

Every bookmark query filters on the caller's user_id in the WHERE clause. Ownership
is never checked by loading a row and comparing afterwards.
"""

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import (
    DUMMY_HASH,
    current_user,
    hash_password,
    make_token,
    rate_limit,
    verify_password,
)
from .db import get_db
from .errors import ApiError
from .models import Bookmark, Tag, User
from .schemas import (
    MAX_TAG_LEN,
    AuthOut,
    BookmarkIn,
    BookmarkOut,
    BookmarkPage,
    LoginIn,
    MonthCount,
    RegisterIn,
    Stats,
    TagCount,
    UserOut,
)

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
bookmarks_router = APIRouter(prefix="/api/bookmarks", tags=["bookmarks"])


# --- auth -------------------------------------------------------------------


@auth_router.post(
    "/register",
    response_model=AuthOut,
    status_code=201,
    dependencies=[Depends(rate_limit)],
)
def register(payload: RegisterIn, db: Session = Depends(get_db)) -> AuthOut:
    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # The unique indexes decide, not a pre-check SELECT: two concurrent signups
        # with the same email both pass a pre-check and only one can win here.
        db.rollback()
        raise ApiError(409, "ALREADY_EXISTS", "That username or email is already taken.")
    db.refresh(user)
    return AuthOut(user=UserOut.model_validate(user), token=make_token(user))


@auth_router.post("/login", response_model=AuthOut, dependencies=[Depends(rate_limit)])
def login(payload: LoginIn, db: Session = Depends(get_db)) -> AuthOut:
    user = db.scalar(select(User).where(User.email == payload.email))
    # Hash against DUMMY_HASH when the email is unknown so both failures cost one
    # bcrypt round; otherwise response time alone reveals which accounts exist.
    ok = verify_password(payload.password, user.password_hash if user else DUMMY_HASH)
    if user is None or not ok:
        # One message for both cases, for the same reason.
        raise ApiError(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
    return AuthOut(user=UserOut.model_validate(user), token=make_token(user))


# --- tags -------------------------------------------------------------------


def _get_or_create_tags(db: Session, names: list[str]) -> list[Tag]:
    found = {t.name: t for t in db.scalars(select(Tag).where(Tag.name.in_(names)))}
    fresh = [Tag(name=n) for n in names if n not in found]
    db.add_all(fresh)
    db.flush()  # surface a unique violation here, while resolve_tags can still recover
    return [*found.values(), *fresh]


def resolve_tags(db: Session, names: list[str]) -> list[Tag]:
    """Get-or-create the global tag rows for `names`.

    The read alone is a race: two requests can both miss the same new tag and both
    INSERT it. The unique index on tags.name is what actually decides; the loser
    rolls back and re-reads, which now finds the winner's row.

    Call this *before* adding or mutating the bookmark: the retry rolls the whole
    session back, so nothing else may be pending on it.
    """
    if not names:
        return []
    try:
        return _get_or_create_tags(db, names)
    except IntegrityError:
        db.rollback()
        return _get_or_create_tags(db, names)


# --- bookmarks --------------------------------------------------------------


def _owned(db: Session, user: User, bookmark_id: int) -> Bookmark:
    bookmark = db.scalar(
        select(Bookmark).where(Bookmark.id == bookmark_id, Bookmark.user_id == user.id)
    )
    if bookmark is None:
        # 404 rather than 403, even when the row exists but belongs to someone else:
        # a 403 confirms the id is real and turns the endpoint into an id oracle.
        raise ApiError(404, "NOT_FOUND", "No such bookmark.")
    return bookmark


@bookmarks_router.post("", response_model=BookmarkOut, status_code=201)
def create_bookmark(
    payload: BookmarkIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Bookmark:
    tags = resolve_tags(db, payload.tags)  # before db.add: a retry rolls the session back
    bookmark = Bookmark(
        url=str(payload.url),
        title=payload.title,
        description=payload.description,
        user_id=user.id,
        tags=tags,
    )
    db.add(bookmark)
    db.commit()
    db.refresh(bookmark)
    return bookmark


PER_PAGE_DEFAULT = 20
PER_PAGE_MAX = 100
TOP_TAGS = 10


def _like(term: str) -> str:
    """A contains-pattern with the wildcards in `term` neutered.

    LIKE reads % and _ as wildcards, so an unescaped search for "100%" also matches
    "1000". Not an injection risk (the term is bound), just the wrong answer.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@bookmarks_router.get("", response_model=BookmarkPage)
def list_bookmarks(
    tag: str | None = Query(None, max_length=MAX_TAG_LEN, description="Exact tag name."),
    q: str | None = Query(None, max_length=200, description="Substring of title or description."),
    date_from: date | None = Query(None, alias="from", description="Created on or after (UTC)."),
    date_to: date | None = Query(None, alias="to", description="Created on or before (UTC)."),
    page: int = Query(1, ge=1),
    per_page: int = Query(PER_PAGE_DEFAULT, ge=1, le=PER_PAGE_MAX),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> BookmarkPage:
    where = [Bookmark.user_id == user.id]
    if tag:
        # Normalised the same way BookmarkIn normalises on write, or "Python" would
        # never match the "python" row it created.
        where.append(Bookmark.tags.any(Tag.name == tag.strip().lower()))
    if q:
        # ilike, not like: SQLite's LIKE ignores ASCII case but Postgres' does not.
        # A NULL description simply fails its half of the OR.
        where.append(
            or_(
                Bookmark.title.ilike(_like(q), escape="\\"),
                Bookmark.description.ilike(_like(q), escape="\\"),
            )
        )
    if date_from:
        where.append(Bookmark.created_at >= datetime.combine(date_from, time.min))
    if date_to:
        # Strictly before the next midnight, so the whole of `to` is included:
        # created_at carries a time, so `<= to` would drop all but 00:00:00.
        where.append(
            Bookmark.created_at < datetime.combine(date_to + timedelta(days=1), time.min)
        )

    # A separate COUNT, because `total` means the size of the whole result set and
    # len(items) can only ever be the size of one page.
    total = db.scalar(select(func.count()).select_from(Bookmark).where(*where))
    items = db.scalars(
        select(Bookmark)
        .where(*where)
        .order_by(Bookmark.created_at.desc(), Bookmark.id.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    ).all()
    return BookmarkPage(items=items, total=total or 0, page=page, per_page=per_page)


# --- stats ------------------------------------------------------------------
#
# Raw SQL on purpose: these are aggregates across the association table, and the ORM
# would only add a translation layer over SQL that is already the clearest statement
# of the question. :uid is a bound parameter, never interpolated.
#
# strftime() is SQLite's; Postgres wants to_char(created_at, 'YYYY-MM'). The literal
# '%Y-%m' is safe inside text() here because sqlite3 uses qmark paramstyle and does
# no %-interpolation of its own.

_TOTALS_SQL = text("""
    SELECT (SELECT COUNT(*) FROM bookmarks WHERE user_id = :uid) AS total_bookmarks,
           -- DISTINCT tag_id over the caller's bookmarks, never COUNT(*) FROM tags:
           -- tags are global, so that would count every user's tags.
           (SELECT COUNT(DISTINCT bt.tag_id)
              FROM bookmark_tags bt
              JOIN bookmarks b ON b.id = bt.bookmark_id
             WHERE b.user_id = :uid) AS total_tags
""")

_TOP_TAGS_SQL = text("""
    SELECT t.name, COUNT(*) AS uses
      FROM tags t
      JOIN bookmark_tags bt ON bt.tag_id = t.id
      JOIN bookmarks b ON b.id = bt.bookmark_id
     WHERE b.user_id = :uid
     GROUP BY t.id, t.name
     ORDER BY uses DESC, t.name ASC
     LIMIT :limit
""")

_PER_MONTH_SQL = text("""
    SELECT strftime('%Y-%m', created_at) AS month, COUNT(*) AS created
      FROM bookmarks
     WHERE user_id = :uid
     GROUP BY month
     ORDER BY month ASC
""")


# Declared before /{bookmark_id}: routes match in definition order, so the other way
# round "stats" would be parsed as a bookmark id and fail validation.
@bookmarks_router.get("/stats", response_model=Stats)
def bookmark_stats(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Stats:
    params = {"uid": user.id}
    total_bookmarks, total_tags = db.execute(_TOTALS_SQL, params).one()
    # Unpacked positionally rather than by attribute: a Row is tuple-backed, so
    # row.count would resolve to tuple.count, the method.
    top = db.execute(_TOP_TAGS_SQL, {**params, "limit": TOP_TAGS}).all()
    months = db.execute(_PER_MONTH_SQL, params).all()
    return Stats(
        total_bookmarks=total_bookmarks,
        total_tags=total_tags,
        top_tags=[TagCount(name=name, count=uses) for name, uses in top],
        bookmarks_per_month=[MonthCount(month=month, count=n) for month, n in months],
    )


@bookmarks_router.get("/{bookmark_id}", response_model=BookmarkOut)
def get_bookmark(
    bookmark_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Bookmark:
    return _owned(db, user, bookmark_id)


@bookmarks_router.put("/{bookmark_id}", response_model=BookmarkOut)
def update_bookmark(
    bookmark_id: int,
    payload: BookmarkIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Bookmark:
    bookmark = _owned(db, user, bookmark_id)
    tags = resolve_tags(db, payload.tags)  # before mutating, for the same rollback reason
    bookmark.url = str(payload.url)
    bookmark.title = payload.title
    bookmark.description = payload.description
    # PUT replaces the tag set wholesale. No PATCH until partial updates are asked for.
    bookmark.tags = tags
    db.commit()
    db.refresh(bookmark)
    return bookmark


@bookmarks_router.delete("/{bookmark_id}", status_code=204)
def delete_bookmark(
    bookmark_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> None:
    # The bookmark_tags rows go with it: SQLAlchemy clears the secondary table on
    # delete, so this does not depend on SQLite having foreign keys enabled.
    db.delete(_owned(db, user, bookmark_id))
    db.commit()
