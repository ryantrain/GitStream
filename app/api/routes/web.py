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


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return HTMLResponse(
        """
        <html>
            <head>
                <title>GitStream Dashboard</title>
                <style>
                    body { font-family: Arial, sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }
                    .card { background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 20px; margin-top: 20px; max-width: 900px; }
                    .meta { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
                    .badge { background: #f97316; color: white; padding: 6px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; }
                    .green { background: #16a34a; }
                    .red { background: #dc2626; }
                    input, button, select { display: block; width: 100%; margin-top: 10px; padding: 10px 12px; border-radius: 8px; border: 1px solid #475569; }
                    button { background: #22c55e; color: #04130b; border: none; font-weight: 700; cursor: pointer; }
                    a { color: #7dd3fc; }
                    .grid { display: grid; gap: 16px; }
                </style>
            </head>
            <body>
                <h1>GitStream Dashboard</h1>
                <div class="card">
                    <h2>Active PR Alerts</h2>
                    <input type="text" placeholder="Search PRs" />
                    <button type="button">Refresh alerts</button>
                    <div class="grid" style="margin-top: 20px;">
                        <div class="card">
                            <div class="meta">
                                <div>
                                    <strong>Refactor queue worker</strong>
                                    <div>Author: alice</div>
                                </div>
                                <span class="badge red">Red &gt;24h</span>
                            </div>
                            <p>Predicted merge delay: 38.2h</p>
                            <p>Additions: 180 | Deletions: 80 | Files changed: 6</p>
                        </div>
                        <div class="card">
                            <div class="meta">
                                <div>
                                    <strong>Trim CI pipeline time</strong>
                                    <div>Author: morgan</div>
                                </div>
                                <span class="badge green">Green &lt;24h</span>
                            </div>
                            <p>Predicted merge delay: 14.8h</p>
                            <p>Additions: 64 | Deletions: 22 | Files changed: 3</p>
                        </div>
                    </div>
                    <p><a href="/dashboard/settings">Repository Settings</a></p>
                </div>
            </body>
        </html>
        """
    )


@router.get("/dashboard/settings", response_class=HTMLResponse)
def repository_settings(request: Request) -> HTMLResponse:
    return HTMLResponse(
        """
        <html>
            <head>
                <title>GitStream Repository Settings</title>
                <style>
                    body { font-family: Arial, sans-serif; background: #020817; color: #f8fafc; padding: 24px; }
                    .card { background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 20px; max-width: 700px; }
                    label { display: block; margin-top: 16px; font-weight: 600; }
                    input, button { width: 100%; margin-top: 8px; padding: 10px 12px; border-radius: 8px; border: 1px solid #475569; }
                    button { background: #38bdf8; color: #082f49; border: none; font-weight: 700; cursor: pointer; }
                    a { color: #7dd3fc; }
                </style>
            </head>
            <body>
                <div class="card">
                    <h1>Repository registration</h1>
                    <form>
                        <label>Repository URL</label>
                        <input type="url" value="https://github.com/owner/repo" />

                        <label>GitHub Personal Access Token</label>
                        <input type="password" placeholder="Optional" />

                        <label>Organization ID</label>
                        <input type="text" value="acme-platform" />

                        <label>Webhook mode</label>
                        <select style="width: 100%; margin-top: 8px; padding: 10px 12px; border-radius: 8px; border: 1px solid #475569;">
                            <option>Auto-install Webhook via GitHub API</option>
                            <option>Manual Webhook Setup</option>
                        </select>

                        <button type="submit">Register repository</button>
                    </form>
                    <p><a href="/dashboard">Back to dashboard</a></p>
                </div>
            </body>
        </html>
        """
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
