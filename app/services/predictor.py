from app.schemas.prediction import BottleneckInsight, PredictionRequest
from app.services.feature_engineering import build_feature_vector


def _heuristic_predict(features: dict[str, float]) -> float:
    # Baseline heuristic while ML model training lifecycle is bootstrapped.
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
    features = build_feature_vector(payload)
    predicted = max(_heuristic_predict(features), 1.0)

    return {
        "tenant_id": tenant_id,
        "pr_id": payload.pr_id,
        "predicted_merge_hours": round(predicted, 2),
        "risk_band": _risk_band(predicted),
        "top_factors": [
            "reviewer_load_index",
            "reviewers_requested",
            "change_size",
        ],
    }


def compute_bottleneck_insights(tenant_id: str) -> list[BottleneckInsight]:
    # Placeholder analytics until historical PR metrics are connected.
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
