"""Orchestrates a full repository estimate.

Both the JSON API (``/estimates/github-history``) and the HTML page (``/estimate``)
run the same pipeline: fetch closed history, derive duration statistics, fetch
open PRs, forecast each one. Keeping that sequence in one place means the page
and the API can never disagree about the numbers.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.services.github_client import (
    GithubHistoryError,
    PullRequestBatch,
    fetch_closed_pr_history,
    fetch_open_pull_requests,
)
from app.services.github_history import (
    build_active_pr_estimates,
    estimate_from_pr_history,
)

logger = logging.getLogger(__name__)

# Ceiling on how many open PRs we forecast in one run.
OPEN_PR_LIMIT = 100


def _as_batch(value: PullRequestBatch | list[dict[str, Any]]) -> PullRequestBatch:
    """Accept either a batch or a bare list.

    The fetchers return ``PullRequestBatch``; tolerating a plain list keeps this
    usable from scripts and keeps test doubles simple.
    """
    if isinstance(value, PullRequestBatch):
        return value
    return PullRequestBatch(items=list(value))


def run_repository_estimate(
    owner: str,
    repository: str,
    lookback_prs: int = 200,
    github_token: str | None = None,
    include_drafts: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Produce the complete estimate payload for one repository.

    Raises ``GithubHistoryError`` for any user-actionable failure (bad repo name,
    exhausted quota, unreachable API); the caller decides how to present it.
    """
    now_utc = now or datetime.now(UTC)

    closed_batch = _as_batch(
        fetch_closed_pr_history(
            owner=owner,
            repository=repository,
            lookback_prs=lookback_prs,
            github_token=github_token,
        )
    )

    history_estimate = estimate_from_pr_history(closed_batch.items, now=now_utc)

    open_batch = _as_batch(
        fetch_open_pull_requests(
            owner=owner,
            repository=repository,
            github_token=github_token,
            limit=OPEN_PR_LIMIT,
        )
    )

    active_estimates = build_active_pr_estimates(
        open_batch.items,
        history_estimate,
        now=now_utc,
        include_drafts=include_drafts,
    )

    draft_count = sum(1 for pr in active_estimates if pr["is_draft"])
    overdue_count = sum(1 for pr in active_estimates if pr["is_overdue"])
    ready_estimates = [pr for pr in active_estimates if not pr["is_draft"]]

    # Total outstanding reviewer time for the ready queue — the number a lead
    # needs in order to decide whether the queue is clearable this week.
    total_review_effort = round(sum(pr["review_effort_hours"] for pr in ready_estimates), 2)

    rate_limit = open_batch.rate_limit or closed_batch.rate_limit

    return {
        "owner": owner,
        "repository": repository,
        **history_estimate,
        "active_pr_count": len(active_estimates),
        "ready_pr_count": len(ready_estimates),
        "draft_pr_count": draft_count,
        "overdue_pr_count": overdue_count,
        "total_review_effort_hours": total_review_effort,
        "active_pull_requests": active_estimates,
        # Provenance so the UI never presents a capped count as the whole truth.
        "open_pr_total_available": open_batch.total_available,
        "open_prs_truncated": open_batch.truncated,
        "open_pr_limit": OPEN_PR_LIMIT,
        "file_details_available": open_batch.file_details_available,
        "fetched_at": now_utc.isoformat(),
        "rate_limit_remaining": rate_limit.remaining if rate_limit else None,
        "rate_limit_limit": rate_limit.limit if rate_limit else None,
        "rate_limit_reset_at": (rate_limit.reset_at.isoformat() if rate_limit and rate_limit.reset_at else None),
    }


__all__ = ["GithubHistoryError", "run_repository_estimate", "OPEN_PR_LIMIT"]
