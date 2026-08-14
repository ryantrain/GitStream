from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes.active_prs import router as active_prs_router
from app.api.routes.estimates import router as estimates_router
from app.api.routes.health import router as health_router
from app.api.routes.ingestion import router as ingestion_router
from app.api.routes.insights import router as insights_router
from app.api.routes.predictions import router as predictions_router
from app.api.routes.repos import router as repos_router
from app.api.routes.web import router as web_router
from app.api.routes.webhooks import router as webhooks_router
from app.core.config import settings

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(health_router, prefix=settings.api_prefix, tags=["health"])
app.include_router(ingestion_router, prefix=settings.api_prefix, tags=["ingestion"])
app.include_router(predictions_router, prefix=settings.api_prefix, tags=["predictions"])
app.include_router(insights_router, prefix=settings.api_prefix, tags=["insights"])
app.include_router(estimates_router, prefix=settings.api_prefix, tags=["estimates"])
app.include_router(active_prs_router, prefix=settings.api_prefix, tags=["active-prs"])
app.include_router(repos_router, prefix=settings.api_prefix, tags=["repositories"])
app.include_router(webhooks_router, prefix=settings.api_prefix, tags=["webhooks"])
app.include_router(web_router, tags=["web"])
