from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.core.db import get_db


@pytest.fixture
def app(app: FastAPI, db: Session) -> FastAPI:
    """Like the root ``app`` fixture, but the data the test arranged is committed (SAVEPOINT
    released) before each request, so an error response rolls back only the request's own work,
    as in production. The root fixture's rollback would also discard the arranged rows and the
    logged-in user (turning a 403/404/422 check into a 401 on the next request)."""

    def override_get_db() -> Iterator[Session]:
        db.commit()
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    return app
