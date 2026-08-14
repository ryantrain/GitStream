"""Background task worker using Arq (async Redis queue).

Start with: arq app.tasks.worker.WorkerSettings
"""

from arq import cron
from arq.connections import RedisSettings

from app.services.ingestion_worker import async_process_pull_request_event


async def startup(ctx: dict) -> None:
    """Called when the worker starts. Initialize shared resources here."""
    pass


async def shutdown(ctx: dict) -> None:
    """Called when the worker stops. Clean up shared resources here."""
    pass


class WorkerSettings:
    """Arq worker configuration."""

    functions = [async_process_pull_request_event]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings(host="localhost", port=6379)
    max_jobs = 10
    job_timeout = 300  # 5 minutes
