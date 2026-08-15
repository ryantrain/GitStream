import time

from fastapi import APIRouter, Depends

from app.api.routes.metrics import (
    PREDICTION_CONFIDENCE,
    PREDICTION_LATENCY,
    PREDICTIONS_TOTAL,
)
from app.core.security import TenantContext, get_tenant_context
from app.schemas.prediction import PredictionRequest, PredictionResponse
from app.services.predictor import predict_time_to_merge_hours

router = APIRouter()


@router.post("/predictions/time-to-merge", response_model=PredictionResponse)
async def predict_time_to_merge(
    payload: PredictionRequest,
    tenant: TenantContext = Depends(get_tenant_context),
) -> PredictionResponse:
    """Predict time-to-merge for a pull request.

    Auto-enriches missing contextual features (avg_author_merge_hours,
    reviewer_load_index) from historical data. Returns ranked factor
    contributions and a confidence score.
    """
    start = time.perf_counter()

    result = predict_time_to_merge_hours(payload=payload, tenant_id=tenant.tenant_id)

    elapsed = time.perf_counter() - start
    PREDICTION_LATENCY.observe(elapsed)
    PREDICTIONS_TOTAL.labels(tenant_id=tenant.tenant_id, risk_band=result["risk_band"]).inc()
    if result.get("confidence_score") is not None:
        PREDICTION_CONFIDENCE.observe(result["confidence_score"])

    return PredictionResponse(**result)
