"""Single source of truth for error responses: every failure leaves as {"error": {...}}."""

import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

log = logging.getLogger(__name__)

# Fallback codes for errors raised by the framework rather than by our own handlers.
_STATUS_CODES = {
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    429: "RATE_LIMITED",
}

# Pydantic reports the violated bound under different ctx keys per constraint.
_LIMIT_KEYS = ("max_length", "min_length", "ge", "le", "gt", "lt", "max_items")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_, exc: ApiError):
        return JSONResponse(body(exc.code, exc.message, exc.details), exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_, exc: RequestValidationError):
        err = exc.errors()[0]
        # loc is ("body", "title") / ("query", "page") — drop the location prefix.
        field = ".".join(str(p) for p in err["loc"][1:]) or str(err["loc"][-1])
        details = {"field": field, "constraint": err["type"]}
        ctx = err.get("ctx") or {}
        details["limit"] = next((ctx[k] for k in _LIMIT_KEYS if k in ctx), None)
        return JSONResponse(body("VALIDATION_ERROR", f"{field}: {err['msg']}", details), 422)

    @app.exception_handler(HTTPException)
    async def _http(_, exc: HTTPException):
        code = _STATUS_CODES.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(body(code, str(exc.detail)), exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_, exc: Exception):
        log.exception("unhandled error", exc_info=exc)
        return JSONResponse(body("INTERNAL_ERROR", "Something went wrong."), 500)
