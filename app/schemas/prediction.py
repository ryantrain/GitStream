from datetime import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Ingestion schemas
# ---------------------------------------------------------------------------


class PullRequestEvent(BaseModel):
    """Inbound PR event — accepted from webhooks or API callers."""

    pr_id: str
    repository: str
    author_id: str
    created_at: datetime
    lines_added: int = Field(ge=0)
    lines_deleted: int = Field(ge=0)
    files_changed: int = Field(ge=1)
    reviewers_requested: int = Field(ge=0)
    labels: list[str] = Field(default_factory=list)

    # --- Review metrics (optional, populated when available) ---
    time_to_first_review_hours: float | None = None
    review_rounds: int | None = Field(default=None, ge=0)
    reviewer_response_hours: float | None = None
    approval_to_merge_hours: float | None = None
    is_cross_timezone: bool | None = None
    comment_count: int | None = Field(default=None, ge=0)

    # --- CI/Pipeline metrics ---
    ci_pass_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    ci_duration_minutes: float | None = Field(default=None, ge=0)
    ci_reruns: int | None = Field(default=None, ge=0)

    # --- Author behavior metrics ---
    commit_count: int | None = Field(default=None, ge=1)
    force_push_count: int | None = Field(default=None, ge=0)
    hours_since_last_push: float | None = Field(default=None, ge=0)
    author_open_pr_count: int | None = Field(default=None, ge=0)

    # --- PR composition metrics ---
    test_lines_added: int | None = Field(default=None, ge=0)
    directories_touched: int | None = Field(default=None, ge=1)
    touches_critical_path: bool | None = None

    # --- Merge outcome (set when event is a merge/close) ---
    merged_at: datetime | None = None


class MergeEvent(BaseModel):
    """Event fired when a PR is merged — used to backfill observed_merge_hours."""

    pr_id: str
    repository: str
    merged_at: datetime
    created_at: datetime | None = None  # If not provided, looked up from DB


class ReviewCycleEvent(BaseModel):
    """Individual review cycle data for granular review analytics."""

    pr_id: str
    repository: str
    cycle_number: int = Field(ge=1)
    reviewer_id: str
    review_requested_at: datetime | None = None
    review_submitted_at: datetime | None = None
    review_state: str | None = None  # approved, changes_requested, commented


class IngestionAccepted(BaseModel):
    status: str
    tenant_id: str


# ---------------------------------------------------------------------------
# Prediction schemas
# ---------------------------------------------------------------------------


class PredictionRequest(BaseModel):
    """Input for time-to-merge prediction."""

    pr_id: str
    repository: str
    author_id: str
    lines_added: int = Field(ge=0)
    lines_deleted: int = Field(ge=0)
    files_changed: int = Field(ge=1)
    reviewers_requested: int = Field(ge=0)

    # --- Contextual features (auto-computed if not supplied) ---
    avg_author_merge_hours: float | None = Field(default=None, ge=0)
    reviewer_load_index: float | None = Field(default=None, ge=0)

    # --- New optional signals for improved prediction ---
    time_to_first_review_hours: float | None = None
    review_rounds: int | None = Field(default=None, ge=0)
    ci_pass_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    ci_duration_minutes: float | None = Field(default=None, ge=0)
    ci_reruns: int | None = Field(default=None, ge=0)
    commit_count: int | None = Field(default=None, ge=1)
    force_push_count: int | None = Field(default=None, ge=0)
    author_open_pr_count: int | None = Field(default=None, ge=0)
    test_lines_added: int | None = Field(default=None, ge=0)
    directories_touched: int | None = Field(default=None, ge=1)
    touches_critical_path: bool | None = None
    labels: list[str] = Field(default_factory=list)
    comment_count: int | None = Field(default=None, ge=0)
    is_cross_timezone: bool | None = None


class PredictionResponse(BaseModel):
    """Output from time-to-merge prediction."""

    tenant_id: str
    pr_id: str
    predicted_merge_hours: float
    risk_band: str
    top_factors: list["PredictionFactor"]
    confidence_score: float | None = None
    prediction_method: str = Field(
        default="heuristic",
        description="Which model produced predicted_merge_hours: 'ml_model' or 'heuristic'.",
    )
    attribution_method: str = Field(
        default="heuristic",
        description=(
            "How top_factors were derived. 'heuristic' means they decompose the "
            "returned prediction exactly. 'heuristic_proxy' means an ML model "
            "produced the prediction and the factors indicate direction and "
            "relative weight only — their hours will not sum to the prediction."
        ),
    )
    risk_thresholds_source: str = Field(
        default="absolute_default",
        description=(
            "'tenant_history' when risk bands were cut from this tenant's own "
            "merge-time distribution, 'absolute_default' when history was too "
            "thin and fixed thresholds were used."
        ),
    )


class PredictionFactor(BaseModel):
    """A single factor contributing to the prediction, ranked by impact."""

    factor: str
    contribution_hours: float
    direction: str  # "increases" or "decreases"


# ---------------------------------------------------------------------------
# Insight schemas
# ---------------------------------------------------------------------------


class BottleneckInsight(BaseModel):
    """A factor that measurably delays merges, with its attributable impact.

    ``impact_hours`` is always the *additional hours attributable to this
    factor* — a median difference between the affected group and its comparison
    group. Keeping every insight in that one unit is what makes sorting by
    impact meaningful.
    """

    factor: str
    impact_hours: float = Field(description="Additional hours attributable to this factor (a group delta).")
    recommendation: str
    category: str = "general"  # review, ci, author, composition, queue
    baseline_hours: float | None = Field(
        default=None,
        description="The comparison group's median, for context on the delta.",
    )
    observations: int = Field(default=0, description="How many PRs or reviews back this insight.")
    is_measured: bool = Field(
        default=True,
        description="False for generic guidance shown when history is insufficient.",
    )


# ---------------------------------------------------------------------------
# Team / Queue schemas
# ---------------------------------------------------------------------------


class TeamMetricsSummary(BaseModel):
    """Aggregated team-level metrics for a time period.

    ``prs_opened`` is keyed on PR creation time; ``prs_merged`` is keyed on
    merge time, so the two are independent counts rather than a subset
    relationship. Duration statistics describe only the PRs merged inside the
    window, and ``merged_sample_size`` reports how many observations back them.
    """

    tenant_id: str
    repository: str | None = None
    period_start: datetime
    period_end: datetime
    prs_merged: int
    prs_opened: int
    prs_stale: int
    merged_sample_size: int = 0
    avg_merge_hours: float | None
    median_merge_hours: float | None
    p75_merge_hours: float | None = None
    p90_merge_hours: float | None = None
    avg_time_to_first_review_hours: float | None
    avg_review_rounds: float | None
    current_queue_depth: int = 0
    avg_ci_pass_rate: float | None
    avg_pr_size_lines: float | None


class QueueDepthResponse(BaseModel):
    """Current PR queue depth per reviewer."""

    tenant_id: str
    repository: str | None = None
    total_open_prs: int
    reviewer_queues: list["ReviewerQueue"]


class ReviewerQueue(BaseModel):
    reviewer_id: str
    open_review_count: int
    avg_wait_hours: float | None
    oldest_review_hours: float | None


class ReviewCycleSummary(BaseModel):
    """Summary of review cycles for a PR or across PRs."""

    pr_id: str | None = None
    total_cycles: int
    avg_wait_hours: float | None
    max_wait_hours: float | None
    cycles: list["ReviewCycleDetail"]


class ReviewCycleDetail(BaseModel):
    cycle_number: int
    reviewer_id: str
    review_state: str | None
    wait_hours: float | None
    review_requested_at: datetime | None
    review_submitted_at: datetime | None
