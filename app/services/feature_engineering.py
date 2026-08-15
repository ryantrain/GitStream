"""Feature engineering: transforms raw PR data into the feature vector used for prediction.

The feature vector is consumed by both the heuristic baseline and the trained ML model.
Features are grouped by signal category for clarity and maintainability.
"""

from app.schemas.prediction import PredictionRequest

# Labels that historically correlate with faster/slower merges
FAST_LABELS = {"hotfix", "bug", "patch", "critical", "urgent", "revert"}
SLOW_LABELS = {"breaking-change", "rfc", "needs-discussion", "wip", "do-not-merge"}


def build_feature_vector(payload: PredictionRequest) -> dict[str, float]:
    """Build the full feature vector from a prediction request.

    All features are numeric (float). Boolean signals are encoded as 0.0/1.0.
    Missing optional values use neutral defaults that don't bias the prediction.
    """
    # --- Size features ---
    change_size = payload.lines_added + payload.lines_deleted
    churn_per_file = change_size / max(payload.files_changed, 1)
    test_ratio = _compute_test_ratio(payload.test_lines_added, payload.lines_added)

    # --- Review features ---
    reviewers_requested = float(payload.reviewers_requested)
    time_to_first_review = _safe_float(payload.time_to_first_review_hours, 0.0)
    review_rounds = _safe_float(payload.review_rounds, 1.0)
    comment_count = _safe_float(payload.comment_count, 0.0)
    is_cross_timezone = 1.0 if payload.is_cross_timezone else 0.0

    # --- Contextual features (may be auto-computed from historical data) ---
    avg_author_merge_hours = _safe_float(payload.avg_author_merge_hours, 24.0)
    reviewer_load_index = _safe_float(payload.reviewer_load_index, 1.0)

    # --- CI features ---
    ci_pass_rate = _safe_float(payload.ci_pass_rate, 1.0)  # assume passing if unknown
    ci_duration_minutes = _safe_float(payload.ci_duration_minutes, 0.0)
    ci_reruns = _safe_float(payload.ci_reruns, 0.0)

    # --- Author behavior features ---
    commit_count = _safe_float(payload.commit_count, 1.0)
    force_push_count = _safe_float(payload.force_push_count, 0.0)
    author_open_pr_count = _safe_float(payload.author_open_pr_count, 1.0)

    # --- Composition features ---
    directories_touched = _safe_float(payload.directories_touched, 1.0)
    touches_critical_path = 1.0 if payload.touches_critical_path else 0.0

    # --- Label-derived features ---
    has_fast_label = 1.0 if any(l.lower() in FAST_LABELS for l in payload.labels) else 0.0
    has_slow_label = 1.0 if any(l.lower() in SLOW_LABELS for l in payload.labels) else 0.0

    return {
        # Size signals
        "change_size": float(change_size),
        "files_changed": float(payload.files_changed),
        "churn_per_file": float(churn_per_file),
        "test_ratio": test_ratio,
        # Review signals
        "reviewers_requested": reviewers_requested,
        "time_to_first_review_hours": time_to_first_review,
        "review_rounds": review_rounds,
        "comment_count": comment_count,
        "is_cross_timezone": is_cross_timezone,
        # Context signals
        "avg_author_merge_hours": avg_author_merge_hours,
        "reviewer_load_index": reviewer_load_index,
        # CI signals
        "ci_pass_rate": ci_pass_rate,
        "ci_duration_minutes": ci_duration_minutes,
        "ci_reruns": ci_reruns,
        # Author behavior signals
        "commit_count": commit_count,
        "force_push_count": force_push_count,
        "author_open_pr_count": author_open_pr_count,
        # Composition signals
        "directories_touched": directories_touched,
        "touches_critical_path": touches_critical_path,
        # Label signals
        "has_fast_label": has_fast_label,
        "has_slow_label": has_slow_label,
    }


def _safe_float(value: float | int | None, default: float) -> float:
    """Convert optional numeric to float with a neutral default."""
    if value is None:
        return default
    return float(value)


def _compute_test_ratio(test_lines: int | None, total_lines_added: int) -> float:
    """Compute ratio of test lines to total lines added.

    A higher ratio suggests better-tested code that reviewers may approve faster.
    """
    if test_lines is None or total_lines_added == 0:
        return 0.0
    return round(min(test_lines / max(total_lines_added, 1), 1.0), 4)
