"""Prediction service: time-to-merge forecasting and bottleneck insights."""

import json
from datetime import datetime, UTC

from app.db.models import PredictionLog
from app.db.session import tenant_session
from app.schemas.prediction import BottleneckInsight, PredictionRequest
from app.services.feature_engineering import build_feature_vector
from app.services.model_loader import predict_with_model


def _heuristic_predict(features: dict[str, float]) -> float:
    """Baseline heuristic while ML model training lifecycle is bootstrapped."""
    return (
        2.5
        + 0.02 * features["change_size"]
        + 1.1 * features["reviewers_requested"]
        + 4.0 * (features["reviewer_load_index"] - 1.0)
        + 0.15 * features["avg_author_merge_hours"]
    )


def _risk_band(hours: float) -> str:
    if hours < 12:
        return "low"
    if hours < 36:
        return "medium"
    return "high"


def predict_time_to_merge_hours(payload: PredictionRequest, tenant_id: str) -> dict:
    """Generate a merge-time prediction and persist it to prediction_logs.

    Uses the trained ML model if available, otherwise falls back to heuristic.
    """
    features = build_feature_vector(payload)

    # Try the trained ML model first; fall back to heuristic.
    ml_prediction = predict_with_model(features)
    if ml_prediction is not None:
        predicted = max(ml_prediction, 1.0)
    else:
        predicted = max(_heuristic_predict(features), 1.0)

    risk = _risk_band(predicted)
    top_factors = [
        "reviewer_load_index",
        "reviewers_requested",
        "change_size",
    ]

    # Persist prediction to the database for accuracy tracking and drift detection.
    with tenant_session(tenant_id) as session:
        log_entry = PredictionLog(
            tenant_id=tenant_id,
            pr_id=payload.pr_id,
            predicted_merge_hours=round(predicted, 2),
            risk_band=risk,
            top_factors=json.dumps(top_factors),
            created_at=datetime.now(UTC),
        )
        session.add(log_entry)
        session.commit()

    return {
        "tenant_id": tenant_id,
        "pr_id": payload.pr_id,
        "predicted_merge_hours": round(predicted, 2),
        "risk_band": risk,
        "top_factors": top_factors,
    }


def compute_bottleneck_insights(tenant_id: str) -> list[BottleneckInsight]:
    """Compute bottleneck insights from actual PR metrics in the database.

    Falls back to generic recommendations when insufficient data exists.
    """
    from sqlalchemy import func
    from app.db.models import PullRequestMetric

    with tenant_session(tenant_id) as session:
        # Check if we have enough data to compute real insights
        row_count = session.query(func.count(PullRequestMetric.id)).filter(
            PullRequestMetric.tenant_id == tenant_id,
            PullRequestMetric.observed_merge_hours.isnot(None),
        ).scalar() or 0

        if row_count < 5:
            # Not enough data — return generic recommendations
            return [
                BottleneckInsight(
                    factor="reviewers_requested",
                    impact_hours=24.0,
                    recommendation=(
                        "Review whether requiring a third reviewer improves quality "
                        "for low-risk pull requests."
                    ),
                ),
                BottleneckInsight(
                    factor="reviewer_load_index",
                    impact_hours=12.0,
                    recommendation=(
                        "Balance reviewer assignment across the team to reduce queue time."
                    ),
                ),
            ]

        # --- Real insights from aggregated metrics ---

        insights: list[BottleneckInsight] = []

        # Insight 1: Large PRs take significantly longer
        avg_hours_large = session.query(func.avg(PullRequestMetric.observed_merge_hours)).filter(
            PullRequestMetric.tenant_id == tenant_id,
            PullRequestMetric.observed_merge_hours.isnot(None),
            (PullRequestMetric.lines_added + PullRequestMetric.lines_deleted) > 500,
        ).scalar()

        avg_hours_small = session.query(func.avg(PullRequestMetric.observed_merge_hours)).filter(
            PullRequestMetric.tenant_id == tenant_id,
            PullRequestMetric.observed_merge_hours.isnot(None),
            (PullRequestMetric.lines_added + PullRequestMetric.lines_deleted) <= 500,
        ).scalar()

        if avg_hours_large and avg_hours_small and avg_hours_large > avg_hours_small:
            impact = round(avg_hours_large - avg_hours_small, 1)
            insights.append(BottleneckInsight(
                factor="change_size",
                impact_hours=impact,
                recommendation=(
                    f"Large PRs (>500 lines) take {impact:.0f}h longer on average. "
                    "Consider breaking work into smaller, focused pull requests."
                ),
            ))

        # Insight 2: High reviewer count correlates with slower merges
        avg_hours_many_reviewers = session.query(
            func.avg(PullRequestMetric.observed_merge_hours)
        ).filter(
            PullRequestMetric.tenant_id == tenant_id,
            PullRequestMetric.observed_merge_hours.isnot(None),
            PullRequestMetric.reviewers_requested >= 3,
        ).scalar()

        avg_hours_few_reviewers = session.query(
            func.avg(PullRequestMetric.observed_merge_hours)
        ).filter(
            PullRequestMetric.tenant_id == tenant_id,
            PullRequestMetric.observed_merge_hours.isnot(None),
            PullRequestMetric.reviewers_requested < 3,
        ).scalar()

        if avg_hours_many_reviewers and avg_hours_few_reviewers and avg_hours_many_reviewers > avg_hours_few_reviewers:
            impact = round(avg_hours_many_reviewers - avg_hours_few_reviewers, 1)
            insights.append(BottleneckInsight(
                factor="reviewers_requested",
                impact_hours=impact,
                recommendation=(
                    f"PRs with 3+ reviewers take {impact:.0f}h longer. "
                    "Evaluate if all reviewers are necessary for low-risk changes."
                ),
            ))

        # Insight 3: Repeat authors with slow merge patterns
        slowest_author = session.query(
            PullRequestMetric.author_id,
            func.avg(PullRequestMetric.observed_merge_hours).label("avg_hours"),
        ).filter(
            PullRequestMetric.tenant_id == tenant_id,
            PullRequestMetric.observed_merge_hours.isnot(None),
        ).group_by(
            PullRequestMetric.author_id,
        ).having(
            func.count(PullRequestMetric.id) >= 3,
        ).order_by(
            func.avg(PullRequestMetric.observed_merge_hours).desc(),
        ).first()

        if slowest_author and slowest_author.avg_hours:
            insights.append(BottleneckInsight(
                factor="author_merge_pattern",
                impact_hours=round(slowest_author.avg_hours, 1),
                recommendation=(
                    f"Author '{slowest_author.author_id}' averages "
                    f"{slowest_author.avg_hours:.0f}h to merge. "
                    "Consider pairing or pre-review discussions to reduce cycle time."
                ),
            ))

        # If we computed no specific insights, return a generic one
        if not insights:
            insights.append(BottleneckInsight(
                factor="general",
                impact_hours=0.0,
                recommendation="Insufficient variation in data to identify specific bottlenecks.",
            ))

        return insights
