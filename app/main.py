from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.db import default_session_factory
from app.core.deps import require_csrf
from app.core.errors import register_exception_handlers
from app.routers.api import accounting as api_accounting
from app.routers.api import auth as api_auth
from app.routers.api import catalog as api_catalog
from app.routers.api import collections as api_collections
from app.routers.api import delivery_notes as api_delivery_notes
from app.routers.api import invoices as api_invoices
from app.routers.api import orders as api_orders
from app.routers.api import reports as api_reports
from app.routers.api import stock as api_stock
from app.routers.api import users as api_users
from app.routers.web import accounting as web_accounting
from app.routers.web import auth as web_auth
from app.routers.web import catalog as web_catalog
from app.routers.web import collections as web_collections
from app.routers.web import delivery_notes as web_delivery_notes
from app.routers.web import home as web_home
from app.routers.web import invoices as web_invoices
from app.routers.web import orders as web_orders
from app.routers.web import reports as web_reports
from app.routers.web import stock as web_stock
from app.routers.web import users as web_users

STATIC_DIR = Path(__file__).resolve().parent / "static"
API_PREFIX = "/api/v1"

API_ROUTERS = (
    api_auth,
    api_users,
    api_catalog,
    api_stock,
    api_orders,
    api_delivery_notes,
    api_invoices,
    api_accounting,
    api_collections,
    api_reports,
)
WEB_ROUTERS = (
    web_home,
    web_auth,
    web_users,
    web_catalog,
    web_stock,
    web_orders,
    web_delivery_notes,
    web_invoices,
    web_accounting,
    web_collections,
    web_reports,
)


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

    @app.api_route("/health", methods=["GET", "HEAD"], tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    for module in API_ROUTERS:
        app.include_router(module.router, prefix=API_PREFIX)
    for module in WEB_ROUTERS:
        app.include_router(
            module.router, include_in_schema=False, dependencies=[Depends(require_csrf)]
        )
    return app


app = create_app()
