"""Auth JSON API (/api/v1). Owner: agent A · Auth (docs/plan.md)."""

from fastapi import APIRouter, Request, Response, status

from app.core.config import get_settings
from app.core.db import DbSession
from app.core.deps import CurrentUser
from app.core.security import SESSION_COOKIE, SESSION_TTL
from app.schemas.auth import LoginIn
from app.schemas.users import UserOut
from app.services import auth as auth_service

router = APIRouter(tags=["auth"])


def _secure_cookie() -> bool:
    return get_settings().env == "production"


def set_session_cookie(response: Response, token: str) -> None:
    """Shared with the web login. HttpOnly + SameSite=Lax; Secure only in production."""
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
        httponly=True,
        samesite="lax",
        secure=_secure_cookie(),
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=_secure_cookie()
    )


@router.post("/auth/login", response_model=UserOut)
def login(body: LoginIn, response: Response, db: DbSession) -> UserOut:
    result = auth_service.login(db, email=body.email, password=body.password)
    set_session_cookie(response, result.token)
    return UserOut.model_validate(result.user)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, db: DbSession) -> Response:
    auth_service.logout(db, request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response)
    return response


@router.get("/auth/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
