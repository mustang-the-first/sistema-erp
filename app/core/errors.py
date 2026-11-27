"""Domain exceptions raised by services, and their translation to HTTP responses."""

from collections.abc import Sequence
from typing import Any, ClassVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response


class DomainError(Exception):
    status_code: ClassVar[int] = 400
    default_code: ClassVar[str] = "domain_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code


class NotAuthenticated(DomainError):
    status_code = 401
    default_code = "not_authenticated"


class Forbidden(DomainError):
    status_code = 403
    default_code = "forbidden"


class NotFound(DomainError):
    status_code = 404
    default_code = "not_found"


class BusinessRuleViolation(DomainError):
    status_code = 422
    default_code = "business_rule_violation"


def _is_api(request: Request) -> bool:
    return request.url.path.startswith("/api/")


# Web errors -------------------------------------------------------------------------------
# HTMX does not swap 4xx responses, so a rejected web form would show nothing. Web requests
# made by HTMX (or any web write: without HTMX's CSRF header a logged-in write is already a 403)
# get their 404/422 rendered into #flash with status 200 instead. 401 redirects to /login and
# 403 keeps its status: a forbidden action is never disguised as a success.
FLASH_TARGET = "#flash"
FLASH_STATUSES = frozenset({404, 422})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def htmx_flash_error(request: Request, detail: str) -> Response:
    """The shared web error response: ``partials/error.html`` swapped into ``#flash``."""
    from app.core.templating import templates  # local import: templating imports the models

    return templates.TemplateResponse(
        request,
        "partials/error.html",
        {"detail": detail},
        headers={"HX-Retarget": FLASH_TARGET, "HX-Reswap": "innerHTML"},
    )


def _wants_flash(request: Request, status_code: int) -> bool:
    return (
        not _is_api(request)
        and status_code in FLASH_STATUSES
        and (bool(request.headers.get("HX-Request")) or request.method not in _SAFE_METHODS)
    )


def error_response(request: Request, *, status_code: int, detail: str, code: str) -> Response:
    """One error format everywhere: ``{"detail", "code"}`` JSON, or the #flash partial."""
    if _wants_flash(request, status_code):
        return htmx_flash_error(request, detail)
    return JSONResponse(status_code=status_code, content={"detail": detail, "code": code})


async def domain_error_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, DomainError):  # pragma: no cover - registered for DomainError only
        raise exc
    if isinstance(exc, NotAuthenticated) and not _is_api(request):
        if request.headers.get("HX-Request"):
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=303)
    return error_response(request, status_code=exc.status_code, detail=exc.message, code=exc.code)


VALIDATION_ERROR_CODE = "validation_error"
_LOCATION_PREFIXES = frozenset({"body", "query", "path", "header", "cookie", "form"})


def format_validation_errors(errors: Sequence[Any]) -> str:
    """``quantity: Input should be greater than 0; date: Input should be a valid date``"""
    parts = []
    for error in errors:
        loc = [str(p) for p in error.get("loc", ()) if p not in _LOCATION_PREFIXES]
        field = ".".join(loc)
        parts.append(f"{field}: {error.get('msg', 'invalid value')}" if field else error["msg"])
    return "; ".join(parts) or "Invalid request"


async def validation_error_handler(request: Request, exc: Exception) -> Response:
    """Schema validation errors use the same ``{"detail", "code"}`` format as business rules."""
    if not isinstance(exc, RequestValidationError):  # pragma: no cover - registered for it only
        raise exc
    return error_response(
        request,
        status_code=422,
        detail=format_validation_errors(exc.errors()),
        code=VALIDATION_ERROR_CODE,
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
