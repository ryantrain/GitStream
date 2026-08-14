"""Initial schema: pull_request_metrics and prediction_logs with RLS.

Revision ID: 001
Revises: None
Create Date: 2026-08-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- pull_request_metrics ---
    op.create_table(
        "pull_request_metrics",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("pr_id", sa.String(128), nullable=False, index=True),
        sa.Column("repository", sa.String(255), nullable=False, index=True),
        sa.Column("author_id", sa.String(128), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("lines_added", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lines_deleted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("files_changed", sa.Integer, nullable=False, server_default="1"),
        sa.Column("reviewers_requested", sa.Integer, nullable=False, server_default="0"),
        sa.Column("observed_merge_hours", sa.Float, nullable=True),
        sa.UniqueConstraint("tenant_id", "pr_id", name="uq_pr_metrics_tenant_pr"),
    )

    # --- prediction_logs ---
    op.create_table(
        "prediction_logs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("pr_id", sa.String(128), nullable=False, index=True),
        sa.Column("predicted_merge_hours", sa.Float, nullable=False),
        sa.Column("risk_band", sa.String(32), nullable=False),
        sa.Column("top_factors", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )

    op.create_index(
        "idx_pr_metrics_tenant_created",
        "pull_request_metrics",
        ["tenant_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_prediction_logs_tenant_created",
        "prediction_logs",
        ["tenant_id", sa.text("created_at DESC")],
    )

    # --- Row-Level Security ---
    op.execute("ALTER TABLE pull_request_metrics ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE prediction_logs ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY pr_metrics_tenant_isolation
        ON pull_request_metrics
        USING (tenant_id = current_setting('app.current_tenant', true))
    """)

    op.execute("""
        CREATE POLICY prediction_logs_tenant_isolation
        ON prediction_logs
        USING (tenant_id = current_setting('app.current_tenant', true))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS prediction_logs_tenant_isolation ON prediction_logs")
    op.execute("DROP POLICY IF EXISTS pr_metrics_tenant_isolation ON pull_request_metrics")
    op.drop_table("prediction_logs")
    op.drop_table("pull_request_metrics")
