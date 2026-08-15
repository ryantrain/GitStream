from fastapi import APIRouter, Depends, HTTPException

from app.core.config import settings
from app.core.security import TenantContext, get_tenant_context
from app.schemas.estimates import (
    GithubHistoryEstimateRequest,
    GithubHistoryEstimateResponse,
)
from app.services.estimation import run_repository_estimate
from app.services.github_client import GithubHistoryError

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
        result = run_repository_estimate(
            owner=payload.owner,
            repository=payload.repository,
            lookback_prs=payload.lookback_prs,
            github_token=payload.github_token or settings.github_token,
            include_drafts=payload.include_drafts,
        )
    except GithubHistoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return GithubHistoryEstimateResponse(**result)
