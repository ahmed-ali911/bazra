from fastapi.testclient import TestClient

from app.config import settings


def test_cors_allows_configured_origin_on_a_real_request(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"Origin": settings.cors_allowed_origin})
    assert response.headers["access-control-allow-origin"] == settings.cors_allowed_origin
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_preflight_allows_configured_origin(client: TestClient) -> None:
    response = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": settings.cors_allowed_origin,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == settings.cors_allowed_origin
