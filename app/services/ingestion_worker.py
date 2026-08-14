"""Ingestion worker: persists PR events to the database via background task queue."""

import json
from datetime import datetime, UTC

from app.db.models import PullRequestMetric
from app.db.session import tenant_session
from app.schemas.prediction import PullRequestEvent


def process_pull_request_event(tenant_id: str, event: PullRequestEvent) -> None:
    """Persist a pull request event to the pull_request_metrics table.

    This function is called synchronously from the ingestion route. For high-throughput
    scenarios, wrap this with the Arq task queue (see app.tasks.worker).
    """
    with tenant_session(tenant_id) as session:
        metric = PullRequestMetric(
            tenant_id=tenant_id,
            pr_id=event.pr_id,
            repository=event.repository,
            author_id=event.author_id,
            created_at=event.created_at,
            lines_added=event.lines_added,
            lines_deleted=event.lines_deleted,
            files_changed=event.files_changed,
            reviewers_requested=event.reviewers_requested,
            observed_merge_hours=None,
        )
        session.merge(metric)
        session.commit()


async def async_process_pull_request_event(ctx: dict, tenant_id: str, event_data: str) -> None:
    """Arq-compatible async task wrapper.

    Deserializes the event JSON and delegates to the synchronous writer.
    """
    event = PullRequestEvent(**json.loads(event_data))
    process_pull_request_event(tenant_id=tenant_id, event=event)
