from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "promotiva-api"}


def test_readiness_reports_all_foundation_dependencies(monkeypatch) -> None:
    async def dependencies_ready() -> dict[str, str]:
        return {
            "database": "ok",
            "pgvector": "ok",
            "redis": "ok",
            "storage": "ok",
        }

    monkeypatch.setattr("app.api.routes.health.check_dependencies", dependencies_ready)

    with TestClient(app) as client:
        response = client.get("/api/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "services": {
            "database": "ok",
            "pgvector": "ok",
            "redis": "ok",
            "storage": "ok",
        },
    }


def test_readiness_fails_when_pgvector_is_unavailable(monkeypatch) -> None:
    async def pgvector_unavailable() -> dict[str, str]:
        return {
            "database": "ok",
            "pgvector": "unavailable",
            "redis": "ok",
            "storage": "ok",
        }

    monkeypatch.setattr("app.api.routes.health.check_dependencies", pgvector_unavailable)

    with TestClient(app) as client:
        response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["services"]["pgvector"] == "unavailable"
