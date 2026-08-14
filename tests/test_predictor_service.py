"""Unit tests for the predictor service logic."""

from unittest.mock import patch, MagicMock
from contextlib import contextmanager

from app.schemas.prediction import PredictionRequest
from app.services.predictor import (
    _heuristic_predict,
    _risk_band,
    predict_time_to_merge_hours,
)
from app.services.feature_engineering import build_feature_vector


def _patch_tenant_session():
    mock_session = MagicMock()

    @contextmanager
    def _fake(tenant_id: str):
        yield mock_session

    return patch("app.services.predictor.tenant_session", _fake), mock_session


class TestHeuristicPredict:
    def test_baseline_small_pr(self) -> None:
        features = {
            "change_size": 50.0,
            "files_changed": 2.0,
            "reviewers_requested": 1.0,
            "avg_author_merge_hours": 10.0,
            "reviewer_load_index": 1.0,
            "churn_per_file": 25.0,
        }
        result = _heuristic_predict(features)
        # 2.5 + 0.02*50 + 1.1*1 + 4.0*(1-1) + 0.15*10 = 2.5 + 1 + 1.1 + 0 + 1.5 = 6.1
        assert abs(result - 6.1) < 0.01

    def test_large_pr_high_load(self) -> None:
        features = {
            "change_size": 1000.0,
            "files_changed": 20.0,
            "reviewers_requested": 3.0,
            "avg_author_merge_hours": 48.0,
            "reviewer_load_index": 3.0,
            "churn_per_file": 50.0,
        }
        result = _heuristic_predict(features)
        # 2.5 + 0.02*1000 + 1.1*3 + 4.0*(3-1) + 0.15*48 = 2.5 + 20 + 3.3 + 8 + 7.2 = 41.0
        assert abs(result - 41.0) < 0.01


class TestRiskBand:
    def test_low(self) -> None:
        assert _risk_band(5.0) == "low"
        assert _risk_band(11.9) == "low"

    def test_medium(self) -> None:
        assert _risk_band(12.0) == "medium"
        assert _risk_band(35.9) == "medium"

    def test_high(self) -> None:
        assert _risk_band(36.0) == "high"
        assert _risk_band(100.0) == "high"


class TestPredictTimeToMerge:
    def test_returns_correct_structure(self) -> None:
        patcher, mock_session = _patch_tenant_session()
        with patcher, patch("app.services.predictor.predict_with_model", return_value=None):
            payload = PredictionRequest(
                pr_id="pr-1",
                repository="org/repo",
                author_id="dev1",
                lines_added=100,
                lines_deleted=20,
                files_changed=5,
                reviewers_requested=2,
            )
            result = predict_time_to_merge_hours(payload, tenant_id="t1")

        assert result["tenant_id"] == "t1"
        assert result["pr_id"] == "pr-1"
        assert result["predicted_merge_hours"] >= 1.0
        assert result["risk_band"] in ("low", "medium", "high")
        assert "reviewer_load_index" in result["top_factors"]

    def test_uses_ml_model_when_available(self) -> None:
        patcher, mock_session = _patch_tenant_session()
        with patcher, patch("app.services.predictor.predict_with_model", return_value=18.5):
            payload = PredictionRequest(
                pr_id="pr-2",
                repository="org/repo",
                author_id="dev1",
                lines_added=200,
                lines_deleted=50,
                files_changed=8,
                reviewers_requested=2,
            )
            result = predict_time_to_merge_hours(payload, tenant_id="t1")

        assert result["predicted_merge_hours"] == 18.5
        assert result["risk_band"] == "medium"

    def test_prediction_minimum_floor(self) -> None:
        """Prediction is always at least 1.0 hours."""
        patcher, mock_session = _patch_tenant_session()
        with patcher, patch("app.services.predictor.predict_with_model", return_value=0.1):
            payload = PredictionRequest(
                pr_id="pr-3",
                repository="org/repo",
                author_id="dev1",
                lines_added=1,
                lines_deleted=0,
                files_changed=1,
                reviewers_requested=0,
            )
            result = predict_time_to_merge_hours(payload, tenant_id="t1")

        assert result["predicted_merge_hours"] == 1.0


class TestBuildFeatureVector:
    def test_vector_keys(self) -> None:
        payload = PredictionRequest(
            pr_id="pr-x",
            repository="org/repo",
            author_id="dev1",
            lines_added=100,
            lines_deleted=50,
            files_changed=3,
            reviewers_requested=2,
        )
        features = build_feature_vector(payload)
        expected_keys = {
            "change_size",
            "files_changed",
            "reviewers_requested",
            "avg_author_merge_hours",
            "reviewer_load_index",
            "churn_per_file",
        }
        assert set(features.keys()) == expected_keys
        assert features["change_size"] == 150.0
        assert features["churn_per_file"] == 50.0
