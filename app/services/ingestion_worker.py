"""Ingestion worker: persists PR events to the database via background task queue."""

import json
from datetime import datetime, UTC

from app.db.models import PullRequestMetric, ReviewCycleMetric
from app.db.session import tenant_session
from app.schemas.prediction import MergeEvent, PullRequestEvent, ReviewCycleEvent


def process_pull_request_event(tenant_id: str, event: PullRequestEvent) -> None:
    """Persist a pull request event to the pull_request_metrics table.

    This function is called synchronously from the ingestion route. For high-throughput
    scenarios, wrap this with the Arq task queue (see app.tasks.worker).
    """
    # Compute observed_merge_hours if merged_at is provided
    observed_merge_hours: float | None = None
    if event.merged_at and event.created_at:
        delta = (event.merged_at - event.created_at).total_seconds() / 3600.0
        if delta >= 0:
            observed_merge_hours = round(delta, 2)

    with tenant_session(tenant_id) as session:
        metric = PullRequestMetric(
            tenant_id=tenant_id,
            pr_id=event.pr_id,
            repository=event.repository,
            author_id=event.author_id,
            created_at=event.created_at,
            # Size metrics
            lines_added=event.lines_added,
            lines_deleted=event.lines_deleted,
            files_changed=event.files_changed,
            # Review metrics
            reviewers_requested=event.reviewers_requested,
            time_to_first_review_hours=event.time_to_first_review_hours,
            review_rounds=event.review_rounds,
            reviewer_response_hours=event.reviewer_response_hours,
            approval_to_merge_hours=event.approval_to_merge_hours,
            is_cross_timezone=event.is_cross_timezone,
            comment_count=event.comment_count,
            # CI/Pipeline metrics
            ci_pass_rate=event.ci_pass_rate,
            ci_duration_minutes=event.ci_duration_minutes,
            ci_reruns=event.ci_reruns,
            # Author behavior metrics
            commit_count=event.commit_count,
            force_push_count=event.force_push_count,
            hours_since_last_push=event.hours_since_last_push,
            author_open_pr_count=event.author_open_pr_count,
            # PR composition metrics
            test_lines_added=event.test_lines_added,
            directories_touched=event.directories_touched,
            touches_critical_path=event.touches_critical_path,
            # Labels (stored as JSON string)
            labels=json.dumps(event.labels) if event.labels else None,
            # Merge outcome
            merged_at=event.merged_at,
            observed_merge_hours=observed_merge_hours,
        )
        session.merge(metric)
        session.commit()


def process_merge_event(tenant_id: str, event: MergeEvent) -> None:
    """Backfill observed_merge_hours when a PR merge event is received.

    This handles the case where the original PR event was ingested before the
    PR was merged, so observed_merge_hours was NULL.
    """
    with tenant_session(tenant_id) as session:
        existing = (
            session.query(PullRequestMetric)
            .filter(
                PullRequestMetric.tenant_id == tenant_id,
                PullRequestMetric.pr_id == event.pr_id,
            )
            .first()
        )

        if existing:
            created_at = event.created_at or existing.created_at
            delta_hours = (event.merged_at - created_at).total_seconds() / 3600.0
            existing.merged_at = event.merged_at
            existing.observed_merge_hours = round(max(delta_hours, 0.0), 2)
        else:
            # PR not yet ingested — create a minimal record with merge data
            if event.created_at:
                delta_hours = (event.merged_at - event.created_at).total_seconds() / 3600.0
                metric = PullRequestMetric(
                    tenant_id=tenant_id,
                    pr_id=event.pr_id,
                    repository=event.repository,
                    author_id="unknown",
                    created_at=event.created_at,
                    lines_added=0,
                    lines_deleted=0,
                    files_changed=1,
                    reviewers_requested=0,
                    merged_at=event.merged_at,
                    observed_merge_hours=round(max(delta_hours, 0.0), 2),
                )
                session.add(metric)

        session.commit()


def process_review_cycle_event(tenant_id: str, event: ReviewCycleEvent) -> None:
    """Persist an individual review cycle for granular review analytics."""
    wait_hours: float | None = None
    if event.review_requested_at and event.review_submitted_at:
        delta = (event.review_submitted_at - event.review_requested_at).total_seconds() / 3600.0
        wait_hours = round(max(delta, 0.0), 2)

    with tenant_session(tenant_id) as session:
        cycle = ReviewCycleMetric(
            tenant_id=tenant_id,
            pr_id=event.pr_id,
            repository=event.repository,
            cycle_number=event.cycle_number,
            reviewer_id=event.reviewer_id,
            review_requested_at=event.review_requested_at,
            review_submitted_at=event.review_submitted_at,
            review_state=event.review_state,
            wait_hours=wait_hours,
            created_at=datetime.now(UTC),
        )
        session.add(cycle)
        session.commit()


async def async_process_pull_request_event(ctx: dict, tenant_id: str, event_data: str) -> None:
    """Arq-compatible async task wrapper.

    Deserializes the event JSON and delegates to the synchronous writer.
    """
    event = PullRequestEvent(**json.loads(event_data))
    process_pull_request_event(tenant_id=tenant_id, event=event)


async def async_process_merge_event(ctx: dict, tenant_id: str, event_data: str) -> None:
    """Arq-compatible async task wrapper for merge events."""
    event = MergeEvent(**json.loads(event_data))
    process_merge_event(tenant_id=tenant_id, event=event)


async def async_process_review_cycle_event(ctx: dict, tenant_id: str, event_data: str) -> None:
    """Arq-compatible async task wrapper for review cycle events."""
    event = ReviewCycleEvent(**json.loads(event_data))
    process_review_cycle_event(tenant_id=tenant_id, event=event)
