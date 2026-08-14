"""Tests for the /estimates/github-history endpoint."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


def test_estimate_from_github_history_success() -> None:
    """Test the full estimate flow with mocked GitHub API calls."""
    mock_closed_prs = [
        {"created_at": "2026-08-01T10:00:00Z", "merged_at": "2026-08-01T20:00:00Z"},
        {"created_at": "2026-08-02T10:00:00Z", "merged_at": "2026-08-03T10:00:00Z"},
        {"created_at": "2026-08-04T10:00:00Z", "merged_at": "2026-08-06T10:00:00Z"},
    ]
    mock_open_prs = [
        {
            "number": 42,
            "title": "Add feature",
            "html_url": "https://github.com/org/repo/pull/42",
            "created_at": "2026-08-10T10:00:00Z",
            "user": {"login": "dev1"},
            "additions": 100,
            "deletions": 20,
            "changed_files": 5,
            "requested_reviewers": [{"login": "reviewer1"}],
            "draft": False,
        }
    ]

    with patch("app.api.routes.estimates.fetch_closed_pr_history", return_value=mock_closed_prs), \
         patch("app.api.routes.estimates.fetch_open_pull_requests", return_value=mock_open_prs):
        client = TestClient(app)
        payload = {
            "owner": "org",
            "repository": "repo",
            "lookback_prs": 50,
            "github_token": "ghp_fake_token",
        }
        response = client.post(
            "/api/v1/estimates/github-history",
            json=payload,
            headers={"X-Tenant-Id": "test-tenant"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["owner"] == "org"
    assert data["repository"] == "repo"
    assert data["merged_pr_count"] == 3
    assert data["active_pr_count"] == 1
    assert data["median_merge_hours"] > 0
    assert data["risk_band"] in ("low", "medium", "high")
    assert len(data["active_pull_requests"]) == 1


def test_estimate_github_history_error_handling() -> None:
    """Test that GithubHistoryError is returned as HTTP 400."""
    from app.services.github_history import GithubHistoryError

    with patch(
        "app.api.routes.estimates.fetch_closed_pr_history",
        side_effect=GithubHistoryError("API rate limited"),
    ):
        client = TestClient(app)
        payload = {
            "owner": "org",
            "repository": "repo",
            "lookback_prs": 50,
        }
        response = client.post("/api/v1/estimates/github-history", json=payload)

    assert response.status_code == 400
    assert "API rate limited" in response.json()["detail"]
