from pydantic import BaseModel, Field


class GithubHistoryEstimateRequest(BaseModel):
    owner: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    lookback_prs: int = Field(default=200, ge=20, le=1000)
    github_token: str | None = Field(default=None)


class GithubHistoryEstimateResponse(BaseModel):
    owner: str
    repository: str
    merged_pr_count: int
    sample_window_pr_count: int
    average_merge_hours: float
    median_merge_hours: float
    p75_merge_hours: float
    p90_merge_hours: float
    estimate_next_pr_hours: float
    risk_band: str
    active_pr_count: int
    active_pull_requests: list["ActivePullRequestEstimate"]


class ActivePullRequestEstimate(BaseModel):
    number: int
    title: str
    author: str
    html_url: str
    created_at: str
    age_hours: float
    additions: int
    deletions: int
    changed_files: int
    requested_reviewers: int
    is_draft: bool
    review_effort_hours: float
    historical_baseline_hours: float
    estimated_total_merge_hours: float
    estimated_remaining_hours: float
    risk_band: str
