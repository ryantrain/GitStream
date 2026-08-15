from pydantic import BaseModel, Field


class GithubHistoryEstimateRequest(BaseModel):
    owner: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    lookback_prs: int = Field(default=200, ge=20, le=1000)
    github_token: str | None = Field(default=None)
    include_drafts: bool = Field(
        default=True,
        description="Include draft PRs in the active queue. Drafts are not yet "
        "awaiting review, so excluding them gives a truer review backlog.",
    )


class SizeBucketStats(BaseModel):
    """Duration statistics for historical PRs of a similar size."""

    median_hours: float
    p75_hours: float
    p90_hours: float | None = None
    count: int


class RiskThresholds(BaseModel):
    """Cut points used to band a projection, in hours.

    Published so the UI can explain what "high risk" means for this repository
    rather than showing an unexplained label.
    """

    low_max: float = Field(description="At or below this is typical for this repo.")
    medium_max: float = Field(description="Above this is in the repo's own tail.")


class GithubHistoryEstimateResponse(BaseModel):
    owner: str
    repository: str

    # --- Sample provenance ---
    merged_pr_count: int
    sample_window_pr_count: int
    closed_unmerged_pr_count: int = 0
    excluded_stale_pr_count: int = 0
    history_window_days: int = 180
    history_window_applied: bool = True
    sample_is_reliable: bool = False
    min_reliable_sample: int = 20
    duration_includes_draft_time: bool = True
    fetched_at: str | None = None

    # --- Duration distribution ---
    average_merge_hours: float = Field(
        description="Trimmed mean: 5% removed from each tail so one abandoned PR cannot dominate the average."
    )
    raw_average_merge_hours: float | None = None
    median_merge_hours: float
    p75_merge_hours: float
    p90_merge_hours: float
    estimate_next_pr_hours: float
    risk_band: str
    risk_thresholds: RiskThresholds | None = None
    size_bucket_stats: dict[str, SizeBucketStats] = Field(default_factory=dict)

    # --- Active queue ---
    active_pr_count: int
    ready_pr_count: int = 0
    draft_pr_count: int = 0
    overdue_pr_count: int = 0
    total_review_effort_hours: float = 0.0
    active_pull_requests: list["ActivePullRequestEstimate"]

    # --- Fetch provenance ---
    open_pr_total_available: int | None = None
    open_prs_truncated: bool = False
    open_pr_limit: int = 100
    file_details_available: bool = False
    rate_limit_remaining: int | None = None
    rate_limit_limit: int | None = None
    rate_limit_reset_at: str | None = None


class ActivePullRequestEstimate(BaseModel):
    number: int
    title: str
    author: str
    html_url: str
    created_at: str
    age_hours: float

    # --- Size ---
    additions: int
    deletions: int
    changed_files: int
    reviewable_additions: int = 0
    reviewable_deletions: int = 0
    generated_file_count: int = 0

    requested_reviewers: int
    is_draft: bool

    # --- Active reviewer time needed ---
    review_effort_hours: float
    effort_band: str

    # --- Calendar-time forecast ---
    size_bucket: str = "medium"
    baseline_merge_hours: float = 0.0
    baseline_source: str = ""
    estimated_total_merge_hours: float
    estimated_remaining_hours: float
    is_overdue: bool = False
    overdue_by_hours: float = 0.0
    projected_merge_at: str | None = None
    risk_band: str
