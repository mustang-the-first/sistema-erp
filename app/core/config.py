from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ERP_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://erp:erp@localhost:5432/erp"
    test_database_url: str = "postgresql+psycopg://erp:erp@localhost:5432/erp_test"
    secret_key: str = "change-me"  # noqa: S105 - overridden via env outside development
    point_of_sale: int = 1


@lru_cache
def get_settings() -> Settings:
    return Settings()
