from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator

MAX_TAGS = 20
MAX_TAG_LEN = 50


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: EmailStr


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    email: EmailStr
    # bcrypt silently truncates past 72 bytes, so reject rather than mislead.
    password: str = Field(min_length=8, max_length=72)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class AuthOut(BaseModel):
    user: UserOut
    token: str


class BookmarkIn(BaseModel):
    url: HttpUrl
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)

    @field_validator("tags")
    @classmethod
    def normalise(cls, raw: list[str]) -> list[str]:
        names = {t.strip().lower() for t in raw if t.strip()}
        if any(len(n) > MAX_TAG_LEN for n in names):
            raise ValueError(f"each tag must be {MAX_TAG_LEN} characters or fewer")
        return sorted(names)


class BookmarkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    title: str
    description: str | None
    tags: list[str]
    created_at: datetime
    updated_at: datetime

    @field_validator("tags", mode="before")
    @classmethod
    def tag_names(cls, tags: list) -> list[str]:
        return [t if isinstance(t, str) else t.name for t in tags]


class BookmarkPage(BaseModel):
    items: list[BookmarkOut]
    total: int
    page: int
    per_page: int


class TagCount(BaseModel):
    name: str
    count: int


class MonthCount(BaseModel):
    month: str
    count: int


class Stats(BaseModel):
    total_bookmarks: int
    total_tags: int
    top_tags: list[TagCount]
    bookmarks_per_month: list[MonthCount]


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict | None = None


class ErrorOut(BaseModel):
    error: ErrorDetail
