"""API routes for review cycle analytics."""

from fastapi import APIRouter, Depends, Query

from app.core.security import TenantContext, get_tenant_context
from app.schemas.prediction import ReviewCycleDetail, ReviewCycleSummary
from app.db.models import ReviewCycleMetric
from app.db.session import tenant_session
from sqlalchemy import func

router = APIRouter()


@router.get("/review-cycles/{pr_id}", response_model=ReviewCycleSummary)
async def get_pr_review_cycles(
    pr_id: str,
    tenant: TenantContext = Depends(get_tenant_context),
) -> ReviewCycleSummary:
    """Get review cycle breakdown for a specific PR."""
    with tenant_session(tenant.tenant_id) as session:
        cycles = (
            session.query(ReviewCycleMetric)
            .filter(
                ReviewCycleMetric.tenant_id == tenant.tenant_id,
                ReviewCycleMetric.pr_id == pr_id,
            )
            .order_by(ReviewCycleMetric.cycle_number)
            .all()
        )

        cycle_details = [
            ReviewCycleDetail(
                cycle_number=c.cycle_number,
                reviewer_id=c.reviewer_id,
                review_state=c.review_state,
                wait_hours=c.wait_hours,
                review_requested_at=c.review_requested_at,
                review_submitted_at=c.review_submitted_at,
            )
            for c in cycles
        ]

        wait_hours_list = [c.wait_hours for c in cycles if c.wait_hours is not None]

        return ReviewCycleSummary(
            pr_id=pr_id,
            total_cycles=len(cycles),
            avg_wait_hours=round(sum(wait_hours_list) / len(wait_hours_list), 2) if wait_hours_list else None,
            max_wait_hours=round(max(wait_hours_list), 2) if wait_hours_list else None,
            cycles=cycle_details,
        )


@router.get("/review-cycles", response_model=list[ReviewCycleSummary])
async def get_review_cycle_stats(
    tenant: TenantContext = Depends(get_tenant_context),
    repository: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ReviewCycleSummary]:
    """Get review cycle summaries across recent PRs, ordered by slowest average wait."""
    with tenant_session(tenant.tenant_id) as session:
        query = (
            session.query(
                ReviewCycleMetric.pr_id,
                func.count(ReviewCycleMetric.id).label("total_cycles"),
                func.avg(ReviewCycleMetric.wait_hours).label("avg_wait"),
                func.max(ReviewCycleMetric.wait_hours).label("max_wait"),
            )
            .filter(
                ReviewCycleMetric.tenant_id == tenant.tenant_id,
                ReviewCycleMetric.wait_hours.isnot(None),
            )
        )

        if repository:
            query = query.filter(ReviewCycleMetric.repository == repository)

        results = (
            query.group_by(ReviewCycleMetric.pr_id)
            .order_by(func.avg(ReviewCycleMetric.wait_hours).desc())
            .limit(limit)
            .all()
        )

        return [
            ReviewCycleSummary(
                pr_id=row.pr_id,
                total_cycles=row.total_cycles,
                avg_wait_hours=round(float(row.avg_wait), 2) if row.avg_wait else None,
                max_wait_hours=round(float(row.max_wait), 2) if row.max_wait else None,
                cycles=[],  # Summary mode — no per-cycle detail
            )
            for row in results
        ]
