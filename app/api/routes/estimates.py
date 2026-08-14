from fastapi import APIRouter, Depends, HTTPException

from app.core.security import TenantContext, get_tenant_context
from app.schemas.estimates import (
    GithubHistoryEstimateRequest,
    GithubHistoryEstimateResponse,
)
from app.services.github_history import (
    GithubHistoryError,
    estimate_from_pr_history,
    fetch_closed_pr_history,
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
        prs = fetch_closed_pr_history(
            owner=payload.owner,
            repository=payload.repository,
            lookback_prs=payload.lookback_prs,
            github_token=payload.github_token,
        )
        estimate = estimate_from_pr_history(prs)
    except GithubHistoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return GithubHistoryEstimateResponse(
        owner=payload.owner,
        repository=payload.repository,
        **estimate,
    )
