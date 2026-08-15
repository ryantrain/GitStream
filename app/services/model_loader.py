"""ML model loading and inference for time-to-merge prediction.

Supports loading trained models (joblib format) from disk. Falls back to the
heuristic predictor when no trained model is available.

Three properties this module is responsible for:

* **Full feature coverage.** ``FEATURE_COLUMNS`` previously listed six columns,
  so everything ``build_feature_vector`` computed beyond size and reviewer count
  — CI health, review rounds, labels, cross-timezone, critical path, test ratio —
  was discarded before inference.
* **Correct neutral defaults.** A missing feature used to become ``0.0``, which
  is wrong for ``ci_pass_rate`` (neutral is 1.0, meaning "passing") and
  ``avg_author_merge_hours`` (neutral is the 24h baseline). Zero there told the
  model "CI is failing and this author merges instantly".
* **Safe cache behaviour.** The cache is guarded by a lock and invalidated when
  the artifact's mtime changes, so a retrained model is picked up without an
  explicit reload and concurrent requests cannot race on a half-populated cache.
"""

import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)

# Canonical feature order for the trained model input vector. Must stay in sync
# with the keys returned by ``feature_engineering.build_feature_vector``. When a
# loaded artifact declares its own order, that order wins (see _resolve_order).
FEATURE_COLUMNS = [
    # Size
    "change_size",
    "files_changed",
    "churn_per_file",
    "test_ratio",
    # Review
    "reviewers_requested",
    "time_to_first_review_hours",
    "review_rounds",
    "comment_count",
    "is_cross_timezone",
    # Context
    "avg_author_merge_hours",
    "reviewer_load_index",
    # CI
    "ci_pass_rate",
    "ci_duration_minutes",
    "ci_reruns",
    # Author behaviour
    "commit_count",
    "force_push_count",
    "author_open_pr_count",
    # Composition
    "directories_touched",
    "touches_critical_path",
    # Labels
    "has_fast_label",
    "has_slow_label",
]

# Neutral value per feature, used when a caller omits one. These mirror the
# defaults in feature_engineering so heuristic and model paths agree.
FEATURE_DEFAULTS: dict[str, float] = {
    "change_size": 0.0,
    "files_changed": 1.0,
    "churn_per_file": 0.0,
    "test_ratio": 0.0,
    "reviewers_requested": 1.0,
    "time_to_first_review_hours": 0.0,
    "review_rounds": 1.0,
    "comment_count": 0.0,
    "is_cross_timezone": 0.0,
    "avg_author_merge_hours": 24.0,
    "reviewer_load_index": 1.0,
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

_model_cache: dict[str, Any] = {}
_cache_lock = threading.RLock()


def _get_model_path() -> Path:
    """Resolve the path to the trained model artifact."""
    return Path(getattr(settings, "model_path", "models/merge_time_model.joblib"))


def _current_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _declared_feature_order(model: Any) -> list[str] | None:
    """Read the feature order the artifact was trained with, if it declares one.

    scikit-learn estimators fitted on a DataFrame expose ``feature_names_in_``.
    Honouring it means a model retrained with a different column order cannot
    silently receive misaligned inputs. Anything that is not a clean sequence of
    strings is ignored rather than trusted.
    """
    declared = getattr(model, "feature_names_in_", None)
    if declared is None:
        return None

    try:
        names = [str(name) for name in declared]
    except TypeError:
        return None

    if not names or not all(isinstance(name, str) and name for name in names):
        return None

    unknown = [name for name in names if name not in FEATURE_DEFAULTS]
    if unknown:
        logger.warning(
            "Model declares features not produced by the feature builder: %s. They will be filled with 0.0.",
            ", ".join(sorted(unknown)),
        )

    missing = [name for name in FEATURE_COLUMNS if name not in names]
    if missing:
        logger.info(
            "Model was trained without these available features: %s",
            ", ".join(missing),
        )

    return names


def _is_cache_stale(model_path: Path) -> bool:
    """Whether the cached artifact no longer matches what is on disk.

    Returns False when no mtime was recorded, so a cache populated directly
    (in tests, or by ``reload_model``) is honoured rather than discarded.
    """
    recorded = _model_cache.get("mtime")
    if recorded is None:
        return False
    return bool(_current_mtime(model_path) != recorded)


def load_model() -> Any | None:
    """Load the trained model from disk, caching it in memory.

    Returns None if no model file exists (triggering heuristic fallback).
    The cached artifact is discarded automatically when the file on disk changes.
    """
    model_path = _get_model_path()

    with _cache_lock:
        if "model" in _model_cache and not _is_cache_stale(model_path):
            return _model_cache["model"]

        if not model_path.exists():
            logger.info("No trained model found at %s — using heuristic fallback.", model_path)
            _model_cache["model"] = None
            _model_cache["feature_order"] = None
            _model_cache["mtime"] = None
            return None

        mtime = _current_mtime(model_path)

        try:
            import joblib

            model = joblib.load(model_path)
        except Exception as exc:
            logger.warning("Failed to load model from %s: %s — using heuristic.", model_path, exc)
            _model_cache["model"] = None
            _model_cache["feature_order"] = None
            _model_cache["mtime"] = mtime
            return None

        _model_cache["model"] = model
        _model_cache["feature_order"] = _declared_feature_order(model)
        _model_cache["mtime"] = mtime
        logger.info("Loaded trained model from %s", model_path)
        return model


def reload_model() -> Any | None:
    """Force-reload the model from disk (e.g., after retraining)."""
    with _cache_lock:
        _model_cache.clear()
        return load_model()


def model_is_available() -> bool:
    """Whether a trained model will serve the next prediction.

    Used by the predictor to report confidence honestly: a heuristic fallback
    deserves a lower confidence score than a fitted model.
    """
    return load_model() is not None


def build_model_input(features: dict[str, float]) -> tuple[np.ndarray, list[str]]:
    """Assemble the inference array in the order the active model expects."""
    order = _model_cache.get("feature_order") or FEATURE_COLUMNS
    row = [float(features.get(column, FEATURE_DEFAULTS.get(column, 0.0))) for column in order]
    return np.array([row]), order


def predict_with_model(features: dict[str, float]) -> float | None:
    """Run inference using the trained model.

    Returns predicted hours, or None if no model is available or inference fails.
    """
    model = load_model()
    if model is None:
        return None

    feature_array, _order = build_model_input(features)

    try:
        prediction = model.predict(feature_array)
        value = float(prediction[0])
    except Exception as exc:
        logger.warning("Model inference failed: %s — falling back to heuristic.", exc)
        return None

    if not np.isfinite(value):
        logger.warning("Model returned a non-finite prediction — falling back.")
        return None

    return value
