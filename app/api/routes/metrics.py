"""Prometheus metrics endpoint for observability."""

from fastapi import APIRouter, Response
from prometheus_client import (
    Counter,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

router = APIRouter()

# --- Application metrics ---

PREDICTIONS_TOTAL = Counter(
    "gitstream_predictions_total",
    "Total number of merge-time predictions made",
    ["tenant_id", "risk_band"],
)

PREDICTION_LATENCY = Histogram(
    "gitstream_prediction_latency_seconds",
    "Time spent computing a prediction",
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)

INGESTION_EVENTS_TOTAL = Counter(
    "gitstream_ingestion_events_total",
    "Total PR events ingested",
    ["tenant_id"],
)

INGESTION_ERRORS_TOTAL = Counter(
    "gitstream_ingestion_errors_total",
    "Total ingestion errors",
    ["tenant_id", "error_type"],
)

HTTP_REQUESTS_TOTAL = Counter(
    "gitstream_http_requests_total",
    "Total HTTP requests by method and path",
    ["method", "path", "status_code"],
)

HTTP_REQUEST_DURATION = Histogram(
    "gitstream_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    """Expose Prometheus metrics in the standard text format."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
