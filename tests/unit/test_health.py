import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.mark.unit
def test_health_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
