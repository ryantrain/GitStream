from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.github_history import (
    GithubHistoryError,
    estimate_from_pr_history,
    fetch_closed_pr_history,
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
    github_token: str = Form(default=""),
) -> HTMLResponse:
    result = None
    error = None

    try:
        lookback_prs = max(20, min(lookback_prs, 1000))
        prs = fetch_closed_pr_history(
            owner=owner,
            repository=repository,
            lookback_prs=lookback_prs,
            github_token=github_token or None,
        )
        estimate = estimate_from_pr_history(prs)
        result = {
            "owner": owner,
            "repository": repository,
            **estimate,
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
