"""Shared test fixtures for GitStream."""

from unittest.mock import MagicMock, patch
from contextlib import contextmanager
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def mock_db_session():
    """Provide a mock SQLAlchemy session and patch tenant_session."""
    mock_session = MagicMock(spec=Session)

    @contextmanager
    def _fake_tenant_session(tenant_id: str) -> Generator[Session, None, None]:
        yield mock_session

    with patch("app.db.session.tenant_session", _fake_tenant_session):
        yield mock_session
