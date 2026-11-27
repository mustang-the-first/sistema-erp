"""Auth API schemas."""

from pydantic import BaseModel, Field

from app.schemas.users import MAX_PASSWORD_LENGTH


class LoginIn(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)
