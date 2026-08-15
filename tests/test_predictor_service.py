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


def _patch_all_sessions():
    """Patch tenant_session in both predictor and historical_metrics."""
    mock_session = MagicMock()

    @contextmanager
    def _fake(tenant_id: str):
        yield mock_session

    patcher_predictor = patch("app.services.predictor.tenant_session", _fake)
    patcher_historical = patch("app.services.historical_metrics.tenant_session", _fake)

    # Default mock: queries return empty results
    mock_query = MagicMock()
    mock_session.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.one.return_value = (None, 0)
    mock_query.scalar.return_value = 0

    return patcher_predictor, patcher_historical, mock_session


class TestHeuristicPredict:
    def test_baseline_small_pr(self) -> None:
        features = {
            "change_size": 50.0,
            "files_changed": 2.0,
            "reviewers_requested": 1.0,
            "avg_author_merge_hours": 24.0,
            "reviewer_load_index": 1.0,
            "churn_per_file": 25.0,
            "test_ratio": 0.0,
            "time_to_first_review_hours": 0.0,
            "review_rounds": 1.0,
            "comment_count": 0.0,
            "is_cross_timezone": 0.0,
            "ci_pass_rate": 1.0,
            "ci_duration_minutes": 0.0,
            "ci_reruns": 0.0,
            "commit_count": 1.0,
            "force_push_count": 0.0,
            "author_open_pr_count": 1.0,
            "directories_touched": 1.0,
            "touches_critical_path": 0.0,
            "has_fast_label": 0.0,
            "has_slow_label": 0.0,
        }
        predicted, factors = _heuristic_predict(features)
        # With these values, most contributions are from change_size and files_changed
        assert predicted > 3.0  # At least intercept + change_size contribution
        assert isinstance(factors, list)
        assert len(factors) > 0

    def test_large_pr_high_load(self) -> None:
        features = {
            "change_size": 1000.0,
            "files_changed": 20.0,
            "reviewers_requested": 3.0,
            "avg_author_merge_hours": 48.0,
            "reviewer_load_index": 3.0,
            "churn_per_file": 50.0,
            "test_ratio": 0.0,
            "time_to_first_review_hours": 0.0,
            "review_rounds": 1.0,
            "comment_count": 0.0,
            "is_cross_timezone": 0.0,
            "ci_pass_rate": 1.0,
            "ci_duration_minutes": 0.0,
            "ci_reruns": 0.0,
            "commit_count": 1.0,
            "force_push_count": 0.0,
            "author_open_pr_count": 1.0,
            "directories_touched": 1.0,
            "touches_critical_path": 0.0,
            "has_fast_label": 0.0,
            "has_slow_label": 0.0,
        }
        predicted, factors = _heuristic_predict(features)
        # Should be quite high with large PR + high load
        assert predicted > 25.0
        assert len(factors) >= 3


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
        patcher_pred, patcher_hist, mock_session = _patch_all_sessions()
        with patcher_pred, patcher_hist, patch("app.services.predictor.predict_with_model", return_value=None):
            payload = PredictionRequest(
                pr_id="pr-1",
                repository="org/repo",
                author_id="dev1",
                lines_added=100,
                lines_deleted=20,
                files_changed=5,
                reviewers_requested=2,
                avg_author_merge_hours=24.0,
                reviewer_load_index=1.0,
            )
            result = predict_time_to_merge_hours(payload, tenant_id="t1")

        assert result["tenant_id"] == "t1"
        assert result["pr_id"] == "pr-1"
        assert result["predicted_merge_hours"] >= 1.0
        assert result["risk_band"] in ("low", "medium", "high")
        assert isinstance(result["top_factors"], list)
        assert len(result["top_factors"]) > 0
        # top_factors are now PredictionFactor objects
        first_factor = result["top_factors"][0]
        assert hasattr(first_factor, "factor")
        assert hasattr(first_factor, "contribution_hours")
        assert hasattr(first_factor, "direction")
        assert "confidence_score" in result

    def test_uses_ml_model_when_available(self) -> None:
        patcher_pred, patcher_hist, mock_session = _patch_all_sessions()
        with patcher_pred, patcher_hist, patch("app.services.predictor.predict_with_model", return_value=18.5):
            payload = PredictionRequest(
                pr_id="pr-2",
                repository="org/repo",
                author_id="dev1",
                lines_added=200,
                lines_deleted=50,
                files_changed=8,
                reviewers_requested=2,
                avg_author_merge_hours=24.0,
                reviewer_load_index=1.0,
            )
            result = predict_time_to_merge_hours(payload, tenant_id="t1")

        assert result["predicted_merge_hours"] == 18.5
        assert result["risk_band"] == "medium"

    def test_prediction_minimum_floor(self) -> None:
        """Prediction is always at least 1.0 hours."""
        patcher_pred, patcher_hist, mock_session = _patch_all_sessions()
        with patcher_pred, patcher_hist, patch("app.services.predictor.predict_with_model", return_value=0.1):
            payload = PredictionRequest(
                pr_id="pr-3",
                repository="org/repo",
                author_id="dev1",
                lines_added=1,
                lines_deleted=0,
                files_changed=1,
                reviewers_requested=0,
                avg_author_merge_hours=24.0,
                reviewer_load_index=1.0,
            )
            result = predict_time_to_merge_hours(payload, tenant_id="t1")

        assert result["predicted_merge_hours"] == 1.0

    def test_auto_enriches_context_when_not_supplied(self) -> None:
        """When avg_author_merge_hours/reviewer_load_index are None, auto-compute."""
        patcher_pred, patcher_hist, mock_session = _patch_all_sessions()
        with patcher_pred, patcher_hist, patch("app.services.predictor.predict_with_model", return_value=None):
            payload = PredictionRequest(
                pr_id="pr-4",
                repository="org/repo",
                author_id="dev1",
                lines_added=50,
                lines_deleted=10,
                files_changed=2,
                reviewers_requested=1,
                # avg_author_merge_hours and reviewer_load_index left as None
            )
            result = predict_time_to_merge_hours(payload, tenant_id="t1")

        # Should not error — falls back to defaults via historical_metrics
        assert result["predicted_merge_hours"] >= 1.0


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
            avg_author_merge_hours=24.0,
            reviewer_load_index=1.0,
        )
        features = build_feature_vector(payload)
        expected_keys = {
            "change_size",
            "files_changed",
            "churn_per_file",
            "test_ratio",
            "reviewers_requested",
            "time_to_first_review_hours",
            "review_rounds",
            "comment_count",
            "is_cross_timezone",
            "avg_author_merge_hours",
            "reviewer_load_index",
            "ci_pass_rate",
            "ci_duration_minutes",
            "ci_reruns",
            "commit_count",
            "force_push_count",
            "author_open_pr_count",
            "directories_touched",
            "touches_critical_path",
            "has_fast_label",
            "has_slow_label",
        }
        assert set(features.keys()) == expected_keys
        assert features["change_size"] == 150.0
        assert features["churn_per_file"] == 50.0
        assert features["reviewers_requested"] == 2.0
        assert features["avg_author_merge_hours"] == 24.0

    def test_label_detection(self) -> None:
        payload = PredictionRequest(
            pr_id="pr-labels",
            repository="org/repo",
            author_id="dev1",
            lines_added=10,
            lines_deleted=5,
            files_changed=1,
            reviewers_requested=1,
            avg_author_merge_hours=24.0,
            reviewer_load_index=1.0,
            labels=["hotfix", "breaking-change"],
        )
        features = build_feature_vector(payload)
        assert features["has_fast_label"] == 1.0
        assert features["has_slow_label"] == 1.0

    def test_test_ratio_computation(self) -> None:
        payload = PredictionRequest(
            pr_id="pr-tests",
            repository="org/repo",
            author_id="dev1",
            lines_added=100,
            lines_deleted=0,
            files_changed=3,
            reviewers_requested=1,
            avg_author_merge_hours=24.0,
            reviewer_load_index=1.0,
            test_lines_added=40,
        )
        features = build_feature_vector(payload)
        assert features["test_ratio"] == 0.4
