from functools import lru_cache
from typing import Literal, Self

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Development-only defaults. In production every one of them must come from the environment:
# the app refuses to start otherwise (see ``_production_requires_explicit_secrets``).
DEV_DATABASE_URL = "postgresql+psycopg://erp:erp@localhost:5432/erp"
DEV_SECRET_KEY = "dev-only-insecure-secret"  # noqa: S105 - rejected in production
MIN_SECRET_KEY_LENGTH = 32
MIN_DEMO_PASSWORD_LENGTH = 8


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ERP_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    # Hosting providers usually inject DATABASE_URL; ERP_DATABASE_URL wins when both are set.
    database_url: str = Field(
        default=DEV_DATABASE_URL,
        validation_alias=AliasChoices("ERP_DATABASE_URL", "DATABASE_URL"),
    )
    test_database_url: str = "postgresql+psycopg://erp:erp@localhost:5432/erp_test"
    secret_key: str = DEV_SECRET_KEY
    point_of_sale: int = 1
    # Public demo: demo accounts cannot change passwords/roles or deactivate users, a banner
    # says data is reset daily, and scripts.reset_demo is allowed to wipe the database.
    demo_mode: bool = False
    demo_password: str | None = None

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, url: str) -> str:
        """``postgres://`` / ``postgresql://`` (as providers hand them out) -> psycopg 3."""
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url.removeprefix(prefix)
        return url

    @model_validator(mode="after")
    def _production_requires_explicit_secrets(self) -> Self:
        if self.env != "production":
            return self
        problems = []
        if "database_url" not in self.model_fields_set:  # explicitly provided, whatever value
            problems.append("ERP_DATABASE_URL (or DATABASE_URL) must be set")
        if (
            "secret_key" not in self.model_fields_set
            or self.secret_key == DEV_SECRET_KEY
            or len(self.secret_key) < MIN_SECRET_KEY_LENGTH
        ):
            problems.append(f"ERP_SECRET_KEY must be set (>= {MIN_SECRET_KEY_LENGTH} chars)")
        if self.demo_mode and (
            not self.demo_password or len(self.demo_password) < MIN_DEMO_PASSWORD_LENGTH
        ):
            problems.append(
                f"ERP_DEMO_PASSWORD must be set (>= {MIN_DEMO_PASSWORD_LENGTH} chars) "
                "when ERP_DEMO_MODE is true"
            )
        if problems:
            raise ValueError("Refusing to start in production: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
