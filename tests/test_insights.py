"""Tests for the /insights/bottlenecks endpoint."""

from unittest.mock import patch, MagicMock
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.main import app


def _patch_tenant_session():
    mock_session = MagicMock()

    @contextmanager
    def _fake(tenant_id: str):
        yield mock_session

    return patch("app.services.predictor.tenant_session", _fake), mock_session


def test_bottleneck_insights_returns_defaults_with_insufficient_data() -> None:
    """When <5 rows exist, generic recommendations are returned."""
    patcher, mock_session = _patch_tenant_session()
    # Mock the count query to return < 5
    mock_query = MagicMock()
    mock_session.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.scalar.return_value = 2

    with patcher:
        client = TestClient(app)
        response = client.get(
            "/api/v1/insights/bottlenecks",
            headers={"X-Tenant-Id": "test-tenant"},
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    factors = [item["factor"] for item in data]
    assert "reviewers_requested" in factors
    assert "reviewer_load_index" in factors


def test_bottleneck_insights_uses_default_tenant() -> None:
    patcher, mock_session = _patch_tenant_session()
    mock_query = MagicMock()
    mock_session.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.scalar.return_value = 0

    with patcher:
        client = TestClient(app)
        response = client.get("/api/v1/insights/bottlenecks")

    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
