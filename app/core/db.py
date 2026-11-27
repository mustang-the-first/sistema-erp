"""Engine, session factory and the request-scoped session dependency.

One transaction per request: ``get_db`` commits when the endpoint returns and rolls back on any
exception. It MUST be used through ``DbSession`` (``Depends(get_db, scope="function")``) so the
commit runs *before* the response is sent: a failed commit becomes a 500 instead of a 200 for
data that was never persisted. Services only ``flush()``; they never commit.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def build_engine(url: str, **kwargs: object) -> Engine:
    return create_engine(url, pool_pre_ping=True, **kwargs)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


_default_factory: sessionmaker[Session] | None = None


def default_session_factory() -> sessionmaker[Session]:
    """Lazily built so importing the app never opens a connection."""
    global _default_factory
    if _default_factory is None:
        _default_factory = build_session_factory(build_engine(get_settings().database_url))
    return _default_factory


def get_db(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db, scope="function")]
