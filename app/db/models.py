from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PullRequestMetric(Base):
    __tablename__ = "pull_request_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    pr_id: Mapped[str] = mapped_column(String(128), index=True)
    repository: Mapped[str] = mapped_column(String(255), index=True)
    author_id: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lines_added: Mapped[int] = mapped_column(Integer)
    lines_deleted: Mapped[int] = mapped_column(Integer)
    files_changed: Mapped[int] = mapped_column(Integer)
    reviewers_requested: Mapped[int] = mapped_column(Integer)
    observed_merge_hours: Mapped[float | None] = mapped_column(Float, nullable=True)


class PredictionLog(Base):
    __tablename__ = "prediction_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    pr_id: Mapped[str] = mapped_column(String(128), index=True)
    predicted_merge_hours: Mapped[float] = mapped_column(Float)
    risk_band: Mapped[str] = mapped_column(String(32))
    top_factors: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
