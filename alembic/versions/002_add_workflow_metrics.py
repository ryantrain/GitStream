"""Add comprehensive PR workflow metrics, review cycle tracking, and team metrics.

Revision ID: 002
Revises: 001
Create Date: 2026-08-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- New columns on pull_request_metrics ---

    # Review metrics
    op.add_column("pull_request_metrics", sa.Column("time_to_first_review_hours", sa.Float, nullable=True))
    op.add_column("pull_request_metrics", sa.Column("review_rounds", sa.Integer, nullable=True))
    op.add_column("pull_request_metrics", sa.Column("reviewer_response_hours", sa.Float, nullable=True))
    op.add_column("pull_request_metrics", sa.Column("approval_to_merge_hours", sa.Float, nullable=True))
    op.add_column("pull_request_metrics", sa.Column("is_cross_timezone", sa.Boolean, nullable=True))
    op.add_column("pull_request_metrics", sa.Column("comment_count", sa.Integer, nullable=True))

    # CI/Pipeline metrics
    op.add_column("pull_request_metrics", sa.Column("ci_pass_rate", sa.Float, nullable=True))
    op.add_column("pull_request_metrics", sa.Column("ci_duration_minutes", sa.Float, nullable=True))
    op.add_column("pull_request_metrics", sa.Column("ci_reruns", sa.Integer, nullable=True))

    # Author behavior metrics
    op.add_column("pull_request_metrics", sa.Column("commit_count", sa.Integer, nullable=True))
    op.add_column("pull_request_metrics", sa.Column("force_push_count", sa.Integer, nullable=True))
    op.add_column("pull_request_metrics", sa.Column("hours_since_last_push", sa.Float, nullable=True))
    op.add_column("pull_request_metrics", sa.Column("author_open_pr_count", sa.Integer, nullable=True))

    # PR composition metrics
    op.add_column("pull_request_metrics", sa.Column("test_lines_added", sa.Integer, nullable=True))
    op.add_column("pull_request_metrics", sa.Column("directories_touched", sa.Integer, nullable=True))
    op.add_column("pull_request_metrics", sa.Column("touches_critical_path", sa.Boolean, nullable=True))

    # Labels (JSON-serialized list)
    op.add_column("pull_request_metrics", sa.Column("labels", sa.Text, nullable=True))

    # Merge outcome timestamp
    op.add_column("pull_request_metrics", sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True))

    # --- review_cycle_metrics table ---
    op.create_table(
        "review_cycle_metrics",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("pr_id", sa.String(128), nullable=False, index=True),
        sa.Column("repository", sa.String(255), nullable=False, index=True),
        sa.Column("cycle_number", sa.Integer, nullable=False),
        sa.Column("reviewer_id", sa.String(128), nullable=False, index=True),
        sa.Column("review_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_state", sa.String(32), nullable=True),
        sa.Column("wait_hours", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )

    op.create_index(
        "idx_review_cycles_tenant_pr",
        "review_cycle_metrics",
        ["tenant_id", "pr_id"],
    )
    op.create_index(
        "idx_review_cycles_reviewer",
        "review_cycle_metrics",
        ["tenant_id", "reviewer_id"],
    )

    # --- team_metrics table ---
    op.create_table(
        "team_metrics",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("repository", sa.String(255), nullable=True, index=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prs_merged", sa.Integer, nullable=False, server_default="0"),
        sa.Column("prs_opened", sa.Integer, nullable=False, server_default="0"),
        sa.Column("prs_stale", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_merge_hours", sa.Float, nullable=True),
        sa.Column("median_merge_hours", sa.Float, nullable=True),
        sa.Column("avg_time_to_first_review_hours", sa.Float, nullable=True),
        sa.Column("avg_review_rounds", sa.Float, nullable=True),
        sa.Column("avg_queue_depth", sa.Float, nullable=True),
        sa.Column("max_queue_depth", sa.Integer, nullable=True),
        sa.Column("avg_ci_pass_rate", sa.Float, nullable=True),
        sa.Column("avg_pr_size_lines", sa.Float, nullable=True),
    )

    op.create_index(
        "idx_team_metrics_tenant_period",
        "team_metrics",
        ["tenant_id", "period_start", "period_end"],
    )

    # --- Row-Level Security for new tables ---
    op.execute("ALTER TABLE review_cycle_metrics ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE team_metrics ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY review_cycles_tenant_isolation
        ON review_cycle_metrics
        USING (tenant_id = current_setting('app.current_tenant', true))
    """)

    op.execute("""
        CREATE POLICY team_metrics_tenant_isolation
        ON team_metrics
        USING (tenant_id = current_setting('app.current_tenant', true))
    """)


def downgrade() -> None:
    # Drop RLS policies
    op.execute("DROP POLICY IF EXISTS team_metrics_tenant_isolation ON team_metrics")
    op.execute("DROP POLICY IF EXISTS review_cycles_tenant_isolation ON review_cycle_metrics")

    # Drop new tables
    op.drop_table("team_metrics")
    op.drop_table("review_cycle_metrics")

    # Drop new columns from pull_request_metrics
    op.drop_column("pull_request_metrics", "merged_at")
    op.drop_column("pull_request_metrics", "labels")
    op.drop_column("pull_request_metrics", "touches_critical_path")
    op.drop_column("pull_request_metrics", "directories_touched")
    op.drop_column("pull_request_metrics", "test_lines_added")
    op.drop_column("pull_request_metrics", "author_open_pr_count")
    op.drop_column("pull_request_metrics", "hours_since_last_push")
    op.drop_column("pull_request_metrics", "force_push_count")
    op.drop_column("pull_request_metrics", "commit_count")
    op.drop_column("pull_request_metrics", "ci_reruns")
    op.drop_column("pull_request_metrics", "ci_duration_minutes")
    op.drop_column("pull_request_metrics", "ci_pass_rate")
    op.drop_column("pull_request_metrics", "comment_count")
    op.drop_column("pull_request_metrics", "is_cross_timezone")
    op.drop_column("pull_request_metrics", "approval_to_merge_hours")
    op.drop_column("pull_request_metrics", "reviewer_response_hours")
    op.drop_column("pull_request_metrics", "review_rounds")
    op.drop_column("pull_request_metrics", "time_to_first_review_hours")
