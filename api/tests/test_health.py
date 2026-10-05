import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.routes.health import get_db_check


def test_health_ok(settings: Settings) -> None:
    app = create_app(settings)
    app.dependency_overrides[get_db_check] = lambda: lambda: None

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_reports_unreachable_database(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unreachable"}


@pytest.mark.db
def test_health_with_real_database(clean_env: pytest.MonkeyPatch, admin_database_url: str) -> None:
    settings = Settings(_env_file=None, database_url=admin_database_url)

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
