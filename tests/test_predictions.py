"""Tests for the /predictions/time-to-merge endpoint."""

from unittest.mock import patch, MagicMock
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.main import app


def _patch_all_tenant_sessions():
    """Context manager that patches tenant_session in both predictor and historical_metrics."""
    mock_session = MagicMock()

    @contextmanager
    def _fake(tenant_id: str):
        yield mock_session

    patcher_predictor = patch("app.services.predictor.tenant_session", _fake)
    patcher_historical = patch("app.services.historical_metrics.tenant_session", _fake)

    # Mock queries for historical metrics:
    # compute_author_avg_merge_hours -> query returns (None, 0)
    # compute_reviewer_load_index -> scalar returns 0
    mock_query = MagicMock()
    mock_session.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.one.return_value = (None, 0)
    mock_query.scalar.return_value = 0

    return patcher_predictor, patcher_historical, mock_session


def test_predict_time_to_merge_success() -> None:
    patcher_pred, patcher_hist, mock_session = _patch_all_tenant_sessions()
    with patcher_pred, patcher_hist:
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
    # Verify new response structure
    for factor in data["top_factors"]:
        assert "factor" in factor
        assert "contribution_hours" in factor
        assert "direction" in factor
        assert factor["direction"] in ("increases", "decreases")
    assert "confidence_score" in data


def test_predict_time_to_merge_uses_defaults() -> None:
    """When avg_author_merge_hours and reviewer_load_index are not supplied,
    they are auto-computed from historical data (falls back to defaults)."""
    patcher_pred, patcher_hist, mock_session = _patch_all_tenant_sessions()
    with patcher_pred, patcher_hist:
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


def test_predict_with_full_signals() -> None:
    """Test prediction with all available signals populated."""
    patcher_pred, patcher_hist, mock_session = _patch_all_tenant_sessions()
    with patcher_pred, patcher_hist:
        client = TestClient(app)
        payload = {
            "pr_id": "pr-full",
            "repository": "org/repo",
            "author_id": "dev1",
            "lines_added": 500,
            "lines_deleted": 100,
            "files_changed": 12,
            "reviewers_requested": 3,
            "avg_author_merge_hours": 30.0,
            "reviewer_load_index": 2.0,
            "time_to_first_review_hours": 8.0,
            "review_rounds": 3,
            "ci_pass_rate": 0.7,
            "ci_duration_minutes": 15.0,
            "ci_reruns": 2,
            "commit_count": 8,
            "force_push_count": 1,
            "author_open_pr_count": 4,
            "test_lines_added": 50,
            "directories_touched": 5,
            "touches_critical_path": True,
            "labels": ["breaking-change"],
            "comment_count": 12,
            "is_cross_timezone": True,
        }
        response = client.post(
            "/api/v1/predictions/time-to-merge",
            json=payload,
            headers={"X-Tenant-Id": "test-tenant"},
        )

    assert response.status_code == 200
    data = response.json()
    # With many risk factors, prediction should be higher
    assert data["predicted_merge_hours"] > 20.0
    assert data["risk_band"] in ("medium", "high")
    # Higher confidence with more signals
    assert data["confidence_score"] and data["confidence_score"] > 0.5
