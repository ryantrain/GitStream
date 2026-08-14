from fastapi.testclient import TestClient

from app.main import app


def test_home_page_renders() -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "GitStream" in response.text
