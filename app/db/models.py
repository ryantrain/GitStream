from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PullRequestMetric(Base):
    """Core fact table for PR data — one row per PR event ingested."""

    __tablename__ = "pull_request_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    pr_id: Mapped[str] = mapped_column(String(128), index=True)
    repository: Mapped[str] = mapped_column(String(255), index=True)
    author_id: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    # --- Size metrics ---
    lines_added: Mapped[int] = mapped_column(Integer)
    lines_deleted: Mapped[int] = mapped_column(Integer)
    files_changed: Mapped[int] = mapped_column(Integer)

    # --- Review metrics ---
    reviewers_requested: Mapped[int] = mapped_column(Integer)
    time_to_first_review_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_rounds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewer_response_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    approval_to_merge_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_cross_timezone: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- CI/Pipeline metrics ---
    ci_pass_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_duration_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_reruns: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Author behavior metrics ---
    commit_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    force_push_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hours_since_last_push: Mapped[float | None] = mapped_column(Float, nullable=True)
    author_open_pr_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- PR composition metrics ---
    test_lines_added: Mapped[int | None] = mapped_column(Integer, nullable=True)
    directories_touched: Mapped[int | None] = mapped_column(Integer, nullable=True)
    touches_critical_path: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # --- Labels (JSON-serialized list) ---
    labels: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Outcome ---
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_merge_hours: Mapped[float | None] = mapped_column(Float, nullable=True)


class PredictionLog(Base):
    """Records every prediction for accuracy tracking and drift detection."""

    __tablename__ = "prediction_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    pr_id: Mapped[str] = mapped_column(String(128), index=True)
    predicted_merge_hours: Mapped[float] = mapped_column(Float)
    risk_band: Mapped[str] = mapped_column(String(32))
    top_factors: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ReviewCycleMetric(Base):
    """Tracks individual review cycles within a PR for granular review analytics."""

    __tablename__ = "review_cycle_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    pr_id: Mapped[str] = mapped_column(String(128), index=True)
    repository: Mapped[str] = mapped_column(String(255), index=True)
    cycle_number: Mapped[int] = mapped_column(Integer)
    reviewer_id: Mapped[str] = mapped_column(String(128), index=True)
    review_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_state: Mapped[str | None] = mapped_column(String(32), nullable=True)  # approved, changes_requested, commented
    wait_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TeamMetric(Base):
    """Aggregated team-level metrics computed periodically (e.g., daily/weekly)."""

    __tablename__ = "team_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    repository: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # --- Throughput ---
    prs_merged: Mapped[int] = mapped_column(Integer, default=0)
    prs_opened: Mapped[int] = mapped_column(Integer, default=0)
    prs_stale: Mapped[int] = mapped_column(Integer, default=0)  # open > 7 days no activity

    # --- Timing ---
    avg_merge_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    median_merge_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_time_to_first_review_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_review_rounds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- Queue ---
    avg_queue_depth: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_queue_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Quality ---
    avg_ci_pass_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_pr_size_lines: Mapped[float | None] = mapped_column(Float, nullable=True)
