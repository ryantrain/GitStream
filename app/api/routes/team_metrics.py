"""API routes for team-level metrics and throughput analytics."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func

from app.core.security import TenantContext, get_tenant_context
from app.db.models import PullRequestMetric
from app.db.session import tenant_session
from app.schemas.prediction import TeamMetricsSummary
from app.services.stats import percentile, robust_center, trimmed_mean

router = APIRouter()

# A PR open longer than this with no merge is considered stale.
STALE_AFTER_DAYS = 7


def _round_or_none(value: float | None, digits: int) -> float | None:
    """Round a possibly-null aggregate without discarding a legitimate zero.

    The previous implementation used ``if value else None``, which reported a
    genuine 0% CI pass rate or a 0h time-to-first-review as "no data".
    """
    if value is None:
        return None
    return round(float(value), digits)


@router.get("/team-metrics", response_model=TeamMetricsSummary)
async def get_team_metrics(
    tenant: TenantContext = Depends(get_tenant_context),
    repository: str | None = Query(default=None),
    days: int = Query(default=7, ge=1, le=90, description="Lookback period in days"),
) -> TeamMetricsSummary:
    """Compute team-level metrics for the specified time window.

    Provides throughput (PRs merged/opened), timing (merge hours, first review),
    queue depth, and quality signals (CI pass rate, PR size).

    Throughput is measured on the event that defines it: ``prs_opened`` counts
    PRs *created* in the window (intake), while ``prs_merged`` counts PRs
    *merged* in the window regardless of when they were opened (output).

    Every quality and timing statistic describes the merged-in-window set, so
    the summary answers one question consistently: of the work that finished this
    period, how did it go? ``prs_stale`` and ``current_queue_depth`` are
    point-in-time snapshots of what is still outstanding.
    """
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)

    with tenant_session(tenant.tenant_id) as session:
        tenant_filter = [PullRequestMetric.tenant_id == tenant.tenant_id]
        if repository:
            tenant_filter.append(PullRequestMetric.repository == repository)

        # PRs opened in period — keyed on creation time.
        opened_filter = tenant_filter + [PullRequestMetric.created_at >= period_start]
        prs_opened = session.query(func.count(PullRequestMetric.id)).filter(*opened_filter).scalar() or 0

        # PRs merged in period — keyed on merge time, not creation time. A PR
        # opened three weeks ago and merged yesterday belongs in this week's
        # throughput; the old created_at filter silently excluded it.
        merged_filter = tenant_filter + [
            PullRequestMetric.merged_at.isnot(None),
            PullRequestMetric.merged_at >= period_start,
            PullRequestMetric.merged_at <= now,
        ]
        prs_merged = session.query(func.count(PullRequestMetric.id)).filter(*merged_filter).scalar() or 0

        # Stale PRs: still open and older than the staleness threshold.
        stale_cutoff = now - timedelta(days=STALE_AFTER_DAYS)
        stale_filter = tenant_filter + [
            PullRequestMetric.observed_merge_hours.is_(None),
            PullRequestMetric.merged_at.is_(None),
            PullRequestMetric.created_at < stale_cutoff,
        ]
        prs_stale = session.query(func.count(PullRequestMetric.id)).filter(*stale_filter).scalar() or 0

        # Duration distribution for PRs merged inside the window. Pulled as raw
        # values so we can report a robust centre and a tail percentile rather
        # than an outlier-sensitive mean alone.
        duration_filter = merged_filter + [PullRequestMetric.observed_merge_hours.isnot(None)]
        merge_hours_rows = session.query(PullRequestMetric.observed_merge_hours).filter(*duration_filter).all()

        merge_hours_values = [
            float(row.observed_merge_hours) for row in merge_hours_rows if row.observed_merge_hours is not None
        ]

        avg_merge: float | None = None
        med_merge: float | None = None
        p75_merge: float | None = None
        p90_merge: float | None = None
        if merge_hours_values:
            avg_merge = round(trimmed_mean(merge_hours_values), 2)
            med_merge = round(robust_center(merge_hours_values), 2)
            p75_merge = round(percentile(merge_hours_values, 0.75), 2)
            p90_merge = round(percentile(merge_hours_values, 0.90), 2)

        # Review timing and quality signals describe *completed* work, so they
        # share the merged-in-window population with the duration statistics
        # above. Scoping them to PRs opened in the window instead would mix two
        # different populations into one summary and report "no data" whenever a
        # team merges PRs faster than it opens them.
        avg_first_review = (
            session.query(func.avg(PullRequestMetric.time_to_first_review_hours))
            .filter(
                *merged_filter,
                PullRequestMetric.time_to_first_review_hours.isnot(None),
            )
            .scalar()
        )

        avg_rounds = (
            session.query(func.avg(PullRequestMetric.review_rounds))
            .filter(
                *merged_filter,
                PullRequestMetric.review_rounds.isnot(None),
            )
            .scalar()
        )

        avg_ci = (
            session.query(func.avg(PullRequestMetric.ci_pass_rate))
            .filter(
                *merged_filter,
                PullRequestMetric.ci_pass_rate.isnot(None),
            )
            .scalar()
        )

        avg_size = (
            session.query(func.avg(PullRequestMetric.lines_added + PullRequestMetric.lines_deleted))
            .filter(*merged_filter)
            .scalar()
        )

        # Queue depth is a point-in-time count of everything still open. It was
        # previously reported as both "avg" and "max" over the period, which it
        # has never been.
        open_filter = tenant_filter + [
            PullRequestMetric.observed_merge_hours.is_(None),
            PullRequestMetric.merged_at.is_(None),
        ]
        current_queue = session.query(func.count(PullRequestMetric.id)).filter(*open_filter).scalar() or 0

        return TeamMetricsSummary(
            tenant_id=tenant.tenant_id,
            repository=repository,
            period_start=period_start,
            period_end=now,
            prs_merged=prs_merged,
            prs_opened=prs_opened,
            prs_stale=prs_stale,
            merged_sample_size=len(merge_hours_values),
            avg_merge_hours=avg_merge,
            median_merge_hours=med_merge,
            p75_merge_hours=p75_merge,
            p90_merge_hours=p90_merge,
            avg_time_to_first_review_hours=_round_or_none(avg_first_review, 2),
            avg_review_rounds=_round_or_none(avg_rounds, 2),
            current_queue_depth=current_queue,
            avg_ci_pass_rate=_round_or_none(avg_ci, 3),
            avg_pr_size_lines=_round_or_none(avg_size, 1),
        )
