"""Users HTML views (Jinja2 + HTMX). Admin only. Owner: agent A · Auth (docs/plan.md)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.db import DbSession
from app.core.deps import require_roles
from app.core.errors import BusinessRuleViolation
from app.core.templating import templates
from app.models.enums import UserRole
from app.models.users import User
from app.services import users as users_service

AdminUser = Annotated[User, Depends(require_roles())]

router = APIRouter(tags=["users"], dependencies=[Depends(require_roles())])

PAGE_SIZE = 50
ROLE_LABELS = {
    UserRole.ADMIN: "Administrador",
    UserRole.SELLER: "Vendedor",
    UserRole.ACCOUNTANT: "Contador",
}
# UI texts (Spanish) for the service error codes; unknown codes fall back to the message.
ERROR_TEXTS = {
    "invalid_email": "El email no es válido.",
    "email_taken": "Ya existe un usuario con ese email.",
    "full_name_required": "El nombre es obligatorio.",
    "password_too_short": (
        f"La contraseña debe tener al menos {users_service.MIN_PASSWORD_LENGTH} caracteres."
    ),
    "cannot_deactivate_self": "No podés desactivar tu propio usuario.",
    "cannot_demote_self": "No podés quitarte el rol de administrador.",
}


def _error_text(exc: BusinessRuleViolation) -> str:
    return ERROR_TEXTS.get(exc.code, exc.message)


def _panel_context(db: Session, offset: int, **extra: Any) -> dict[str, Any]:
    page = users_service.list_users(db, limit=PAGE_SIZE, offset=offset)
    return {
        "users": page.items,
        "total": page.total,
        "offset": offset,
        "page_size": PAGE_SIZE,
        "role_labels": ROLE_LABELS,
        "form": {"email": "", "full_name": "", "role": UserRole.SELLER.value},
        **extra,
    }


@router.get("/users", response_class=HTMLResponse)
def users_page(
    request: Request, db: DbSession, offset: Annotated[int, Query(ge=0)] = 0
) -> HTMLResponse:
    return templates.TemplateResponse(request, "users/index.html", _panel_context(db, offset))


@router.post("/users", response_class=HTMLResponse)
def create_user(
    request: Request,
    db: DbSession,
    email: Annotated[str, Form(max_length=255)],
    full_name: Annotated[str, Form(max_length=200)],
    role: Annotated[UserRole, Form()],
    password: Annotated[str, Form(max_length=128)],
) -> HTMLResponse:
    # Rendered with 200 even on a rule violation: htmx does not swap 4xx responses.
    try:
        user = users_service.create_user(
            db, email=email, full_name=full_name, role=role, password=password
        )
    except BusinessRuleViolation as exc:
        context = _panel_context(
            db,
            0,
            error=_error_text(exc),
            form={"email": email, "full_name": full_name, "role": role.value},
        )
    else:
        context = _panel_context(db, 0, message=f"Usuario {user.email} creado.")
    return templates.TemplateResponse(request, "users/_panel.html", context)


@router.get("/users/{user_id}", response_class=HTMLResponse)
def edit_page(request: Request, user_id: int, db: DbSession) -> HTMLResponse:
    target = users_service.get_user(db, user_id)
    return templates.TemplateResponse(
        request, "users/edit.html", {"target": target, "role_labels": ROLE_LABELS}
    )


@router.patch("/users/{user_id}", response_class=HTMLResponse)
def update_user(
    request: Request,
    user_id: int,
    db: DbSession,
    actor: AdminUser,
    full_name: Annotated[str, Form(max_length=200)],
    role: Annotated[UserRole, Form()],
    is_active: Annotated[bool, Form()],
    password: Annotated[str, Form(max_length=128)] = "",
) -> HTMLResponse:
    changes = users_service.UserChanges(
        full_name=full_name, role=role, is_active=is_active, password=password or None
    )
    try:
        target = users_service.update_user(db, user_id, changes, actor=actor)
        context: dict[str, Any] = {"target": target, "message": "Cambios guardados."}
    except BusinessRuleViolation as exc:
        # The service validates before changing anything, so the row is still untouched.
        target = users_service.get_user(db, user_id)
        context = {"target": target, "error": _error_text(exc)}
    context["role_labels"] = ROLE_LABELS
    return templates.TemplateResponse(request, "users/_edit_form.html", context)
