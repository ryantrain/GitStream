"""ML model loading and inference for time-to-merge prediction.

Supports loading trained models (joblib format) from disk. Falls back to the
heuristic predictor when no trained model is available.
"""

import logging
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)

# Expected feature order for the trained model input vector.
FEATURE_COLUMNS = [
    "change_size",
    "files_changed",
    "reviewers_requested",
    "avg_author_merge_hours",
    "reviewer_load_index",
    "churn_per_file",
]

_model_cache: dict[str, Any] = {}


def _get_model_path() -> Path:
    """Resolve the path to the trained model artifact."""
    return Path(getattr(settings, "model_path", "models/merge_time_model.joblib"))


def load_model() -> Any | None:
    """Load the trained model from disk, caching it in memory.

    Returns None if no model file exists (triggering heuristic fallback).
    """
    model_path = _get_model_path()

    if "model" in _model_cache:
        return _model_cache["model"]

    if not model_path.exists():
        logger.info("No trained model found at %s — using heuristic fallback.", model_path)
        _model_cache["model"] = None
        return None

    try:
        import joblib
        model = joblib.load(model_path)
        _model_cache["model"] = model
        logger.info("Loaded trained model from %s", model_path)
        return model
    except Exception as exc:
        logger.warning("Failed to load model from %s: %s — using heuristic.", model_path, exc)
        _model_cache["model"] = None
        return None


def reload_model() -> Any | None:
    """Force-reload the model from disk (e.g., after retraining)."""
    _model_cache.clear()
    return load_model()


def predict_with_model(features: dict[str, float]) -> float | None:
    """Run inference using the trained model.

    Returns predicted hours, or None if no model is available.
    """
    model = load_model()
    if model is None:
        return None

    # Build feature array in the expected column order.
    feature_array = np.array([[features.get(col, 0.0) for col in FEATURE_COLUMNS]])

    try:
        prediction = model.predict(feature_array)
        return float(prediction[0])
    except Exception as exc:
        logger.warning("Model inference failed: %s — falling back to heuristic.", exc)
        return None
