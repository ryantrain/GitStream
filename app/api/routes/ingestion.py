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
    # Synchronous write for simplicity. For high-throughput, enqueue via Arq:
    # from arq.connections import create_pool, RedisSettings
    # pool = await create_pool(RedisSettings())
    # await pool.enqueue_job("async_process_pull_request_event", tenant.tenant_id, payload.model_dump_json())
    process_pull_request_event(tenant_id=tenant.tenant_id, event=payload)
    return IngestionAccepted(status="accepted", tenant_id=tenant.tenant_id)
