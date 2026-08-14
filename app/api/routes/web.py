from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.services.github_history import (
    GithubHistoryError,
    build_active_pr_estimates,
    estimate_from_pr_history,
    fetch_closed_pr_history,
    fetch_open_pull_requests,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "result": None,
            "error": None,
            "owner": "",
            "repository": "",
            "lookback_prs": 200,
        },
    )


@router.post("/estimate", response_class=HTMLResponse)
def estimate_form(
    request: Request,
    owner: str = Form(...),
    repository: str = Form(...),
    lookback_prs: int = Form(200),
) -> HTMLResponse:
    result = None
    error = None

    try:
        lookback_prs = max(20, min(lookback_prs, 1000))
        token = settings.github_token
        prs = fetch_closed_pr_history(
            owner=owner,
            repository=repository,
            lookback_prs=lookback_prs,
            github_token=token,
        )
        history_estimate = estimate_from_pr_history(prs)
        open_prs = fetch_open_pull_requests(
            owner=owner,
            repository=repository,
            github_token=token,
        )
        active_estimates = build_active_pr_estimates(open_prs, history_estimate)
        result = {
            "owner": owner,
            "repository": repository,
            **history_estimate,
            "active_pr_count": len(active_estimates),
            "active_pull_requests": active_estimates,
        }
    except GithubHistoryError as exc:
        error = str(exc)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "result": result,
            "error": error,
            "owner": owner,
            "repository": repository,
            "lookback_prs": lookback_prs,
        },
    )
