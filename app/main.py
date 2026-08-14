from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.ingestion import router as ingestion_router
from app.api.routes.insights import router as insights_router
from app.api.routes.predictions import router as predictions_router
from app.core.config import settings

app = FastAPI(title=settings.app_name)

app.include_router(health_router, prefix=settings.api_prefix, tags=["health"])
app.include_router(ingestion_router, prefix=settings.api_prefix, tags=["ingestion"])
app.include_router(predictions_router, prefix=settings.api_prefix, tags=["predictions"])
app.include_router(insights_router, prefix=settings.api_prefix, tags=["insights"])
