import time

from fastapi import APIRouter, Depends

from app.api.routes.metrics import (
    INGESTION_ERRORS_TOTAL,
    INGESTION_EVENTS_TOTAL,
    MERGE_EVENTS_TOTAL,
    REVIEW_CYCLES_TOTAL,
)
from app.core.security import TenantContext, get_tenant_context
from app.schemas.prediction import (
    IngestionAccepted,
    MergeEvent,
    PullRequestEvent,
    ReviewCycleEvent,
)
from app.services.ingestion_worker import (
    process_merge_event,
    process_pull_request_event,
    process_review_cycle_event,
)

router = APIRouter()


@router.post("/ingestion/pr-event", response_model=IngestionAccepted)
async def ingest_pr_event(
    payload: PullRequestEvent,
    tenant: TenantContext = Depends(get_tenant_context),
) -> IngestionAccepted:
    """Ingest a pull request event with all available metrics."""
    try:
        process_pull_request_event(tenant_id=tenant.tenant_id, event=payload)
        INGESTION_EVENTS_TOTAL.labels(tenant_id=tenant.tenant_id, event_type="pr_event").inc()
    except Exception as exc:
        INGESTION_ERRORS_TOTAL.labels(tenant_id=tenant.tenant_id, error_type=type(exc).__name__).inc()
        raise
    return IngestionAccepted(status="accepted", tenant_id=tenant.tenant_id)


@router.post("/ingestion/merge-event", response_model=IngestionAccepted)
async def ingest_merge_event(
    payload: MergeEvent,
    tenant: TenantContext = Depends(get_tenant_context),
) -> IngestionAccepted:
    """Ingest a merge event to backfill observed_merge_hours on existing PR records."""
    try:
        process_merge_event(tenant_id=tenant.tenant_id, event=payload)
        MERGE_EVENTS_TOTAL.labels(tenant_id=tenant.tenant_id).inc()
        INGESTION_EVENTS_TOTAL.labels(tenant_id=tenant.tenant_id, event_type="merge_event").inc()
    except Exception as exc:
        INGESTION_ERRORS_TOTAL.labels(tenant_id=tenant.tenant_id, error_type=type(exc).__name__).inc()
        raise
    return IngestionAccepted(status="accepted", tenant_id=tenant.tenant_id)


@router.post("/ingestion/review-cycle", response_model=IngestionAccepted)
async def ingest_review_cycle(
    payload: ReviewCycleEvent,
    tenant: TenantContext = Depends(get_tenant_context),
) -> IngestionAccepted:
    """Ingest an individual review cycle for granular review analytics."""
    try:
        process_review_cycle_event(tenant_id=tenant.tenant_id, event=payload)
        REVIEW_CYCLES_TOTAL.labels(tenant_id=tenant.tenant_id, review_state=payload.review_state or "unknown").inc()
        INGESTION_EVENTS_TOTAL.labels(tenant_id=tenant.tenant_id, event_type="review_cycle").inc()
    except Exception as exc:
        INGESTION_ERRORS_TOTAL.labels(tenant_id=tenant.tenant_id, error_type=type(exc).__name__).inc()
        raise
    return IngestionAccepted(status="accepted", tenant_id=tenant.tenant_id)
