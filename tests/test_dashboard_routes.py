from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_dashboard_route_exists() -> None:
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "GitStream Dashboard" in response.text
    assert "<button" in response.text
    assert "<input" in response.text


def test_repository_settings_route_exists() -> None:
    response = client.get("/dashboard/settings")
    assert response.status_code == 200
    assert "Repository registration" in response.text
    assert "<input" in response.text
    assert "<button" in response.text
