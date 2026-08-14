from fastapi import APIRouter, Depends

from app.core.security import TenantContext, get_tenant_context
from app.schemas.prediction import BottleneckInsight
from app.services.predictor import compute_bottleneck_insights

router = APIRouter()


@router.get("/insights/bottlenecks", response_model=list[BottleneckInsight])
async def bottleneck_insights(
    tenant: TenantContext = Depends(get_tenant_context),
) -> list[BottleneckInsight]:
    return compute_bottleneck_insights(tenant_id=tenant.tenant_id)
