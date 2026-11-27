"""Domain exceptions raised by services, and their translation to HTTP responses."""

from typing import ClassVar

from fastapi import FastAPI, Request
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


async def domain_error_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, DomainError):  # pragma: no cover - registered for DomainError only
        raise exc
    if isinstance(exc, NotAuthenticated) and not _is_api(request):
        if request.headers.get("HX-Request"):
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=303)
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.message, "code": exc.code}
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_error_handler)
