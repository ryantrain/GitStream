-- GitStream initial schema with row-level security.

CREATE TABLE IF NOT EXISTS pull_request_metrics (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  pr_id TEXT NOT NULL,
  repository TEXT NOT NULL,
  author_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  lines_added INTEGER NOT NULL DEFAULT 0,
  lines_deleted INTEGER NOT NULL DEFAULT 0,
  files_changed INTEGER NOT NULL DEFAULT 1,
  reviewers_requested INTEGER NOT NULL DEFAULT 0,
  observed_merge_hours DOUBLE PRECISION,
  UNIQUE (tenant_id, pr_id)
);

CREATE TABLE IF NOT EXISTS prediction_logs (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  pr_id TEXT NOT NULL,
  predicted_merge_hours DOUBLE PRECISION NOT NULL,
  risk_band TEXT NOT NULL,
  top_factors TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pr_metrics_tenant_created
  ON pull_request_metrics (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_prediction_logs_tenant_created
  ON prediction_logs (tenant_id, created_at DESC);

ALTER TABLE pull_request_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE prediction_logs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS pr_metrics_tenant_isolation ON pull_request_metrics;
CREATE POLICY pr_metrics_tenant_isolation
  ON pull_request_metrics
  USING (tenant_id = current_setting('app.current_tenant', true));

DROP POLICY IF EXISTS prediction_logs_tenant_isolation ON prediction_logs;
CREATE POLICY prediction_logs_tenant_isolation
  ON prediction_logs
  USING (tenant_id = current_setting('app.current_tenant', true));
