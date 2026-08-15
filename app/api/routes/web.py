"""HTML front end for the repository estimator."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.services.estimation import run_repository_estimate
from app.services.github_client import GithubHistoryError

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

MIN_LOOKBACK_PRS = 20
MAX_LOOKBACK_PRS = 1000
DEFAULT_LOOKBACK_PRS = 200


def humanize_hours(value: float | int | None) -> str:
    """Render an hour count the way a person would say it.

    ``742.5`` is technically precise and practically unreadable; this returns
    ``31d hours`` -> ``31d 22h``. Sub-hour values are shown in minutes so a
    10-minute review does not display as ``0.17``.
    """
    if value is None:
        return "—"

    try:
        hours = float(value)
    except (TypeError, ValueError):
        return "—"

    if hours < 0:
        hours = 0.0

    if hours < 1:
        minutes = int(round(hours * 60))
        return f"{max(minutes, 1)}m"

    if hours < 24:
        whole = int(hours)
        minutes = int(round((hours - whole) * 60))
        if minutes >= 60:
            whole += 1
            minutes = 0
        if whole and minutes:
            return f"{whole}h {minutes}m"
        return f"{whole}h" if whole else f"{minutes}m"

    days = int(hours // 24)
    remainder = int(round(hours % 24))
    if remainder >= 24:
        days += 1
        remainder = 0
    if remainder:
        return f"{days}d {remainder}h"
    return f"{days}d"


def thousands(value: float | int | None) -> str:
    """Format a number with thousands separators."""
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"


templates.env.filters["humanize_hours"] = humanize_hours
templates.env.filters["thousands"] = thousands


def _base_context(
    owner: str = "",
    repository: str = "",
    lookback_prs: int = DEFAULT_LOOKBACK_PRS,
    include_drafts: bool = False,
) -> dict:
    return {
        "result": None,
        "error": None,
        "owner": owner,
        "repository": repository,
        "lookback_prs": lookback_prs,
        "include_drafts": include_drafts,
        "min_lookback_prs": MIN_LOOKBACK_PRS,
        "max_lookback_prs": MAX_LOOKBACK_PRS,
    }


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=_base_context(),
    )


@router.post("/estimate", response_class=HTMLResponse)
def estimate_form(
    request: Request,
    owner: str = Form(...),
    repository: str = Form(...),
    lookback_prs: int = Form(DEFAULT_LOOKBACK_PRS),
    include_drafts: str | None = Form(default=None),
) -> HTMLResponse:
    owner = owner.strip()
    repository = repository.strip()
    lookback_prs = max(MIN_LOOKBACK_PRS, min(lookback_prs, MAX_LOOKBACK_PRS))
    drafts_included = include_drafts is not None

    context = _base_context(owner, repository, lookback_prs, drafts_included)

    try:
        context["result"] = run_repository_estimate(
            owner=owner,
            repository=repository,
            lookback_prs=lookback_prs,
            github_token=settings.github_token,
            include_drafts=drafts_included,
        )
    except GithubHistoryError as exc:
        # Expected, user-actionable failures: bad repo name, exhausted quota,
        # unreachable API. The client already turns transport errors into this.
        context["error"] = str(exc)
    except Exception:
        # Anything else is a defect on our side. Log it with a traceback and show
        # the user something they can act on instead of a raw 500 page.
        logger.exception("Unexpected failure estimating %s/%s", owner, repository)
        context["error"] = (
            "Something went wrong while building the estimate. The details were "
            "logged. Try again, or reduce the number of PRs to sample."
        )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=context,
    )
