# GitStream

GitStream is an engineering workflow intelligence platform that predicts pull-request delivery delays and surfaces operational bottlenecks.

## MVP Scope

- FastAPI backend for ingestion, predictions, and insights
- PostgreSQL schema with row-level security (RLS)
- Feature engineering pipeline from pull-request events
- Baseline ML predictor interface (ready for XGBoost/LightGBM)

## Architecture

- API Layer: FastAPI endpoints for ingestion, prediction, and bottleneck insights
- Data Layer: PostgreSQL tables scoped by tenant_id and protected by RLS
- Service Layer: Feature extraction, prediction service, and ingestion workflow
- Security: Tenant identity propagated to DB session via app.current_tenant setting

## Quick Start

1. Create a virtual environment.
2. Install dependencies:

   ```bash
   pip install -e .[dev]
   ```

3. Copy environment file:

   ```bash
   cp .env.example .env
   ```

4. Start PostgreSQL (optional, for local DB):

   ```bash
   docker compose up -d
   ```

5. Run API:

   ```bash
   uvicorn app.main:app --reload
   ```

6. Open docs:

   - http://127.0.0.1:8000/docs

## Next Build Steps

- Integrate GitHub webhook ingestion and background queue
- Replace baseline predictor with trained LightGBM or XGBoost model
- Add auth integration (JWT from Supabase/Auth0/Okta)
- Add dashboards (React or Streamlit frontend)
- Add CI pipeline and migration tool (Alembic)
