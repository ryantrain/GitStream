from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes.estimates import router as estimates_router
from app.api.routes.health import router as health_router
from app.api.routes.ingestion import router as ingestion_router
from app.api.routes.insights import router as insights_router
from app.api.routes.metrics import router as metrics_router
from app.api.routes.predictions import router as predictions_router
from app.api.routes.queue import router as queue_router
from app.api.routes.review_cycles import router as review_cycles_router
from app.api.routes.team_metrics import router as team_metrics_router
from app.api.routes.web import router as web_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.middleware import ObservabilityMiddleware

# Initialize structured logging on startup.
configure_logging()

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Observability middleware (request logging + Prometheus metrics)
app.add_middleware(ObservabilityMiddleware)

app.include_router(health_router, prefix=settings.api_prefix, tags=["health"])
app.include_router(ingestion_router, prefix=settings.api_prefix, tags=["ingestion"])
app.include_router(predictions_router, prefix=settings.api_prefix, tags=["predictions"])
app.include_router(insights_router, prefix=settings.api_prefix, tags=["insights"])
app.include_router(estimates_router, prefix=settings.api_prefix, tags=["estimates"])
app.include_router(queue_router, prefix=settings.api_prefix, tags=["queue"])
app.include_router(review_cycles_router, prefix=settings.api_prefix, tags=["review-cycles"])
app.include_router(team_metrics_router, prefix=settings.api_prefix, tags=["team-metrics"])
app.include_router(metrics_router, prefix=settings.api_prefix, tags=["metrics"])
app.include_router(web_router, tags=["web"])
