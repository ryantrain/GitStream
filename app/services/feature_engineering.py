from app.schemas.prediction import PredictionRequest


def build_feature_vector(payload: PredictionRequest) -> dict[str, float]:
    change_size = payload.lines_added + payload.lines_deleted
    churn_per_file = change_size / max(payload.files_changed, 1)

    return {
        "change_size": float(change_size),
        "files_changed": float(payload.files_changed),
        "reviewers_requested": float(payload.reviewers_requested),
        "avg_author_merge_hours": float(payload.avg_author_merge_hours),
        "reviewer_load_index": float(payload.reviewer_load_index),
        "churn_per_file": float(churn_per_file),
    }
