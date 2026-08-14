"""Tests for the /predictions/time-to-merge endpoint."""

from unittest.mock import patch, MagicMock
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.main import app


def _patch_tenant_session():
    """Context manager that patches tenant_session with a mock."""
    mock_session = MagicMock()

    @contextmanager
    def _fake(tenant_id: str):
        yield mock_session

    return patch("app.services.predictor.tenant_session", _fake), mock_session


def test_predict_time_to_merge_success() -> None:
    patcher, mock_session = _patch_tenant_session()
    with patcher:
        client = TestClient(app)
        payload = {
            "pr_id": "pr-123",
            "repository": "org/repo",
            "author_id": "dev1",
            "lines_added": 100,
            "lines_deleted": 20,
            "files_changed": 5,
            "reviewers_requested": 2,
            "avg_author_merge_hours": 24.0,
            "reviewer_load_index": 1.5,
        }
        response = client.post(
            "/api/v1/predictions/time-to-merge",
            json=payload,
            headers={"X-Tenant-Id": "test-tenant"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["pr_id"] == "pr-123"
    assert data["tenant_id"] == "test-tenant"
    assert data["predicted_merge_hours"] > 0
    assert data["risk_band"] in ("low", "medium", "high")
    assert isinstance(data["top_factors"], list)
    assert len(data["top_factors"]) > 0


def test_predict_time_to_merge_uses_defaults() -> None:
    """Test that default values for avg_author_merge_hours and reviewer_load_index work."""
    patcher, mock_session = _patch_tenant_session()
    with patcher:
        client = TestClient(app)
        payload = {
            "pr_id": "pr-456",
            "repository": "org/repo",
            "author_id": "dev2",
            "lines_added": 10,
            "lines_deleted": 5,
            "files_changed": 1,
            "reviewers_requested": 1,
        }
        response = client.post(
            "/api/v1/predictions/time-to-merge",
            json=payload,
        )

    assert response.status_code == 200
    data = response.json()
    assert data["predicted_merge_hours"] >= 1.0


def test_predict_time_to_merge_validation_error() -> None:
    client = TestClient(app)
    payload = {
        "pr_id": "pr-789",
        "repository": "org/repo",
        "author_id": "dev3",
        "lines_added": -1,  # Invalid: ge=0
        "lines_deleted": 0,
        "files_changed": 1,
        "reviewers_requested": 0,
    }
    response = client.post("/api/v1/predictions/time-to-merge", json=payload)
    assert response.status_code == 422
