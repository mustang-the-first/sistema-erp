"""Auth HTML views (Jinja2 + HTMX). Owner: agent A · Auth (docs/plan.md)."""

from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.db import DbSession
from app.core.deps import OptionalUser
from app.core.errors import NotAuthenticated
from app.core.security import SESSION_COOKIE
from app.core.templating import templates
from app.routers.api.auth import clear_session_cookie, set_session_cookie
from app.services import auth as auth_service

router = APIRouter(tags=["auth"])

LOGIN_TEMPLATE = "auth/login.html"


@router.get("/login", response_class=HTMLResponse, response_model=None)
def login_page(request: Request, user: OptionalUser) -> Response:
    if user is not None:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response = templates.TemplateResponse(request, LOGIN_TEMPLATE, {"email": "", "error": False})
    if SESSION_COOKIE in request.cookies:
        # Expired or revoked session: drop the cookie so the anonymous login form post is not
        # rejected by the CSRF check (which applies whenever a session cookie is present).
        clear_session_cookie(response)
    return response


@router.post("/login", response_class=HTMLResponse, response_model=None)
def login_submit(
    request: Request,
    db: DbSession,
    email: Annotated[str, Form(max_length=255)] = "",
    password: Annotated[str, Form(max_length=128)] = "",
) -> Response:
    try:
        result = auth_service.login(db, email=email, password=password)
    except NotAuthenticated:
        return templates.TemplateResponse(
            request,
            LOGIN_TEMPLATE,
            {"email": email, "error": True},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, result.token)
    return response


@router.post("/logout")
def logout(request: Request, db: DbSession) -> Response:
    auth_service.logout(db, request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=status.HTTP_200_OK, headers={"HX-Redirect": "/login"})
    clear_session_cookie(response)
    return response
