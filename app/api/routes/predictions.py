from fastapi import APIRouter, Depends

from app.core.security import TenantContext, get_tenant_context
from app.schemas.prediction import PredictionRequest, PredictionResponse
from app.services.predictor import predict_time_to_merge_hours

router = APIRouter()


@router.post("/predictions/time-to-merge", response_model=PredictionResponse)
async def predict_time_to_merge(
    payload: PredictionRequest,
    tenant: TenantContext = Depends(get_tenant_context),
) -> PredictionResponse:
    result = predict_time_to_merge_hours(payload=payload, tenant_id=tenant.tenant_id)
    return PredictionResponse(**result)
