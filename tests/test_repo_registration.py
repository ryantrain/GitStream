from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_repo_registration_manual_setup_returns_generated_webhook_details() -> None:
    response = client.post(
        "/api/v1/repos/register",
        json={
            "repository_url": "https://github.com/octo/example-repo",
            "github_token": "",
            "org_id": "org-123",
            "auto_install_webhook": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["repository"]["owner"] == "octo"
    assert payload["repository"]["name"] == "example-repo"
    assert payload["org_id"] == "org-123"
    assert payload["webhook_url"] == "https://api.gitstream.dev/api/v1/webhooks/github"
    assert payload["webhook_secret"]
