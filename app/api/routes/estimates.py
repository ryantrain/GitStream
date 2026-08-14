from fastapi import APIRouter, Depends, HTTPException

from app.core.config import settings
from app.core.security import TenantContext, get_tenant_context
from app.schemas.estimates import (
    GithubHistoryEstimateRequest,
    GithubHistoryEstimateResponse,
)
from app.services.github_history import (
    GithubHistoryError,
    build_active_pr_estimates,
    estimate_from_pr_history,
    fetch_closed_pr_history,
    fetch_open_pull_requests,
)

router = APIRouter()


@router.post(
    "/estimates/github-history",
    response_model=GithubHistoryEstimateResponse,
)
def estimate_from_github_history(
    payload: GithubHistoryEstimateRequest,
    tenant: TenantContext = Depends(get_tenant_context),
) -> GithubHistoryEstimateResponse:
    _ = tenant

    try:
        token = payload.github_token or settings.github_token
        prs = fetch_closed_pr_history(
            owner=payload.owner,
            repository=payload.repository,
            lookback_prs=payload.lookback_prs,
            github_token=token,
        )
        history_estimate = estimate_from_pr_history(prs)
        open_prs = fetch_open_pull_requests(
            owner=payload.owner,
            repository=payload.repository,
            github_token=token,
        )
        active_estimates = build_active_pr_estimates(open_prs, history_estimate)
    except GithubHistoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return GithubHistoryEstimateResponse(
        owner=payload.owner,
        repository=payload.repository,
        **history_estimate,
        active_pr_count=len(active_estimates),
        active_pull_requests=active_estimates,
    )
