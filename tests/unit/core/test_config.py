import pytest
from pydantic import ValidationError

from app.core.config import DEV_DATABASE_URL, DEV_SECRET_KEY, Settings

GOOD_SECRET = "x" * 48
NEON_URL = "postgresql://user:pw@ep-demo.us-east-1.aws.neon.tech/erp?sslmode=require"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings must not pick up the CI / developer environment in these tests."""
    for name in (
        "ERP_ENV",
        "ERP_DATABASE_URL",
        "DATABASE_URL",
        "ERP_SECRET_KEY",
        "ERP_DEMO_MODE",
        "ERP_DEMO_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def production(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "env": "production",
        "database_url": NEON_URL,
        "secret_key": GOOD_SECRET,
    }
    return settings(**(values | overrides))


def test_development_runs_with_defaults() -> None:
    config = settings()

    assert (config.secret_key, config.database_url) == (DEV_SECRET_KEY, DEV_DATABASE_URL)


def test_production_with_explicit_values_starts() -> None:
    config = production(demo_mode=True, demo_password="demo-pass-123")

    assert config.env == "production"
    assert config.database_url.startswith("postgresql+psycopg://")


@pytest.mark.parametrize(
    ("overrides", "missing"),
    [
        ({"secret_key": DEV_SECRET_KEY}, "ERP_SECRET_KEY"),
        ({"secret_key": "short"}, "ERP_SECRET_KEY"),
        ({"demo_mode": True}, "ERP_DEMO_PASSWORD"),
        ({"demo_mode": True, "demo_password": "1234"}, "ERP_DEMO_PASSWORD"),
    ],
)
def test_production_refuses_to_start_without_secrets(
    overrides: dict[str, object], missing: str
) -> None:
    with pytest.raises(ValidationError, match=missing):
        production(**overrides)


def test_production_reads_secrets_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ERP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", NEON_URL)  # as injected by a hosting provider
    monkeypatch.setenv("ERP_SECRET_KEY", GOOD_SECRET)

    config = settings()

    assert config.database_url == "postgresql+psycopg://" + NEON_URL.removeprefix("postgresql://")


def test_production_without_any_environment_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ERP_ENV", "production")

    with pytest.raises(ValidationError, match="Refusing to start in production"):
        settings()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgres://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ],
)
def test_database_url_uses_psycopg_driver(raw: str, expected: str) -> None:
    assert settings(database_url=raw).database_url == expected


def test_production_without_database_url_refuses_to_start() -> None:
    with pytest.raises(ValidationError, match="ERP_DATABASE_URL"):
        settings(env="production", secret_key=GOOD_SECRET)


def test_production_accepts_any_explicit_database_url() -> None:
    """Regression: a URL equal to the dev default, but explicitly set, is a valid choice."""
    config = production(database_url="postgres://erp:erp@localhost:5432/erp")

    assert config.database_url == DEV_DATABASE_URL
