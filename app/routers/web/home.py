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


# HEAD too: uptime monitors and link previewers probe the root with HEAD.
@router.api_route("/", methods=["GET", "HEAD"])
def home(user: OptionalUser) -> RedirectResponse:
    """No session -> /login; logged in -> the landing page of the user's role."""
    target = "/login" if user is None else LANDING[user.role]
    return RedirectResponse(target, status_code=303)
