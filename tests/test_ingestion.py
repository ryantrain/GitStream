"""Tests for the /ingestion/pr-event endpoint."""

from unittest.mock import patch, MagicMock
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.main import app


def _patch_tenant_session():
    mock_session = MagicMock()

    @contextmanager
    def _fake(tenant_id: str):
        yield mock_session

    return patch("app.services.ingestion_worker.tenant_session", _fake), mock_session


def test_ingest_pr_event_success() -> None:
    patcher, mock_session = _patch_tenant_session()
    with patcher:
        client = TestClient(app)
        payload = {
            "pr_id": "pr-100",
            "repository": "org/repo",
            "author_id": "dev1",
            "created_at": "2026-08-10T12:00:00Z",
            "lines_added": 50,
            "lines_deleted": 10,
            "files_changed": 3,
            "reviewers_requested": 2,
            "labels": ["bug", "urgent"],
        }
        response = client.post(
            "/api/v1/ingestion/pr-event",
            json=payload,
            headers={"X-Tenant-Id": "test-tenant"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    assert data["tenant_id"] == "test-tenant"
    # Verify session.merge was called (PR metric was persisted)
    mock_session.merge.assert_called_once()
    mock_session.commit.assert_called_once()


def test_ingest_pr_event_uses_default_tenant() -> None:
    patcher, mock_session = _patch_tenant_session()
    with patcher:
        client = TestClient(app)
        payload = {
            "pr_id": "pr-101",
            "repository": "org/repo",
            "author_id": "dev2",
            "created_at": "2026-08-10T13:00:00Z",
            "lines_added": 20,
            "lines_deleted": 5,
            "files_changed": 1,
            "reviewers_requested": 1,
        }
        response = client.post("/api/v1/ingestion/pr-event", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["tenant_id"] == "dev-tenant"


def test_ingest_pr_event_validation_error() -> None:
    client = TestClient(app)
    payload = {
        "pr_id": "pr-102",
        "repository": "org/repo",
        "author_id": "dev3",
        "created_at": "2026-08-10T14:00:00Z",
        "lines_added": 10,
        "lines_deleted": 5,
        "files_changed": 0,  # Invalid: ge=1
        "reviewers_requested": 0,
    }
    response = client.post("/api/v1/ingestion/pr-event", json=payload)
    assert response.status_code == 422
