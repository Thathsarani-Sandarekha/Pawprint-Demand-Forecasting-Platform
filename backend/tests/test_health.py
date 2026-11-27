"""endpoint smoke tests."""
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import APP_VERSION

client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "pet-demand-api",
        "version": APP_VERSION,
    }


def test_root_endpoint() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["health"] == "/api/v1/health"


def test_openapi_includes_health() -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert "/api/v1/health" in response.json()["paths"]


def test_cors_restricts_unauthorized_origin() -> None:
    response = client.get(
        "/api/v1/health", headers={"Origin": "https://not-a-trusted-origin.example"}
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
