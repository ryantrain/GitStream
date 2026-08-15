"""API routes for PR queue depth and reviewer workload visibility."""

from fastapi import APIRouter, Depends, Query

from app.core.security import TenantContext, get_tenant_context
from app.schemas.prediction import QueueDepthResponse, ReviewerQueue
from app.services.historical_metrics import compute_queue_depth

router = APIRouter()


@router.get("/queue/depth", response_model=QueueDepthResponse)
async def get_queue_depth(
    tenant: TenantContext = Depends(get_tenant_context),
    repository: str | None = Query(default=None),
) -> QueueDepthResponse:
    """Get current PR queue depth with per-reviewer breakdown.

    Shows how many PRs are waiting for review and which reviewers are most loaded.
    Useful for load balancing and identifying review bottlenecks in real-time.
    """
    result = compute_queue_depth(
        tenant_id=tenant.tenant_id,
        repository=repository,
    )

    reviewer_queues = [
        ReviewerQueue(
            reviewer_id=rq["reviewer_id"],
            open_review_count=rq["open_review_count"],
            avg_wait_hours=rq["avg_wait_hours"],
            oldest_review_hours=rq["oldest_review_hours"],
        )
        for rq in result["reviewer_queues"]
    ]

    return QueueDepthResponse(
        tenant_id=tenant.tenant_id,
        repository=repository,
        total_open_prs=result["total_open_prs"],
        reviewer_queues=reviewer_queues,
    )
