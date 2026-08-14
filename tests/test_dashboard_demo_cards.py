from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_dashboard_route_contains_demo_pr_cards() -> None:
    response = client.get("/dashboard")
    assert response.status_code == 200
    html = response.text
    assert "GitStream Dashboard" in html
    assert "Refactor queue worker" in html
    assert "Refresh alerts" in html
