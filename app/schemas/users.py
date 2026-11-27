"""User API schemas. ``password_hash`` is never part of any output schema.

Only structural limits live here; business rules (email format and uniqueness, password length)
are enforced by ``app.services.users`` so they apply to every entry point and answer with the
shared ``{"detail", "code"}`` error format.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import UserRole

MAX_PASSWORD_LENGTH = 128


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime


class UserPageOut(BaseModel):
    items: list[UserOut]
    total: int


class UserCreate(BaseModel):
    email: str = Field(max_length=255)
    full_name: str = Field(max_length=200)
    role: UserRole
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, max_length=MAX_PASSWORD_LENGTH)
