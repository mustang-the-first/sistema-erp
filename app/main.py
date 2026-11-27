from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.db import default_session_factory
from app.core.errors import register_exception_handlers

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(session_factory: sessionmaker[Session] | None = None) -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Sistema ERP",
        version="0.1.0",
        docs_url="/docs" if settings.env != "production" else None,
    )
    app.state.session_factory = session_factory or default_session_factory()
    register_exception_handlers(app)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
