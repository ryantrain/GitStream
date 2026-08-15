"""Service to compute historical author and reviewer metrics from stored PR data.

These computed values replace the static defaults in PredictionRequest, giving
predictions real context about author velocity and reviewer workload.

Two deliberate choices run through this module:

* Central tendency uses the **median**, not the mean. Pull request durations
  have a long right tail (one PR left open over a holiday), and a mean lets a
  single observation dominate an author's or a team's baseline.
* Aggregation over timestamps happens **in Python**, not in SQL. The previous
  implementation used ``func.extract('epoch', ...)``, which is PostgreSQL-only
  and silently breaks on SQLite. The row counts involved (pending review cycles
  for one tenant) are small enough that this costs nothing.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func

from app.db.models import PullRequestMetric, ReviewCycleMetric
from app.db.session import tenant_session
from app.services.stats import robust_center

# Minimum observations before a computed baseline is trusted over the fallback.
MIN_AUTHOR_OBSERVATIONS = 2
MIN_TENANT_OBSERVATIONS = 3

# Fallback merge time when a tenant has no usable history at all.
DEFAULT_MERGE_HOURS = 24.0

# Reviewer load index bounds. 1.0 means balanced; the floor represents a team
# with genuine spare capacity and the ceiling stops a pathological ratio
# (30 open PRs against a single active reviewer) from dominating a prediction.
MIN_LOAD_INDEX = 0.1
MAX_LOAD_INDEX = 10.0
BALANCED_LOAD_INDEX = 1.0


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalise a database timestamp to an aware UTC datetime.

    SQLite discards timezone information even for ``DateTime(timezone=True)``
    columns, so values read back may be naive. Treating those as UTC keeps
    arithmetic against ``datetime.now(UTC)`` from raising.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def compute_author_merge_hours_detail(
    tenant_id: str,
    author_id: str,
    lookback_days: int = 90,
) -> tuple[float, str]:
    """Author merge-time baseline plus where the number came from.

    The source label lets the predictor score its own confidence honestly: a
    baseline backed by an author's own merges deserves more trust than the
    tenant average, which in turn beats the hardcoded default.

    Returns ``(hours, source)`` where source is ``"author"``, ``"tenant"`` or
    ``"default"``.
    """
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

    with tenant_session(tenant_id) as session:
        rows = (
            session.query(PullRequestMetric.observed_merge_hours)
            .filter(
                PullRequestMetric.tenant_id == tenant_id,
                PullRequestMetric.author_id == author_id,
                PullRequestMetric.observed_merge_hours.isnot(None),
                PullRequestMetric.merged_at.isnot(None),
                PullRequestMetric.merged_at >= cutoff,
            )
            .all()
        )

        values = [float(row.observed_merge_hours) for row in rows if row.observed_merge_hours is not None]

        if len(values) >= MIN_AUTHOR_OBSERVATIONS:
            return round(robust_center(values), 2), "author"

    return _compute_tenant_merge_hours_detail(tenant_id, cutoff)


def compute_author_avg_merge_hours(
    tenant_id: str,
    author_id: str,
    lookback_days: int = 90,
) -> float:
    """Compute a robust typical merge time for an author over recent history.

    Returns the median ``observed_merge_hours`` for the author's PRs merged
    within the lookback window. The window is keyed on ``merged_at`` so it
    describes recent delivery behaviour rather than recent PR creation.

    Falls back to the tenant-wide baseline, then to ``DEFAULT_MERGE_HOURS``,
    when there is not enough data.
    """
    hours, _source = compute_author_merge_hours_detail(tenant_id, author_id, lookback_days)
    return hours


def compute_reviewer_load_index(
    tenant_id: str,
    repository: str | None = None,
    lookback_days: int = 14,
) -> float:
    """Compute reviewer load index: ratio of open reviews to available reviewers.

    See ``compute_reviewer_load_index_detail`` for the semantics.
    """
    value, _source = compute_reviewer_load_index_detail(tenant_id, repository, lookback_days)
    return value


def compute_reviewer_load_index_detail(
    tenant_id: str,
    repository: str | None = None,
    lookback_days: int = 14,
) -> tuple[float, str]:
    """Reviewer load index plus where the number came from.

    A value of 1.0 means balanced load. >1.0 means reviewers are overloaded.
    <1.0 means reviewers have capacity.

    Calculation:
        load_index = (active open PRs awaiting review) / (distinct active reviewers)

    An empty queue returns ``MIN_LOAD_INDEX`` rather than 1.0. Reporting a team
    with nothing in flight as "balanced" removed the prediction credit those
    teams have actually earned, since the heuristic measures load relative to a
    1.0 baseline.

    Returns ``(value, source)`` where source is ``"measured"`` (real queue and
    reviewer counts), ``"capacity"`` (queue genuinely empty), ``"proxy"``
    (reviewer count inferred from author count) or ``"unknown"`` (no data, so
    ``BALANCED_LOAD_INDEX`` is returned as a neutral guess).
    """
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    source = "measured"

    with tenant_session(tenant_id) as session:
        # Count PRs still open (no observed_merge_hours) created in lookback window
        open_prs_query = session.query(func.count(PullRequestMetric.id)).filter(
            PullRequestMetric.tenant_id == tenant_id,
            PullRequestMetric.observed_merge_hours.is_(None),
            PullRequestMetric.merged_at.is_(None),
            PullRequestMetric.created_at >= cutoff,
        )
        if repository:
            open_prs_query = open_prs_query.filter(PullRequestMetric.repository == repository)
        open_pr_count = open_prs_query.scalar() or 0

        # Count distinct reviewers who have been active (reviewed something) recently
        active_reviewers_query = session.query(func.count(func.distinct(ReviewCycleMetric.reviewer_id))).filter(
            ReviewCycleMetric.tenant_id == tenant_id,
            ReviewCycleMetric.created_at >= cutoff,
        )
        if repository:
            active_reviewers_query = active_reviewers_query.filter(ReviewCycleMetric.repository == repository)
        active_reviewer_count = active_reviewers_query.scalar() or 0

        if active_reviewer_count == 0:
            # Fallback: estimate from distinct authors, assuming each also reviews.
            distinct_authors = (
                session.query(func.count(func.distinct(PullRequestMetric.author_id)))
                .filter(
                    PullRequestMetric.tenant_id == tenant_id,
                    PullRequestMetric.created_at >= cutoff,
                )
                .scalar()
                or 0
            )

            if distinct_authors == 0:
                # No reviewers and no authors: the data is unknown, not empty.
                return BALANCED_LOAD_INDEX, "unknown"

            active_reviewer_count = distinct_authors
            source = "proxy"

        if open_pr_count == 0:
            # Nothing queued: reviewers have full capacity.
            return MIN_LOAD_INDEX, "capacity"

        load_index = open_pr_count / active_reviewer_count
        clamped = round(min(max(load_index, MIN_LOAD_INDEX), MAX_LOAD_INDEX), 2)
        return clamped, source


def compute_reviewer_response_time(
    tenant_id: str,
    reviewer_id: str | None = None,
    lookback_days: int = 30,
) -> float | None:
    """Compute typical reviewer response time (hours) from review cycle data.

    If reviewer_id is provided, returns that reviewer's median.
    Otherwise returns the tenant-wide median. Returns None when there are too
    few observations to be meaningful.
    """
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

    with tenant_session(tenant_id) as session:
        query = session.query(ReviewCycleMetric.wait_hours).filter(
            ReviewCycleMetric.tenant_id == tenant_id,
            ReviewCycleMetric.wait_hours.isnot(None),
            ReviewCycleMetric.created_at >= cutoff,
        )

        if reviewer_id:
            query = query.filter(ReviewCycleMetric.reviewer_id == reviewer_id)

        values = [float(row.wait_hours) for row in query.all() if row.wait_hours is not None]

        if len(values) >= MIN_AUTHOR_OBSERVATIONS:
            return round(robust_center(values), 2)

    return None


def compute_queue_depth(
    tenant_id: str,
    repository: str | None = None,
) -> dict:
    """Compute current PR queue depth — total open PRs and per-reviewer breakdown.

    Wait times are derived in Python from ``review_requested_at`` so the query
    works on every SQL dialect, and so ``oldest_review_hours`` can be reported
    for real instead of being hardcoded to null.

    Returns:
        {
            "total_open_prs": int,
            "reviewer_queues": [
                {
                    "reviewer_id": str,
                    "open_review_count": int,
                    "avg_wait_hours": float | None,
                    "oldest_review_hours": float | None,
                }
            ]
        }
    """
    now = datetime.now(UTC)

    with tenant_session(tenant_id) as session:
        # Total open PRs (no merge timestamp)
        open_query = session.query(func.count(PullRequestMetric.id)).filter(
            PullRequestMetric.tenant_id == tenant_id,
            PullRequestMetric.observed_merge_hours.is_(None),
            PullRequestMetric.merged_at.is_(None),
        )
        if repository:
            open_query = open_query.filter(PullRequestMetric.repository == repository)
        total_open = open_query.scalar() or 0

        # Pending review cycles: requested but not yet submitted.
        pending_query = session.query(
            ReviewCycleMetric.reviewer_id,
            ReviewCycleMetric.review_requested_at,
        ).filter(
            ReviewCycleMetric.tenant_id == tenant_id,
            ReviewCycleMetric.review_submitted_at.is_(None),
            ReviewCycleMetric.review_requested_at.isnot(None),
        )
        if repository:
            pending_query = pending_query.filter(ReviewCycleMetric.repository == repository)

        waits_by_reviewer: dict[str, list[float]] = {}
        for row in pending_query.all():
            requested_at = _as_utc(row.review_requested_at)
            if requested_at is None:
                continue
            wait_hours = max((now - requested_at).total_seconds() / 3600.0, 0.0)
            waits_by_reviewer.setdefault(row.reviewer_id, []).append(wait_hours)

        reviewer_queues = []
        for reviewer_id, waits in waits_by_reviewer.items():
            # `is not None` rather than truthiness: a review requested seconds
            # ago has a legitimate wait of ~0.0 and must not report as "no data".
            avg_wait = sum(waits) / len(waits) if waits else None
            oldest_wait = max(waits) if waits else None
            reviewer_queues.append(
                {
                    "reviewer_id": reviewer_id,
                    "open_review_count": len(waits),
                    "avg_wait_hours": round(avg_wait, 2) if avg_wait is not None else None,
                    "oldest_review_hours": (round(oldest_wait, 2) if oldest_wait is not None else None),
                }
            )

        # Sort by load (most overloaded first), then by longest wait.
        reviewer_queues.sort(
            key=lambda r: (r["open_review_count"], r["oldest_review_hours"] or 0.0),
            reverse=True,
        )

        return {
            "total_open_prs": total_open,
            "reviewer_queues": reviewer_queues,
        }


def enrich_prediction_context(
    tenant_id: str,
    author_id: str,
    repository: str | None = None,
) -> dict:
    """Compute contextual features for prediction that the caller did not supply.

    Returns a dict with:
        - avg_author_merge_hours: float
        - reviewer_load_index: float
        - author_history_source: "author" | "tenant" | "default"
        - reviewer_load_source: "measured" | "capacity" | "proxy" | "unknown"

    The source labels travel with the values so the predictor can score its own
    confidence on whether these features are measured or guessed.
    """
    author_hours, author_source = compute_author_merge_hours_detail(tenant_id, author_id)
    load_index, load_source = compute_reviewer_load_index_detail(tenant_id, repository)

    return {
        "avg_author_merge_hours": author_hours,
        "reviewer_load_index": load_index,
        "author_history_source": author_source,
        "reviewer_load_source": load_source,
    }


def _compute_tenant_merge_hours_detail(tenant_id: str, cutoff: datetime) -> tuple[float, str]:
    """Robust tenant-wide merge time, used as the author-level fallback."""
    with tenant_session(tenant_id) as session:
        rows = (
            session.query(PullRequestMetric.observed_merge_hours)
            .filter(
                PullRequestMetric.tenant_id == tenant_id,
                PullRequestMetric.observed_merge_hours.isnot(None),
                PullRequestMetric.merged_at.isnot(None),
                PullRequestMetric.merged_at >= cutoff,
            )
            .all()
        )

        values = [float(row.observed_merge_hours) for row in rows if row.observed_merge_hours is not None]

        if len(values) >= MIN_TENANT_OBSERVATIONS:
            return round(robust_center(values), 2), "tenant"

    return DEFAULT_MERGE_HOURS, "default"


def _compute_tenant_avg_merge_hours(tenant_id: str, cutoff: datetime) -> float:
    """Backwards-compatible wrapper returning only the value."""
    hours, _source = _compute_tenant_merge_hours_detail(tenant_id, cutoff)
    return hours
