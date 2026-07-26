"""Application assembly: env, error handlers, routers, health check."""

from dotenv import load_dotenv

# Must run before anything imports app.auth, which reads SECRET_KEY at import time
# and refuses to load without it. Hence the deferred imports below.
load_dotenv()

from fastapi import FastAPI  # noqa: E402

from . import errors  # noqa: E402
from .routes import auth_router, bookmarks_router  # noqa: E402
from .schemas import ErrorOut  # noqa: E402

app = FastAPI(
    title="Bookmarks API",
    description="Save, tag, search, and manage bookmarks.",
    version="0.1.0",
    # Applies to every route, which also registers ErrorOut in the OpenAPI
    # components so the documented error shape is the one errors.py emits.
    responses={"default": {"model": ErrorOut, "description": "Error"}},
)

errors.install(app)
app.include_router(auth_router)
app.include_router(bookmarks_router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """Liveness only. It does not touch the database, so a probe cannot be the thing
    that exhausts the connection pool."""
    return {"status": "ok"}
