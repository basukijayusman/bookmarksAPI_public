"""HTTP layer: registration, login, and bookmark CRUD.

Every bookmark query filters on the caller's user_id in the WHERE clause. Ownership
is never checked by loading a row and comparing afterwards.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
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
    AuthOut,
    BookmarkIn,
    BookmarkOut,
    BookmarkPage,
    LoginIn,
    RegisterIn,
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


@bookmarks_router.get("", response_model=BookmarkPage)
def list_bookmarks(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> BookmarkPage:
    items = db.scalars(
        select(Bookmark)
        .where(Bookmark.user_id == user.id)
        .order_by(Bookmark.created_at.desc(), Bookmark.id.desc())
    ).all()
    # ponytail: one page of everything until Stage 6 adds the query params. The
    # envelope is already the final shape, so that stage only fills it in.
    return BookmarkPage(items=items, total=len(items), page=1, per_page=len(items))


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
