"""Unit tests for the model loader service."""

from unittest.mock import patch, MagicMock
from pathlib import Path

from app.services.model_loader import (
    load_model,
    reload_model,
    predict_with_model,
    _model_cache,
    FEATURE_COLUMNS,
)


class TestLoadModel:
    def setup_method(self):
        _model_cache.clear()

    def test_returns_none_when_no_model_file(self) -> None:
        with patch("app.services.model_loader._get_model_path", return_value=Path("/nonexistent/model.joblib")):
            model = load_model()
        assert model is None

    def test_caches_model_after_first_load(self) -> None:
        with patch("app.services.model_loader._get_model_path", return_value=Path("/nonexistent/model.joblib")):
            load_model()
            # Second call should use cache
            assert "model" in _model_cache
            model = load_model()
        assert model is None

    def test_loads_model_from_disk(self) -> None:
        mock_model = MagicMock()
        fake_path = MagicMock(spec=Path)
        fake_path.exists.return_value = True

        with patch("app.services.model_loader._get_model_path", return_value=fake_path), \
             patch("joblib.load", return_value=mock_model):
            model = load_model()

        assert model is mock_model

    def test_reload_clears_cache(self) -> None:
        _model_cache["model"] = "old_model"
        with patch("app.services.model_loader._get_model_path", return_value=Path("/nonexistent/model.joblib")):
            model = reload_model()
        assert model is None
        assert _model_cache["model"] is None


class TestPredictWithModel:
    def setup_method(self):
        _model_cache.clear()

    def test_returns_none_when_no_model(self) -> None:
        with patch("app.services.model_loader._get_model_path", return_value=Path("/nonexistent/model.joblib")):
            result = predict_with_model({"change_size": 100.0})
        assert result is None

    def test_runs_inference_with_loaded_model(self) -> None:
        import numpy as np

        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([15.5])
        _model_cache["model"] = mock_model

        features = {col: 1.0 for col in FEATURE_COLUMNS}
        result = predict_with_model(features)

        assert result == 15.5
        mock_model.predict.assert_called_once()

    def test_returns_none_on_inference_error(self) -> None:
        mock_model = MagicMock()
        mock_model.predict.side_effect = RuntimeError("model error")
        _model_cache["model"] = mock_model

        features = {col: 1.0 for col in FEATURE_COLUMNS}
        result = predict_with_model(features)

        assert result is None
