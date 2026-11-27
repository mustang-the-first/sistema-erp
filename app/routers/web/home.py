"""Landing redirect. Owner: Foundation."""

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from app.core.deps import OptionalUser
from app.models.enums import UserRole

router = APIRouter(include_in_schema=False)

LANDING = {
    UserRole.ADMIN: "/orders",
    UserRole.SELLER: "/orders",
    UserRole.ACCOUNTANT: "/accounting/journal",
}


@router.get("/")
def home(user: OptionalUser) -> RedirectResponse:
    target = "/login" if user is None else LANDING[user.role]
    return RedirectResponse(target, status_code=303)
