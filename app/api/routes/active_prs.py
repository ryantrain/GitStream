from fastapi import APIRouter, HTTPException

from app.services.github_history import (
    GithubHistoryError,
    build_active_pr_prediction_rows,
    fetch_active_prs,
)

router = APIRouter()


@router.get("/pull-requests/active")
def list_active_pull_requests(
    owner: str,
    repository: str,
    github_token: str | None = None,
) -> list[dict[str, object]]:
    try:
        prs = fetch_active_prs(owner=owner, repository=repository, github_token=github_token)
        return build_active_pr_prediction_rows(prs, baseline_hours=24.0)
    except GithubHistoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
