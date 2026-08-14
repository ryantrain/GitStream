from fastapi import APIRouter, Depends

from app.core.security import TenantContext, get_tenant_context
from app.schemas.prediction import PullRequestEvent, IngestionAccepted
from app.services.ingestion_worker import process_pull_request_event

router = APIRouter()


@router.post("/ingestion/pr-event", response_model=IngestionAccepted)
async def ingest_pr_event(
    payload: PullRequestEvent,
    tenant: TenantContext = Depends(get_tenant_context),
) -> IngestionAccepted:
    process_pull_request_event(tenant_id=tenant.tenant_id, event=payload)
    return IngestionAccepted(status="accepted", tenant_id=tenant.tenant_id)
