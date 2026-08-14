"""FastAPI middleware for request logging and metrics collection."""

import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger
from app.api.routes.metrics import HTTP_REQUESTS_TOTAL, HTTP_REQUEST_DURATION

logger = get_logger(__name__)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Middleware that logs requests and records Prometheus metrics."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.perf_counter()
        method = request.method
        path = request.url.path

        # Bind request context for structured logging
        tenant_id = request.headers.get("x-tenant-id", "unknown")

        try:
            response = await call_next(request)
            duration = time.perf_counter() - start_time

            # Record metrics
            status_code = str(response.status_code)
            HTTP_REQUESTS_TOTAL.labels(method=method, path=path, status_code=status_code).inc()
            HTTP_REQUEST_DURATION.labels(method=method, path=path).observe(duration)

            # Log request (skip health checks and metrics to reduce noise)
            if path not in ("/api/v1/health", "/api/v1/metrics"):
                logger.info(
                    "request_completed",
                    method=method,
                    path=path,
                    status_code=response.status_code,
                    duration_ms=round(duration * 1000, 2),
                    tenant_id=tenant_id,
                )

            return response

        except Exception as exc:
            duration = time.perf_counter() - start_time
            HTTP_REQUESTS_TOTAL.labels(method=method, path=path, status_code="500").inc()
            HTTP_REQUEST_DURATION.labels(method=method, path=path).observe(duration)

            logger.error(
                "request_failed",
                method=method,
                path=path,
                duration_ms=round(duration * 1000, 2),
                tenant_id=tenant_id,
                error=str(exc),
            )
            raise
