"""Users JSON API (/api/v1). Admin only. Owner: agent A · Auth (docs/plan.md)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.db import DbSession
from app.core.deps import require_roles
from app.models.users import User
from app.schemas.users import UserCreate, UserOut, UserPageOut, UserUpdate
from app.services import users as users_service

# require_roles() with no roles: admin only.
AdminUser = Annotated[User, Depends(require_roles())]

router = APIRouter(tags=["users"], dependencies=[Depends(require_roles())])


@router.get("/users", response_model=UserPageOut)
def list_users(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserPageOut:
    page = users_service.list_users(db, limit=limit, offset=offset)
    return UserPageOut(items=[UserOut.model_validate(u) for u in page.items], total=page.total)


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: DbSession) -> UserOut:
    user = users_service.create_user(
        db, email=body.email, full_name=body.full_name, role=body.role, password=body.password
    )
    return UserOut.model_validate(user)


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: DbSession) -> UserOut:
    return UserOut.model_validate(users_service.get_user(db, user_id))


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, db: DbSession, actor: AdminUser) -> UserOut:
    changes = users_service.UserChanges(**body.model_dump(exclude_unset=True))
    user = users_service.update_user(db, user_id, changes, actor=actor)
    return UserOut.model_validate(user)
