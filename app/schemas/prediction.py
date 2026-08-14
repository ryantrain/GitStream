from datetime import datetime

from pydantic import BaseModel, Field


class PullRequestEvent(BaseModel):
    pr_id: str
    repository: str
    author_id: str
    created_at: datetime
    lines_added: int = Field(ge=0)
    lines_deleted: int = Field(ge=0)
    files_changed: int = Field(ge=1)
    reviewers_requested: int = Field(ge=0)
    labels: list[str] = Field(default_factory=list)


class IngestionAccepted(BaseModel):
    status: str
    tenant_id: str


class PredictionRequest(BaseModel):
    pr_id: str
    repository: str
    author_id: str
    lines_added: int = Field(ge=0)
    lines_deleted: int = Field(ge=0)
    files_changed: int = Field(ge=1)
    reviewers_requested: int = Field(ge=0)
    avg_author_merge_hours: float = Field(default=24.0, ge=0)
    reviewer_load_index: float = Field(default=1.0, ge=0)


class PredictionResponse(BaseModel):
    tenant_id: str
    pr_id: str
    predicted_merge_hours: float
    risk_band: str
    top_factors: list[str]


class BottleneckInsight(BaseModel):
    factor: str
    impact_hours: float
    recommendation: str
